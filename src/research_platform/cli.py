"""Command line interface: the single local entry point of the platform.

Commands:

* ``--version`` — platform name, application version, Python and key package versions;
* ``--help`` — the commands and what they do;
* ``contracts validate --input <path>`` — validate a versioned agent package;
* ``config validate`` — load and validate configuration (types, required values, paths,
  secret scanning);
* ``platform check`` — run the platform self-checks;
* ``agent1a workflow-smoke`` — execute the technical LangGraph skeleton.

Exit codes (documented and stable):

===  ==============================================================
0    the operation succeeded
1    execution or validation failure (contract violation, bad input)
2    configuration error
3    infrastructure unavailable or not initialised
4    controlled stop / waiting for an external action
===  ==============================================================

The CLI contains no business logic: it parses arguments, calls a service and maps the
result to an exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from research_platform import __version__
from research_platform.agents.agent_1a.contracts.package import (
    Agent1AExtractionPackageV2,
)
from research_platform.agents.agent_1a.contracts.package_v21 import (
    Agent1AExtractionPackageV21,
)
from research_platform.agents.agent_1a.graph import SmokeRunRequest
from research_platform.app import ApplicationBootstrap, ApplicationContext
from research_platform.application.services.configuration_service import ConfigurationService
from research_platform.application.services.document_errors import DocumentPipelineError
from research_platform.application.services.health_check import CheckStatus
from research_platform.composition.document_pipeline import document_dependencies
from research_platform.config import SettingsError
from research_platform.domain.documents.quality import QualityStatus
from research_platform.platform.observability.logging import (
    CONFIG_VALIDATION_FAILED,
    StructuredLogger,
)

__all__ = [
    "EXIT_CONFIG_ERROR",
    "EXIT_CONTRACT_VIOLATION",
    "EXIT_CONTROLLED_STOP",
    "EXIT_INFRASTRUCTURE_ERROR",
    "EXIT_INPUT_ERROR",
    "EXIT_OK",
    "PACKAGE_VALIDATORS",
    "build_parser",
    "main",
]

EXIT_OK: Final = 0
EXIT_CONTRACT_VIOLATION: Final = 1
EXIT_CONFIG_ERROR: Final = 2
EXIT_INFRASTRUCTURE_ERROR: Final = 3
EXIT_CONTROLLED_STOP: Final = 4

EXIT_INPUT_ERROR: Final = EXIT_CONTRACT_VIOLATION
"""Unreadable input or invalid JSON is a validation failure (exit code 1).

Iteration 0 used a dedicated code 2 for this case; code 2 now means "configuration error"
for every command, so the two meanings do not collide.
"""

PACKAGE_VALIDATORS: Final[dict[str, type[BaseModel]]] = {
    "2.0": Agent1AExtractionPackageV2,
    "2.1": Agent1AExtractionPackageV21,
}
"""Agent 1A package contract versions this validator understands."""

_CONTRACTS_PACKAGE_HINT: Final = (
    "supported Agent 1A extraction package contract versions: "
    + ", ".join(sorted(PACKAGE_VALIDATORS))
)

_PROGRAM = "research-platform"


# ------------------------------------------------------------------------- parser


def _add_json_flag(parser: argparse.ArgumentParser, *, default: object = argparse.SUPPRESS) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=default,
        help="emit machine-readable JSON instead of text",
    )


def _add_project_root(parser: argparse.ArgumentParser, *, default: object = None) -> None:
    parser.add_argument(
        "--project-root",
        metavar="PATH",
        default=default,
        help="project root used to resolve config/ and data/ (default: current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (public for tests)."""
    parser = argparse.ArgumentParser(
        prog=_PROGRAM,
        description=(
            "Multi-Agent Research Platform - platform skeleton. Runs technical self-checks "
            "and validates versioned contracts; it does not process scientific documents."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print version information and exit",
    )
    _add_project_root(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit machine-readable JSON instead of text",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # contracts validate
    contracts = subparsers.add_parser(
        "contracts",
        help="contract tooling (validate versioned agent packages)",
        description="Contract tooling for versioned inter-agent packages.",
    )
    contracts_sub = contracts.add_subparsers(dest="contracts_command", metavar="<subcommand>")
    validate = contracts_sub.add_parser(
        "validate",
        help="validate a JSON document against the Agent 1A extraction package contract",
        description=(
            "Validate a JSON document against the Agent 1A extraction package contract "
            f"({_CONTRACTS_PACKAGE_HINT})."
        ),
    )
    validate.add_argument(
        "--input", required=True, metavar="PATH", help="path to the JSON package to validate"
    )
    _add_project_root(validate, default=argparse.SUPPRESS)
    _add_json_flag(validate)

    # config validate
    config = subparsers.add_parser(
        "config",
        help="configuration tooling",
        description="Configuration tooling.",
    )
    config_sub = config.add_subparsers(dest="config_command", metavar="<subcommand>")
    config_validate = config_sub.add_parser(
        "validate",
        help="load and validate platform configuration",
        description=(
            "Load config/application.yaml and config/models.yaml, apply environment "
            "overrides, and validate types, values, paths and the absence of secrets."
        ),
    )
    config_validate.add_argument(
        "--config", metavar="PATH", default=None, help="override the application YAML path"
    )
    config_validate.add_argument(
        "--models", metavar="PATH", default=None, help="override the models YAML path"
    )
    _add_project_root(config_validate, default=argparse.SUPPRESS)
    _add_json_flag(config_validate)

    # platform check
    platform = subparsers.add_parser(
        "platform",
        help="platform self-checks",
        description="Platform self-checks (no database, no provider, no document).",
    )
    platform_sub = platform.add_subparsers(dest="platform_command", metavar="<subcommand>")
    platform_check = platform_sub.add_parser(
        "check",
        help="check runtime, configuration, directories, artifact store, model gateway",
        description=(
            "Run the platform self-checks. Each check reports PASS, WARNING, FAILED or "
            "NOT_CONFIGURED. PostgreSQL and real LLM endpoints are not contacted: they are "
            "outside this iteration's scope."
        ),
    )
    _add_project_root(platform_check, default=argparse.SUPPRESS)
    _add_json_flag(platform_check)

    # document processing
    document = subparsers.add_parser(
        "document",
        help="document ingestion, parsing and quality gate",
        description=(
            "Document pipeline: ingest a PDF or TXT, parse it, run the deterministic "
            "Quality Gate and store every artefact. No LLM is involved."
        ),
    )
    document_sub = document.add_subparsers(dest="document_command", metavar="<subcommand>")
    document_process = document_sub.add_parser(
        "process",
        help="process one document end to end",
        description=(
            "Ingest, parse and quality-check one document. Prints identifiers, parser "
            "provenance, page count and the Quality Gate verdict; the full report is "
            "available through 'document report --run-id'."
        ),
    )
    document_process.add_argument(
        "--input", required=True, metavar="PATH", help="path to the PDF or TXT to process"
    )
    document_process.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="validate the input and exit without parsing",
    )
    _add_project_root(document_process, default=argparse.SUPPRESS)
    _add_json_flag(document_process)

    document_report = document_sub.add_parser(
        "report",
        help="print the stored Quality Report of a run",
        description=(
            "Read the Quality Report artefact stored for a run. The run repository is "
            "in-memory, so the report is loaded from the artefact store, not from the run."
        ),
    )
    document_report.add_argument(
        "--run-id", required=True, metavar="RUN_ID", help="run id whose report is printed"
    )
    _add_project_root(document_report, default=argparse.SUPPRESS)
    _add_json_flag(document_report)

    # agent1a workflow-smoke
    agent1a = subparsers.add_parser(
        "agent1a",
        help="Agent 1A runtime tooling",
        description="Agent 1A runtime tooling (technical skeleton only).",
    )
    agent1a_sub = agent1a.add_subparsers(dest="agent1a_command", metavar="<subcommand>")
    smoke = agent1a_sub.add_parser(
        "workflow-smoke",
        help="run the technical LangGraph skeleton on a synthetic state",
        description=(
            "Execute the minimal Agent 1A graph (initialize_run, validate_runtime_state, "
            "execute_smoke_step, complete_run) on a synthetic state with fake adapters. "
            "No scientific document is processed and no card is produced."
        ),
    )
    smoke.add_argument(
        "--document-id",
        metavar="ID",
        default=None,
        help="synthetic document id recorded on the run (default: doc_smoke_0001)",
    )
    smoke.add_argument(
        "--note", metavar="TEXT", default=None, help="free-form operator note (logged only)"
    )
    _add_project_root(smoke, default=argparse.SUPPRESS)
    _add_json_flag(smoke)
    smoke.add_argument(
        "--no-model",
        action="store_true",
        help="run the smoke workflow without the model gateway",
    )
    return parser


# --------------------------------------------------------------------- helpers


def _format_location(loc: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        elif parts:
            parts.append(f".{item}")
        else:
            parts.append(str(item))
    return "".join(parts) or "<root>"


def _version_payload() -> dict[str, str]:
    import platform as platform_module

    payload = {
        "name": "research-agent-platform",
        "version": __version__,
        "python": platform_module.python_version(),
        "implementation": platform_module.python_implementation(),
        "platform": platform_module.platform(),
    }
    for package in ("langgraph", "langchain-core", "pydantic", "jsonschema"):
        try:
            from importlib.metadata import version as package_version

            payload[package] = package_version(package)
        except Exception:
            payload[package] = "not installed"
    return payload


def _print_version(as_json: bool) -> int:
    payload = _version_payload()
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"{payload['name']} {payload['version']}")
    print(f"python {payload['python']} ({payload['implementation']}) on {payload['platform']}")
    for package in ("langgraph", "langchain-core", "pydantic"):
        print(f"{package} {payload[package]}")
    return EXIT_OK


def _project_root(args: argparse.Namespace) -> Path:
    root = getattr(args, "project_root", None)
    return Path(root).resolve() if root else Path.cwd().resolve()


def _validation_logger(*, enabled: bool = False) -> StructuredLogger:
    return StructuredLogger(
        service_name=_PROGRAM,
        stream=sys.stderr,
        enabled=enabled,
    )


# -------------------------------------------------------------------- contracts


def validate_package_file(path: Path) -> int:
    """Validate one JSON file against the Agent 1A package contract matching its version.

    Returns:
        :data:`EXIT_OK` or :data:`EXIT_CONTRACT_VIOLATION`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"error: {path} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            file=sys.stderr,
        )
        return EXIT_CONTRACT_VIOLATION

    if not isinstance(payload, dict):
        print(f"error: {path} must contain a JSON object", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION

    declared = payload.get("contract_version")
    if not isinstance(declared, str):
        print(
            f"invalid: {path} does not declare a string 'contract_version' "
            f"({_CONTRACTS_PACKAGE_HINT})"
        )
        return EXIT_CONTRACT_VIOLATION

    model = PACKAGE_VALIDATORS.get(declared)
    if model is None:
        print(
            f"invalid: {path} declares unsupported contract_version {declared!r}; "
            f"{_CONTRACTS_PACKAGE_HINT}"
        )
        return EXIT_CONTRACT_VIOLATION

    try:
        model.model_validate(payload)
    except ValidationError as exc:
        print(f"invalid: {path} does not satisfy {model.__name__}")
        for error in exc.errors():
            location = _format_location(tuple(error["loc"]))
            print(f"  {location}: {error['msg']} [{error['type']}]")
        print(f"{len(exc.errors())} contract violation(s)")
        return EXIT_CONTRACT_VIOLATION
    print(f"valid: {path} satisfies {model.__name__} (contract {declared})")
    return EXIT_OK


# ----------------------------------------------------------------------- config


def run_config_validate(args: argparse.Namespace) -> int:
    """Load, validate and report configuration."""
    project_root = _project_root(args)
    logger = _validation_logger()
    service = ConfigurationService(project_root=project_root, logger=logger)
    application_path = Path(args.config).resolve() if args.config else None
    models_path = Path(args.models).resolve() if args.models else None
    result = (
        service.load_from(application_path=application_path, models_path=models_path)
        if (application_path or models_path)
        else service.load()
    )
    result_settings = result.settings

    if getattr(args, "json_output", False):
        payload = {
            "ok": result.ok,
            "sources": result.sources,
            "issues": [issue.render() for issue in result.issues],
            "secret_findings": result.secret_findings,
            "environment": result_settings.app.environment.value if result_settings else None,
            "profiles": sorted(result_settings.models.profiles) if result_settings else [],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for source in result.sources:
            print(f"config: {source}")
        for issue in result.issues:
            print(f"  issue: {issue.render()}")
        for finding in result.secret_findings:
            print(f"  secret: {finding}")
        if result.ok and result_settings is not None:
            print(
                f"valid: environment={result_settings.app.environment.value}, "
                f"profiles={', '.join(sorted(result_settings.models.profiles)) or 'none'}"
            )

    if not result.ok:
        logger.event(
            CONFIG_VALIDATION_FAILED,
            error_code="config_invalid",
            issue_count=len(result.issues),
        )
        return EXIT_CONFIG_ERROR
    return EXIT_OK


# --------------------------------------------------------------------- platform


def run_platform_check(args: argparse.Namespace) -> int:
    """Run the platform self-checks and print a status table."""
    project_root = _project_root(args)
    try:
        context = ApplicationBootstrap(project_root=project_root).build()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        print("error: invalid configuration", file=sys.stderr)
        for error in exc.errors():
            print(f"  {_format_location(tuple(error['loc']))}: {error['msg']}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        report = context.health_check.run()
    finally:
        context.close()

    if getattr(args, "json_output", False):
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "checks": [
                        {"name": c.name, "status": c.status.value, "detail": c.detail}
                        for c in report.checks
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"platform check ({project_root})")
        for check in report.checks:
            print(f"  {check.render()}")
        if report.failed:
            print("result: FAILED - fix the failed checks above")
        elif report.warnings:
            print("result: PASS WITH WARNINGS")
        else:
            print("result: PASS")
    if report.failed:
        failed_names = {c.name for c in report.checks if c.status is CheckStatus.FAILED}
        if failed_names == {"configuration"}:
            return EXIT_CONFIG_ERROR
        return EXIT_INFRASTRUCTURE_ERROR
    return EXIT_OK


# ------------------------------------------------------------------ workflow smoke


def run_workflow_smoke(args: argparse.Namespace) -> int:
    """Execute the technical Agent 1A graph once."""
    project_root = _project_root(args)
    try:
        context = ApplicationBootstrap(
            project_root=project_root,
            enable_model_gateway=not getattr(args, "no_model", False),
        ).build()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        print("error: invalid configuration", file=sys.stderr)
        for error in exc.errors():
            print(f"  {_format_location(tuple(error['loc']))}: {error['msg']}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        runtime = context.runtime_factory.build_agent_1a()
        request = SmokeRunRequest(
            document_id=(args.document_id or "doc_smoke_0001"),
            note=(args.note or None),
        )
        result = runtime.run(request)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as exc:
        print(f"error: smoke workflow failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION
    finally:
        context.close()

    payload = {
        "smoke": True,
        "run_id": result.run_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "technical_trace_ref": result.technical_trace_ref,
        "warnings": list(result.final_state.get("warnings") or []),
        "note": "SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED",
    }
    if getattr(args, "json_output", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("agent1a workflow-smoke")
        print(f"  run_id:         {payload['run_id']}")
        print(f"  correlation_id: {payload['correlation_id']}")
        print(f"  status:         {payload['status']}")
        print(f"  trace artifact: {payload['technical_trace_ref']}")
        for warning in payload["warnings"]:
            print(f"  warning:        {warning}")
        print()
        print("SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED")
    if result.status.value not in {"completed", "completed_with_warnings"}:
        print(
            f"error: smoke workflow ended in status {result.status.value!r}",
            file=sys.stderr,
        )
        return EXIT_CONTRACT_VIOLATION
    return EXIT_OK


# ------------------------------------------------------------------- documents


def _document_context(project_root: Path) -> ApplicationContext:
    return ApplicationBootstrap(project_root=project_root).build()


def run_document_process(args: argparse.Namespace) -> int:
    """Process one document end to end."""
    project_root = _project_root(args)
    try:
        context = _document_context(project_root)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        print("error: invalid configuration", file=sys.stderr)
        for error in exc.errors():
            print(f"  {_format_location(tuple(error['loc']))}: {error['msg']}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        dependencies = document_dependencies(
            settings=context.settings,
            pipeline=context.document_pipeline,
            run_manager=context.run_manager,
            artifacts=context.artifact_service,
            logger=context.logger,
        )
        workflow = context.document_pipeline.workflow(dependencies)
        result = workflow.run(input_path=args.input, dry_run=getattr(args, "dry_run", False))
    except DocumentPipelineError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        if exc.code in {"parser_unavailable"}:
            return EXIT_INFRASTRUCTURE_ERROR
        return EXIT_CONTRACT_VIOLATION
    except ModuleNotFoundError as exc:
        print(f"error: parser unavailable: {exc}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE_ERROR
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION
    finally:
        context.close()

    if getattr(args, "dry_run", False):
        print(
            f"dry-run: {args.input} is an acceptable {result.state.get('processing_status')} input"
        )
        print("dry-run: the input passed validation; nothing was parsed or stored")
        return EXIT_OK

    report = result.quality_report
    if report is None:
        print("error: the workflow produced no quality report", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION
    payload = {
        "run_id": result.run_id,
        "document_id": result.document_id,
        "source_file_name": result.state.get("file_name"),
        "source_type": result.state.get("source_type"),
        "source_sha256": result.state.get("source_sha256"),
        "source_size_bytes": result.state.get("source_size_bytes"),
        "page_count": result.state.get("page_count"),
        "language": result.state.get("language"),
        "parser_name": result.state.get("parser_name"),
        "parser_version": result.state.get("parser_version"),
        "used_fallback": result.state.get("used_fallback", False),
        "parsing_status": result.state.get("processing_status"),
        "quality_status": report.overall_status.value,
        "warnings_count": report.warnings_count,
        "errors_count": report.errors_count,
        "ocr_required_pages": report.ocr_required_pages,
        "parsed_document_uri": result.state.get("parsed_document_uri"),
        "quality_report_uri": result.state.get("quality_report_uri"),
        "run_status": result.status.value,
        "stop_reason": result.state.get("stop_reason"),
    }
    if getattr(args, "json_output", False):
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("document process")
        print(f"  run_id:            {payload['run_id']}")
        print(f"  document_id:       {payload['document_id']}")
        print(f"  source:            {payload['source_file_name']} ({payload['source_type']})")
        print(
            f"  size:              {payload['source_size_bytes']} bytes"
            f"  sha256:            {payload['source_sha256']}"
        )
        print(f"  pages:             {payload['page_count']} (null for TXT)")
        print(f"  language:          {payload['language']}")
        print(f"  parser:            {payload['parser_name']} {payload['parser_version']}")
        print(f"  fallback:          {payload['used_fallback']}")
        print(f"  quality:           {payload['quality_status']}")
        print(f"  warnings / errors: {payload['warnings_count']} / {payload['errors_count']}")
        print(f"  parsed document:   {payload['parsed_document_uri']}")
        print(f"  quality report:    {payload['quality_report_uri']}")
        print(f"  run status:        {payload['run_status']}")
        if payload["ocr_required_pages"]:
            print(f"  ocr required:      pages {payload['ocr_required_pages']}")
        if payload["stop_reason"]:
            print(f"  stop reason:       {payload['stop_reason']}")

    status = report.overall_status
    if status.is_ready:
        return EXIT_OK
    if status in {QualityStatus.REQUIRES_OCR, QualityStatus.NEEDS_HUMAN_REVIEW}:
        return EXIT_CONTROLLED_STOP
    return EXIT_CONTRACT_VIOLATION


def run_document_report(args: argparse.Namespace) -> int:
    """Print the stored Quality Report of a run."""
    project_root = _project_root(args)
    try:
        context = _document_context(project_root)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        print("error: invalid configuration", file=sys.stderr)
        for error in exc.errors():
            print(f"  {_format_location(tuple(error['loc']))}: {error['msg']}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        metadata = [
            item
            for item in context.artifact_service.list_for_run(args.run_id)
            if item.artifact_type.value == "quality_report"
        ]
        if not metadata:
            print(
                f"error: no quality report is stored for run {args.run_id!r}",
                file=sys.stderr,
            )
            return EXIT_CONTRACT_VIOLATION
        raw = context.artifact_service.read_by_id(metadata[0].artifact_id)
    finally:
        context.close()

    report = json.loads(raw)
    if getattr(args, "json_output", False):
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(f"quality report for run {args.run_id}")
        print(f"  document_id:   {report['document_id']}")
        print(f"  status:        {report['overall_status']}")
        print(f"  policy:        {report['quality_policy_version']}")
        print(f"  pages:         {report['processed_pages']} / {report['page_count']}")
        print(f"  warnings:      {report['warnings_count']}")
        print(f"  errors:        {report['errors_count']}")
        if report["ocr_required_pages"]:
            print(f"  ocr pages:     {report['ocr_required_pages']}")
        print(f"  summary:       {report['summary']}")
        print("  checks:")
        for check in report["checks"]:
            print(
                f"    - {check['check_id']}: {check['status']} "
                f"[{check['severity']}] {check['message']}"
            )
    return EXIT_OK


# ------------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if getattr(args, "version", False):
        return _print_version(getattr(args, "json_output", False))

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    if args.command == "contracts":
        if getattr(args, "contracts_command", None) != "validate":
            print(
                "error: 'contracts' requires a subcommand; "
                "run: research-platform contracts validate --input <path>",
                file=sys.stderr,
            )
            return EXIT_CONTRACT_VIOLATION
        return validate_package_file(Path(args.input))

    if args.command == "config":
        if getattr(args, "config_command", None) != "validate":
            print(
                "error: 'config' requires a subcommand; run: research-platform config validate",
                file=sys.stderr,
            )
            return EXIT_CONTRACT_VIOLATION
        return run_config_validate(args)

    if args.command == "platform":
        if getattr(args, "platform_command", None) != "check":
            print(
                "error: 'platform' requires a subcommand; run: research-platform platform check",
                file=sys.stderr,
            )
            return EXIT_CONTRACT_VIOLATION
        return run_platform_check(args)

    if args.command == "document":
        if getattr(args, "document_command", None) == "process":
            return run_document_process(args)
        if getattr(args, "document_command", None) == "report":
            return run_document_report(args)
        print(
            "error: 'document' requires a subcommand; run: research-platform document "
            "process --input <path>",
            file=sys.stderr,
        )
        return EXIT_CONTRACT_VIOLATION

    if args.command == "agent1a":
        if getattr(args, "agent1a_command", None) != "workflow-smoke":
            print(
                "error: 'agent1a' requires a subcommand; "
                "run: research-platform agent1a workflow-smoke",
                file=sys.stderr,
            )
            return EXIT_CONTRACT_VIOLATION
        return run_workflow_smoke(args)

    parser.print_help()
    return EXIT_CONTRACT_VIOLATION


if __name__ == "__main__":  # pragma: no cover - convenience for ``python -m``
    raise SystemExit(main())
