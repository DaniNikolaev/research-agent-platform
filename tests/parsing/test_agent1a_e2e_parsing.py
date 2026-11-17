"""End-to-end extraction over real parsed documents (scenarios A and C of the iteration brief).

These tests use the real document workflow and the real Docling parser, then run the Agent 1A
workflow on the resulting document run.  The extraction itself is the deterministic fake
adapter, and every assertion respects that: the results are *technical*, not scientific.
"""

from __future__ import annotations

import json

import pytest

from research_platform.app import ApplicationContext
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.runs.artifacts import ArtifactType
from tests.agent1a.conftest import Project, document_run
from tests.parsing.pdf_fixtures import build_fixture

pytestmark = pytest.mark.parsing


def _pdf_document_run(context: ApplicationContext, project: Project, key: str) -> tuple[str, str]:
    """Process a PDF fixture with the real document workflow; return (run_id, quality)."""
    from research_platform.composition.document_pipeline import document_dependencies

    pdf = build_fixture(key, project.root / "fixtures")
    dependencies = document_dependencies(
        settings=context.settings,
        pipeline=context.document_pipeline,
        run_manager=context.run_manager,
        artifacts=context.artifact_service,
        logger=context.logger,
    )
    result = context.document_pipeline.workflow(dependencies).run(input_path=str(pdf))
    return result.run_id, result.quality_report.overall_status.value


def test_scenario_a_digital_pdf(project: Project, context: ApplicationContext) -> None:
    """A digital PDF is parsed, then extracted through four sequential groups."""
    document_run_id, quality = _pdf_document_run(context, project, "single_page_en")
    assert quality in {"passed", "passed_with_warnings"}

    outcome = context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_e2e_pdf"
    )
    state = outcome.state
    assert state["processing_status"] == "completed"
    assert state["quality_status"] == quality
    assert state["completed_groups"] == [
        "g1_bibliography",
        "g2_research_design",
        "g3_methods_data_variables",
        "g4_findings_implications",
    ]

    run_id = state["run_id"]
    candidates = [
        metadata
        for metadata in context.artifact_service.list_for_run(run_id)
        if metadata.artifact_type is ArtifactType.EXTRACTION_CANDIDATES
    ]
    assert len(candidates) == 5, "four groups plus the aggregate"
    for metadata in candidates:
        payload = json.loads(context.artifact_service.read_by_id(metadata.artifact_id))
        assert payload.get("execution_mode", "simulation") == "simulation"

    manifest_meta = next(
        metadata
        for metadata in context.artifact_service.list_for_run(run_id)
        if metadata.artifact_id.startswith("art_extraction_manifest")
    )
    manifest = json.loads(context.artifact_service.read_by_id(manifest_meta.artifact_id))
    assert manifest["execution_mode"] == "simulation"
    assert manifest["provider_id"] == "fake"
    assert manifest["document_run_id"] == document_run_id
    assert len(manifest["group_result_uris"]) == 4


def test_scenario_c_scanned_pdf_is_never_extracted(
    project: Project, context: ApplicationContext
) -> None:
    """A scanned PDF stops at requires_ocr and the extraction never starts."""
    document_run_id, quality = _pdf_document_run(context, project, "scanned_only")
    assert quality == "requires_ocr"

    outcome = context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_e2e_scan"
    )
    assert outcome.state["processing_status"] == "requires_ocr"
    assert outcome.state["completed_groups"] == []
    run_id = outcome.state["run_id"]
    assert [
        metadata
        for metadata in context.artifact_service.list_for_run(run_id)
        if metadata.artifact_type is ArtifactType.EXTRACTION_CANDIDATES
    ] == []
    assert outcome.run is not None
    assert outcome.run.status.value == "waiting_for_human"


def test_multipage_pdf_extraction_keeps_pagination_out_of_the_state(
    project: Project, context: ApplicationContext
) -> None:
    """A three-page PDF is extracted and the state stays transport-sized."""
    document_run_id, quality = _pdf_document_run(context, project, "multi_page_en")
    assert quality in {"passed", "passed_with_warnings"}
    outcome = context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_e2e_multi"
    )
    assert outcome.state["processing_status"] == "completed"
    serialized = json.dumps(outcome.state, default=str)
    assert len(serialized) < 4096
    assert "Method" not in serialized


def test_txt_document_run_is_extractable_end_to_end(
    project: Project, context: ApplicationContext
) -> None:
    from tests.agent1a.conftest import write_txt

    path = write_txt(project)
    from research_platform.composition.document_pipeline import document_dependencies

    dependencies = document_dependencies(
        settings=context.settings,
        pipeline=context.document_pipeline,
        run_manager=context.run_manager,
        artifacts=context.artifact_service,
        logger=context.logger,
    )
    document_run_id = (
        context.document_pipeline.workflow(dependencies).run(input_path=str(path)).run_id
    )
    outcome = context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_e2e_txt"
    )
    assert outcome.state["processing_status"] == "completed"
    assert outcome.state["quality_status"] == QualityStatus.PASSED.value
    assert document_run(context, project) != document_run_id
