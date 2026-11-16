"""CLI contract: help, version, ``contracts validate`` and version dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fixtures.builders import valid_package, valid_package_v20

from research_platform import __version__
from research_platform.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_CONTRACT_VIOLATION,
    EXIT_CONTROLLED_STOP,
    EXIT_INFRASTRUCTURE_ERROR,
    EXIT_INPUT_ERROR,
    EXIT_OK,
    PACKAGE_VALIDATORS,
    build_parser,
    main,
)


def write_json(tmp_path: Path, payload: object, name: str = "package.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_help_lists_available_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "contracts" in output
    assert "validate" in output


def test_version_prints_platform_python_and_package(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == EXIT_OK
    output = capsys.readouterr().out
    assert __version__ in output
    assert "research-agent-platform" in output
    assert "python" in output.lower()
    assert "langgraph" in output


def test_version_as_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "research-agent-platform"
    assert payload["version"] == __version__
    assert payload["python"].count(".") == 2


def test_validator_knows_both_package_versions() -> None:
    assert sorted(PACKAGE_VALIDATORS) == ["2.0", "2.1"]


def test_documented_commands_exist() -> None:
    parser = build_parser()
    subcommands = {
        action.dest: sorted(action.choices)  # type: ignore[union-attr]
        for action in parser._actions
        if getattr(action, "choices", None)
    }
    assert subcommands == {"command": ["agent1a", "config", "contracts", "document", "platform"]}
    for forbidden in ("process", "run-agent", "parse", "index"):
        with pytest.raises(SystemExit):
            main([forbidden])


def test_exit_codes_are_documented_and_distinct() -> None:
    codes = {
        EXIT_OK,
        EXIT_CONTRACT_VIOLATION,
        EXIT_CONFIG_ERROR,
        EXIT_INFRASTRUCTURE_ERROR,
        EXIT_CONTROLLED_STOP,
    }
    assert codes == {0, 1, 2, 3, 4}
    assert EXIT_INPUT_ERROR == EXIT_CONTRACT_VIOLATION


@pytest.mark.parametrize("builder", [valid_package, valid_package_v20])
def test_valid_package_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], builder: object
) -> None:
    path = write_json(tmp_path, builder())  # type: ignore[operator]
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_OK
    assert "valid" in capsys.readouterr().out


def test_unsupported_contract_version_is_rejected_with_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = valid_package()
    payload["contract_version"] = "2.9"
    path = write_json(tmp_path, payload)
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_CONTRACT_VIOLATION
    output = capsys.readouterr().out
    assert "unsupported contract_version" in output
    assert "2.1" in output


def test_missing_contract_version_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = valid_package()
    del payload["contract_version"]
    path = write_json(tmp_path, payload)
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_CONTRACT_VIOLATION
    assert "contract_version" in capsys.readouterr().out


def test_invalid_package_returns_one_with_field_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = valid_package()
    del payload["card"]["contribution"]
    path = write_json(tmp_path, payload)
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_CONTRACT_VIOLATION
    output = capsys.readouterr().out
    assert "card.contribution" in output
    assert "contract violation" in output


def test_unknown_field_reports_its_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = valid_package()
    payload["surprise"] = 1
    path = write_json(tmp_path, payload)
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_CONTRACT_VIOLATION
    assert "surprise" in capsys.readouterr().out


def test_missing_file_returns_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"
    assert main(["contracts", "validate", "--input", str(missing)]) == EXIT_INPUT_ERROR
    assert "cannot read" in capsys.readouterr().err


def test_broken_json_returns_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"contract_version": "2.1",', encoding="utf-8")
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_INPUT_ERROR
    assert "not valid JSON" in capsys.readouterr().err


def test_non_object_json_returns_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert main(["contracts", "validate", "--input", str(path)]) == EXIT_INPUT_ERROR
    assert "JSON object" in capsys.readouterr().err


def test_contracts_without_subcommand_does_not_validate_anything() -> None:
    assert main(["contracts"]) == EXIT_INPUT_ERROR


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "usage" in capsys.readouterr().out.lower()
