"""Dependency-direction checks without an external architecture linter.

The rules are the ones fixed in ``docs/architecture/architecture_overview.md``. They are
verified by reading the import graph, which is enough for a skeleton of this size and
costs nothing to maintain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
SRC = SRC_ROOT / "research_platform"

DOMAIN_FORBIDDEN_PREFIXES = (
    "research_platform.application",
    "research_platform.platform",
    "research_platform.agents",
    "research_platform.config",
    "research_platform.cli",
    "langchain",
    "langchain_core",
    "langgraph",
    "langsmith",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "pgvector",
    "docling",
    "pymupdf",
    "openai",
    "anthropic",
)

APPLICATION_FORBIDDEN_PREFIXES = (
    "research_platform.platform",
    "research_platform.agents",
    "research_platform.cli",
    "langchain",
    "langgraph",
    "sqlalchemy",
    "docling",
    "openai",
    "anthropic",
)
"""Modules the application layer must not import.

``research_platform.config`` is deliberately allowed: it holds the typed settings contract
(pure Pydantic models plus a YAML reader that performs no connection and holds no secrets),
and the configuration service exists precisely to read it.  Physical paths, adapters and
graph runtimes stay forbidden.
"""


def module_name(path: Path, src_root: Path) -> str:
    """Absolute module name of a source file under ``src_root``."""
    relative = path.resolve().relative_to(src_root.resolve()).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_modules(path: Path, src_root: Path = SRC_ROOT) -> set[str]:
    """Absolute module names imported by a source file.

    Relative imports are resolved against the importing package, so
    ``from ...platform import x`` inside ``agents`` is caught just like
    ``from research_platform.platform import x``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = module_name(path, src_root).split(".")[:-1]
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    modules.add(node.module)
                continue
            base = package_parts[: len(package_parts) - (node.level - 1)]
            resolved = ".".join([*base, node.module]) if node.module else ".".join(base)
            modules.add(resolved)
    return modules


def test_relative_imports_are_resolved() -> None:
    """Guard the guard: a relative import must resolve to an absolute module name."""
    assert module_name(SRC / "domain" / "base.py", SRC_ROOT) == ("research_platform.domain.base")
    assert module_name(SRC / "domain" / "__init__.py", SRC_ROOT) == "research_platform.domain"


def test_relative_import_of_platform_is_detected(tmp_path: Path) -> None:
    """A relative import cannot sneak past the boundary rule."""
    src_root = tmp_path / "src"
    package = src_root / "research_platform" / "agents" / "agent_1x"
    package.mkdir(parents=True)
    for init in (
        src_root / "research_platform" / "__init__.py",
        src_root / "research_platform" / "agents" / "__init__.py",
        package / "__init__.py",
    ):
        init.write_text("", encoding="utf-8")
    module = package / "nodes.py"
    module.write_text("from ...platform import adapter\nfrom . import local\n", encoding="utf-8")

    names = imported_modules(module, src_root)
    assert "research_platform.platform" in names
    assert "research_platform.agents.agent_1x" in names


def python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def test_domain_does_not_import_infrastructure() -> None:
    violations: list[str] = []
    for path in python_files(SRC / "domain"):
        for module in imported_modules(path):
            for forbidden in DOMAIN_FORBIDDEN_PREFIXES:
                if module == forbidden or module.startswith(f"{forbidden}."):
                    violations.append(f"{path.name}: {module}")
    assert not violations, "domain must stay infrastructure-free: " + "; ".join(violations)


def test_domain_has_no_filesystem_knowledge() -> None:
    """The domain layer must not touch the file system or the environment."""
    violations: list[str] = []
    for path in python_files(SRC / "domain"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name in {"pathlib", "os", "shutil", "tempfile"} for alias in node.names
                ):
                    violations.append(f"{path.name}: imports {node.names[0].name}")
            elif isinstance(node, ast.ImportFrom) and node.module in {
                "pathlib",
                "os",
                "shutil",
                "tempfile",
            }:
                violations.append(f"{path.name}: from {node.module} import ...")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                violations.append(f"{path.name}: calls open()")
    assert not violations, "; ".join(violations)


def test_application_does_not_import_infrastructure() -> None:
    violations: list[str] = []
    for path in python_files(SRC / "application"):
        for module in imported_modules(path):
            for forbidden in APPLICATION_FORBIDDEN_PREFIXES:
                if module == forbidden or module.startswith(f"{forbidden}."):
                    violations.append(f"{path.name}: {module}")
    assert not violations, "; ".join(violations)


@pytest.mark.parametrize(
    ("owner", "forbidden"),
    [("agent_1a", "agent_1b"), ("agent_1b", "agent_1a")],
)
def test_agents_do_not_import_each_other(owner: str, forbidden: str) -> None:
    prefix = f"research_platform.agents.{forbidden}"
    violations = [
        f"{path.name}: {module}"
        for path in python_files(SRC / "agents" / owner)
        for module in imported_modules(path)
        if module == prefix or module.startswith(f"{prefix}.")
    ]
    assert not violations, f"{owner} must not import {forbidden}: " + "; ".join(violations)


def test_agents_use_shared_contracts_not_platform_internals() -> None:
    """Agents depend on domain contracts and application ports, not on adapters."""
    violations: list[str] = []
    for path in python_files(SRC / "agents"):
        for module in imported_modules(path):
            if module.startswith("research_platform.platform"):
                violations.append(f"{path.name}: {module}")
    assert not violations, "; ".join(violations)


def test_cli_is_the_only_entry_point_module() -> None:
    entry_points = [path.name for path in python_files(SRC) if path.name == "cli.py"]
    assert entry_points == ["cli.py"]
