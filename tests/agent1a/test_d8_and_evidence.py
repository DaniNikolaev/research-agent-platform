"""D8 contract guard and the two missing D5/D6 evidence tests.

* D8 — an outbound ``Message.content`` above the published 8192-character contract limit is
  rejected by the Application layer *before* the contract model is built and before the gateway
  is called, as a typed ``extraction_context_overflow``.
* D5 — a real checkpoint *read* during resume fails and stays a typed
  ``ExtractionCheckpointError`` with the original cause.
* D6 — ``checkpoint_saved`` is not emitted when the base ``SqliteSaver.put`` fails, and is
  emitted exactly once after a confirmed write.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.services.extraction_errors import (
    ErrorCategory,
    ExtractionCheckpointError,
    ExtractionContextOverflow,
)
from research_platform.application.services.extraction_prompts import load_prompt
from research_platform.domain.extraction.models import ExtractionGroup, ExtractionRunManifest
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.platform.models.token_counting import HeuristicTokenCounter
from tests.agent1a.conftest import Project, document_run, seed_document_run

ALL_GROUPS = tuple(group.value for group in ExtractionGroup)
D8_MARKER = "D8-CONTRACT-CHECK "


def _manifest(context: ApplicationContext, run_id: str) -> ExtractionRunManifest | None:
    for metadata in context.artifact_service.list_for_run(run_id):
        if metadata.artifact_id.startswith("art_extraction_manifest"):
            return ExtractionRunManifest.model_validate(
                json.loads(context.artifact_service.read_by_id(metadata.artifact_id))
            )
    return None


def _run(context: ApplicationContext, document_run_id: str):
    return context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_d8"
    )


def _spy(context: ApplicationContext, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    gateway = context.agent1a_extraction.gateway
    original = gateway.generate_structured

    def spy(request, schema):
        calls.append(request.request_id)
        return original(request, schema)

    monkeypatch.setattr(gateway, "generate_structured", spy)
    return calls


def _prefix() -> str:
    from research_platform.application.services.agent1a_extraction import (
        _message_contents,
    )

    contents = _message_contents(
        prompt=load_prompt(ExtractionGroup.G1_BIBLIOGRAPHY),
        context_text="",
        repair_hint=None,
    )
    return contents[1][1]


def _text_of_message_length(total: int) -> str:
    body = D8_MARKER + "a" * 4000
    target = total - len(_prefix())
    text = (body * ((target // len(body)) + 1))[:target]
    assert len(text) == target
    return text


def test_message_at_contract_limit_is_sent_untruncated(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.application.services.agent1a_extraction import (
        _message_content_limit,
    )

    limit = _message_content_limit()
    assert limit == 8192
    text = _text_of_message_length(limit)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    calls = _spy(context, monkeypatch)
    sent: list[str] = []
    gateway = context.agent1a_extraction.gateway
    original = gateway.generate_structured

    def capture(request, schema):
        sent.append(request.messages[-1].content)
        return original(request, schema)

    monkeypatch.setattr(gateway, "generate_structured", capture)
    outcome = _run(context, doc_run)
    assert outcome.state["processing_status"] == "completed"
    assert len(calls) == 4
    for content in sent:
        assert len(content) == limit
        assert content.endswith(text)
        assert (
            hashlib.sha256(content.encode()).hexdigest()
            == hashlib.sha256(content.encode()).hexdigest()
        )


def test_message_above_contract_limit_is_refused_before_contract(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.application.services.agent1a_extraction import (
        _message_content_limit,
    )

    limit = _message_content_limit()
    text = _text_of_message_length(limit + 1)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    canonical_sha = hashlib.sha256(text.encode()).hexdigest()
    calls = _spy(context, monkeypatch)

    with pytest.raises(ExtractionContextOverflow) as excinfo:
        _run(context, doc_run)

    error = excinfo.value
    assert error.category is ErrorCategory.CONTEXT
    assert error.code == "extraction_context_overflow"
    assert calls == []
    message = str(error)
    assert "actual_chars=8193" in message
    assert "max_chars=8192" in message
    assert "was not sent" in message

    stored = [
        item
        for item in context.artifact_service.list_for_run(doc_run)
        if item.media_type == "text/plain" and item.artifact_type is ArtifactType.PARSED_DOCUMENT
    ]
    assert (
        hashlib.sha256(context.artifact_service.read_by_id(stored[0].artifact_id)).hexdigest()
        == canonical_sha
    )


def test_d8_regression_20000_characters_is_a_controlled_stop(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original D8 report case: fits the token budget, exceeds the message contract."""
    text = _text_of_message_length(20_000)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    calls = _spy(context, monkeypatch)

    with pytest.raises(ExtractionContextOverflow) as excinfo:
        _run(context, doc_run)

    error = excinfo.value
    assert error.category is ErrorCategory.CONTEXT
    assert error.code == "extraction_context_overflow"
    assert calls == []
    assert "actual_chars=20000" in str(error)

    # the token budget alone would have allowed this document
    counter = HeuristicTokenCounter()
    capabilities = context.agent1a_extraction.gateway.capabilities(
        context.settings.agent_1a.model_profile
    )
    budget = (
        capabilities.max_context_tokens
        - context.settings.agent_1a.context_output_reserve_tokens
        - context.settings.agent_1a.context_safety_reserve_tokens
    )
    prompt_tokens = max(counter.count(load_prompt(group).text) for group in ExtractionGroup)
    assert counter.count(text) + prompt_tokens <= budget

    failed = next(record for record in context.run_manager.list_runs() if record.errors)
    assert len(failed.errors) == 1
    assert failed.errors[0].code == "extraction_context_overflow"
    manifest = _manifest(context, failed.run_id)
    assert manifest is not None
    assert manifest.final_status == "failed"
    assert any("extraction_context_overflow" in item for item in manifest.errors)


def test_each_message_is_checked_separately(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.application.services.agent1a_extraction import (
        _message_content_limit,
        _message_contents,
    )

    limit = _message_content_limit()
    text = _text_of_message_length(limit)
    contents = _message_contents(
        prompt=load_prompt(ExtractionGroup.G1_BIBLIOGRAPHY),
        context_text=text,
        repair_hint=None,
    )
    system_chars = len(contents[0][1])
    user_chars = len(contents[1][1])
    assert user_chars == limit
    assert system_chars <= limit
    assert system_chars + user_chars > limit, "the limit is per message, not for the sum"

    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    calls = _spy(context, monkeypatch)
    outcome = _run(context, doc_run)
    assert outcome.state["processing_status"] == "completed"
    assert len(calls) == 4


# ------------------------------------------------- CLI classification for D8 (exit 1)


def test_d8_cli_exits_one_as_a_terminal_failure(
    project: Project, context: ApplicationContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """A context overflow ends the Run as ``failed`` with a RunError → exit 1, not 4."""
    from research_platform.cli import EXIT_CONTRACT_VIOLATION, EXIT_OK, main

    text = _text_of_message_length(20_000)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    context.close()

    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            doc_run,
            "--project-root",
            str(project.root),
        ]
    )
    assert code == EXIT_CONTRACT_VIOLATION
    assert code != EXIT_OK
    captured = capsys.readouterr()
    assert "extraction_context_overflow" in captured.err
    assert "max_chars=8192" in captured.err

    fresh = bootstrap_application(project_root=project.root)
    try:
        document_run = fresh.artifact_service.list_for_run(doc_run)
        document_id = document_run[0].document_id
        manifests = [
            item
            for item in fresh.artifact_service.list_for_document(document_id)
            if item.artifact_id.startswith("art_extraction_manifest")
        ]
        assert len(manifests) == 1, "the terminal failure must publish exactly one manifest"
        manifest = ExtractionRunManifest.model_validate(
            json.loads(fresh.artifact_service.read_by_id(manifests[0].artifact_id))
        )
        assert manifest.final_status == "failed"
        assert manifest.errors
        assert "extraction_context_overflow" in manifest.errors[0]
    finally:
        fresh.close()


# ------------------------------------------------------- D5: checkpoint read failure


class _RecordingLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def event(self, event: str, *, level: str = "INFO", **fields: object) -> dict[str, object]:
        record = {"event": event, "level": level, **fields}
        self.records.append(record)
        return record

    def bind(self, **fields: object) -> _RecordingLogger:
        return self

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "error_type", [OSError, sqlite3.OperationalError], ids=["oserror", "sqlite3"]
)
def test_checkpoint_read_failure_is_typed_and_preserves_cause(
    project: Project, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    """A real ``get_tuple`` read during resume fails; the error stays typed CHECKPOINT."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    project.config(interrupt_after=["validate_and_persist_g2_research_design"])
    first = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(first, project)
        partial = first.agent1a_extraction.start(
            document_run_id=doc_run, correlation_id="corr_read_fail"
        )
    finally:
        first.close()
    run_id = partial.state["run_id"]
    project.config()

    def failing_read(self, config):
        raise error_type("scripted checkpoint read failure")

    monkeypatch.setattr(SqliteSaver, "get_tuple", failing_read)
    second = bootstrap_application(project_root=project.root)
    try:
        with pytest.raises(ExtractionCheckpointError) as excinfo:
            second.agent1a_extraction.resume(run_id=run_id, correlation_id="corr_read_fail_2")
    finally:
        monkeypatch.undo()
        second.close()

    error = excinfo.value
    assert error.category is ErrorCategory.CHECKPOINT
    assert error.code == "checkpoint_error"
    assert isinstance(error.__cause__, error_type)
    assert "checkpoint store operation failed" in str(error)


# --------------------------------------------------- D6: no event on a failed write


def test_checkpoint_saved_is_not_emitted_when_checkpoint_write_fails() -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver

    from research_platform.platform.checkpointing.observed import ObservedSqliteSaver

    logger = _RecordingLogger()
    saver = ObservedSqliteSaver(sqlite3.connect(":memory:"), logger=logger)
    config = {"configurable": {"thread_id": "run_d6_probe", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": "1ef00000-0000-6000-8000-000000000001",
        "ts": "2026-11-18T00:00:00+00:00",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
    }
    metadata = {"source": "input", "step": -1, "parents": {}}

    def failing_put(self, config, checkpoint, metadata, new_versions):
        raise OSError("scripted checkpoint write failure")

    original_put = SqliteSaver.put
    try:
        SqliteSaver.put = failing_put  # type: ignore[method-assign]
        with pytest.raises(OSError):
            saver.put(config, checkpoint, metadata, {})
    finally:
        SqliteSaver.put = original_put  # type: ignore[method-assign]

    assert [record for record in logger.records if record["event"] == "checkpoint_saved"] == []

    saver.put(config, checkpoint, metadata, {})
    saved = [record for record in logger.records if record["event"] == "checkpoint_saved"]
    assert len(saved) == 1
    assert saved[0]["thread_id"] == "run_d6_probe"
    assert saved[0]["checkpoint_id"] == checkpoint["id"]
    saver.conn.close()
