"""Fixtures of the Agent 1A extraction tests.

Two ways to obtain a document run:

* the *real* document workflow (TXT and PDF fixtures) — used by the end-to-end tests;
* :func:`seed_document_run` — publishes the four document artefacts with a chosen Quality Gate
  verdict, so every routing branch can be tested without re-running Docling.

Both produce exactly the artefact bundle the extraction workflow reads.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.domain.documents.elements import (
    CANONICALIZATION_VERSION,
)
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.documents.models import (
    DocumentLanguage,
    SourceDocumentManifest,
    SourceMediaType,
)
from research_platform.domain.documents.parsed import (
    CanonicalTextInfo,
    ParsedDocument,
    ParserProvenance,
    ParsingStatus,
)
from research_platform.domain.documents.quality import (
    CheckSeverity,
    CheckStatus,
    QualityCheckResult,
    QualityReport,
    QualityStatus,
)
from research_platform.domain.runs.artifacts import ArtifactType

ENC = "utf-8"

ARTICLE_TEXT = (
    "Deterministic Extraction of Scientific Documents\n\n"
    "Abstract\n\n"
    "The platform parses a document into pages, sections and blocks and keeps the physical "
    "page of every element.\n\n"
    "1 Introduction\n\n"
    "Reviewers need reproducible evidence, so every element keeps its origin.\n\n"
    "2 Method\n\n"
    "We apply a sequential extraction over four field groups and record every attempt.\n\n"
    "3 Conclusion\n\n"
    "The behaviour is reproducible and bounded.\n"
)


def _checks_for(status: QualityStatus) -> list[QualityCheckResult]:
    """A minimal consistent check list for the seeded verdict."""
    if status is QualityStatus.FAILED:
        return [
            QualityCheckResult(
                check_id="structure_page_count_matches_source",
                status=CheckStatus.FAILED,
                severity=CheckSeverity.CRITICAL,
                message="seeded: the parsed document does not cover the source pages",
            )
        ]
    if status is QualityStatus.NEEDS_HUMAN_REVIEW:
        return [
            QualityCheckResult(
                check_id="provenance_character_ranges",
                status=CheckStatus.FAILED,
                severity=CheckSeverity.MAJOR,
                message="seeded: character ranges do not reproduce the text",
            )
        ]
    return []


@dataclass(frozen=True, slots=True)
class Project:
    """A temporary project root with a SQLite checkpoint database and a fake profile."""

    root: Path
    checkpoint_database: Path

    def config(self, *, observability: dict[str, Any] | None = None, **agent_1a: Any) -> None:
        """(Re)write the configuration with optional ``agent_1a`` overrides."""
        settings = {
            "workflow_version": "1.0",
            "graph_state_version": "1.0",
            "prompt_set_version": "1.0",
            "model_profile": "fake-extraction",
            "retrieval_mode": "full_context",
        }
        settings.update(agent_1a)
        application = {
            "app": {"name": "research-agent-platform", "environment": "local"},
            "runtime": {
                "checkpointer": "sqlite",
                "checkpoint_database": "data/checkpoints/agent1a.sqlite3",
                "checkpoint_timeout_seconds": 30.0,
                "max_graph_steps": 60,
            },
            "storage": {"artifact_root_uri": "artifact://local/artifacts"},
            "agent_1a": settings,
        }
        if observability is not None:
            application["observability"] = observability
        models = {
            "models": {
                "default_profile": "fake-extraction",
                "profiles": {
                    "fake-extraction": {
                        "provider": "fake_provider",
                        "model_id": "fake-structured-v1",
                        "purpose": "extraction",
                        "is_fake": True,
                        "max_context_tokens": 16000,
                        "max_output_tokens": 2048,
                        "supports_structured_output": True,
                        "supports_json_schema": True,
                    }
                },
            }
        }
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "config" / "application.yaml").write_text(
            yaml.safe_dump(application, sort_keys=False), encoding=ENC
        )
        (self.root / "config" / "models.yaml").write_text(
            yaml.safe_dump(models, sort_keys=False), encoding=ENC
        )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Temporary project root, configured but not yet bootstrapped."""
    root = tmp_path / "project"
    root.mkdir()
    proj = Project(root=root, checkpoint_database=root / "data/checkpoints/agent1a.sqlite3")
    proj.config()
    return proj


@pytest.fixture
def context(project: Project) -> Iterator[ApplicationContext]:
    """Bootstrapped application bound to the temporary project."""
    ctx = bootstrap_application(project_root=project.root)
    try:
        yield ctx
    finally:
        ctx.close()


def write_txt(project: Project, name: str = "article.txt") -> Path:
    path = project.root / name
    path.write_text(ARTICLE_TEXT, encoding=ENC)
    return path


def document_run(context: ApplicationContext, project: Project) -> str:
    """Run the real document workflow on a TXT file and return its run id."""
    from research_platform.composition.document_pipeline import document_dependencies

    path = write_txt(project)
    dependencies = document_dependencies(
        settings=context.settings,
        pipeline=context.document_pipeline,
        run_manager=context.run_manager,
        artifacts=context.artifact_service,
        logger=context.logger,
    )
    result = context.document_pipeline.workflow(dependencies).run(input_path=str(path))
    return result.run_id


def seed_document_run(
    artifacts: ArtifactService,
    *,
    quality_status: QualityStatus = QualityStatus.PASSED,
    document_run_id: str = "run_seeded_document_0001",
    canonical_text: str = ARTICLE_TEXT,
    tamper_sha: bool = False,
) -> str:
    """Publish the four document artefacts of a run with a chosen Quality Gate verdict."""
    sha256 = hashlib.sha256(canonical_text.encode(ENC)).hexdigest()
    document_id = "doc_" + hashlib.sha256(sha256.encode()).hexdigest()[:24]
    manifest = SourceDocumentManifest(
        document_id=document_id,
        run_id=document_run_id,
        source_file_name="seeded.txt",
        source_type=DocumentSourceType.TXT,
        source_media_type=SourceMediaType.TEXT,
        source_sha256=sha256,
        source_size_bytes=len(canonical_text.encode(ENC)),
        original_encoding="utf-8",
        source_artifact_uri=(
            f"artifact://documents/{document_id}/runs/{document_run_id}/original_document/"
            "art_source_seeded"
        ),
        language=DocumentLanguage.EN,
        created_at="2026-11-17T12:00:00Z",
        processing_status="quality_checked",
    )
    parsed = ParsedDocument(
        document_id=document_id,
        run_id=document_run_id,
        source_sha256=sha256 if not tamper_sha else "0" * 64,
        source_type=DocumentSourceType.TXT,
        language=DocumentLanguage.EN,
        parser=ParserProvenance(
            parser_name="txt-block-parser",
            parser_version="1.0",
            pipeline_version="txt-blocks/1",
            parser_options_hash="0123456789abcdef",
            backend_name="python-regex",
            table_extraction_enabled=False,
            ocr_enabled=False,
        ),
        parsing_status=ParsingStatus.PARSED,
        page_count=None,
        pages=[],
        sections=[],
        elements=[],
        tables=[],
        figures=[],
        canonical_text=CanonicalTextInfo(
            artifact_uri=(
                f"artifact://documents/{document_id}/runs/{document_run_id}/parsed_document/"
                "art_canonical_seeded"
            ),
            canonicalization_version=CANONICALIZATION_VERSION,
            char_count=len(canonical_text),
            page_separator="\f",
        ),
        source_artifact_uri=manifest.source_artifact_uri,
        created_at="2026-11-17T12:00:00Z",
    )
    report = QualityReport(
        document_id=document_id,
        run_id=document_run_id,
        source_sha256=sha256 if not tamper_sha else "0" * 64,
        parsed_document_version="1.0",
        quality_policy_version="1.0",
        overall_status=quality_status,
        page_count=None,
        processed_pages=0,
        warnings_count=0,
        errors_count=1
        if quality_status in {QualityStatus.FAILED, QualityStatus.NEEDS_HUMAN_REVIEW}
        else 0,
        ocr_required_pages=[1] if quality_status is QualityStatus.REQUIRES_OCR else [],
        page_results=[],
        checks=_checks_for(quality_status),
        summary=f"seeded report with status {quality_status.value}",
        created_at="2026-11-17T12:00:00Z",
    )

    def store(payload: bytes, *, artifact_id: str, artifact_type: ArtifactType, media: str) -> None:
        artifacts.store_bytes(
            data=payload,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            media_type=media,
            document_id=document_id,
            run_id=document_run_id,
        )

    store(
        json.dumps(manifest.model_dump(mode="json")).encode(ENC),
        artifact_id="art_manifest_seeded",
        artifact_type=ArtifactType.HANDOFF_PACKAGE,
        media="application/json",
    )
    store(
        json.dumps(parsed.model_dump(mode="json")).encode(ENC),
        artifact_id="art_parsed_seeded",
        artifact_type=ArtifactType.PARSED_DOCUMENT,
        media="application/json",
    )
    store(
        canonical_text.encode(ENC),
        artifact_id="art_canonical_seeded",
        artifact_type=ArtifactType.PARSED_DOCUMENT,
        media="text/plain",
    )
    store(
        json.dumps(report.model_dump(mode="json")).encode(ENC),
        artifact_id="art_quality_seeded",
        artifact_type=ArtifactType.QUALITY_REPORT,
        media="application/json",
    )
    return document_run_id
