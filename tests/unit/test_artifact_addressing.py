"""Three address spaces of the artefact store: document, run and platform scope."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.logging import silent_logger

from research_platform.application.ports.artifact_store import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactMetadata,
    ArtifactScopeError,
    ArtifactStorePort,
)
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.domain.runs.artifacts import (
    ARTIFACT_URI_TEMPLATE,
    ArtifactReference,
    ArtifactScope,
    ArtifactType,
    build_artifact_uri,
    parse_artifact_uri,
    scope_for,
)
from research_platform.platform.artifact_store import LocalFileArtifactStore

DOCUMENT = "doc_alpha_0001"
RUN = "run_20261112_120000_abcdef"
OTHER_RUN = "run_20261112_130000_bbbbbb"


@pytest.fixture
def store(tmp_path: Path) -> LocalFileArtifactStore:
    return LocalFileArtifactStore(root=tmp_path / "artifacts")


def document_artifact(store: LocalFileArtifactStore, *, data: bytes = b"%PDF-1.7"):
    return store.put(
        artifact_id="art_doc_source_0001",
        artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
        data=data,
        media_type="application/pdf",
        document_id=DOCUMENT,
        run_id=RUN,
    )


def run_artifact(store: LocalFileArtifactStore, *, artifact_id: str = "art_run_trace_0001"):
    return store.put(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.TRACE,
        data=b'{"trace": true}',
        media_type="application/json",
        run_id=RUN,
    )


def platform_artifact(
    store: LocalFileArtifactStore, *, artifact_id: str = "art_platform_report_0001"
):
    return store.put(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.RUN_LOG,
        data=b"platform diagnostics",
        media_type="text/plain",
    )


# ------------------------------------------------------------------ scope model


def test_scope_is_derived_from_the_identifiers() -> None:
    assert scope_for(document_id=DOCUMENT, run_id=RUN) is ArtifactScope.DOCUMENT
    assert scope_for(document_id=None, run_id=RUN) is ArtifactScope.RUN
    assert scope_for(document_id=None, run_id=None) is ArtifactScope.PLATFORM


def test_document_without_run_is_rejected() -> None:
    with pytest.raises(ValueError):
        scope_for(document_id=DOCUMENT, run_id=None)
    with pytest.raises(ValueError):
        build_artifact_uri(
            document_id=DOCUMENT, artifact_type=ArtifactType.TRACE, artifact_id="art_trace_0001"
        )


def test_three_uri_shapes() -> None:
    document_uri = build_artifact_uri(
        document_id=DOCUMENT,
        run_id=RUN,
        artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
        artifact_id="art_doc_source_0001",
    )
    run_uri = build_artifact_uri(
        run_id=RUN, artifact_type=ArtifactType.TRACE, artifact_id="art_run_trace_0001"
    )
    platform_uri = build_artifact_uri(
        artifact_type=ArtifactType.RUN_LOG, artifact_id="art_platform_report_0001"
    )
    assert document_uri == (
        f"artifact://documents/{DOCUMENT}/runs/{RUN}/original_document/art_doc_source_0001"
    )
    assert run_uri == f"artifact://runs/{RUN}/trace/art_run_trace_0001"
    assert platform_uri == "artifact://platform/run_log/art_platform_report_0001"


def test_uri_round_trip_preserves_scope() -> None:
    for document_id, run_id in ((DOCUMENT, RUN), (None, RUN), (None, None)):
        for artifact_type in (ArtifactType.TRACE, ArtifactType.RUN_LOG):
            uri = build_artifact_uri(
                document_id=document_id,
                run_id=run_id,
                artifact_type=artifact_type,
                artifact_id="art_example_0001",
            )
            parts = parse_artifact_uri(uri)
            assert parts.scope is scope_for(document_id=document_id, run_id=run_id)
            assert parts.document_id == document_id
            assert parts.run_id == run_id
            assert parts.artifact_type is artifact_type
            assert parts.artifact_id == "art_example_0001"


def test_the_document_uri_template_is_unchanged() -> None:
    assert ARTIFACT_URI_TEMPLATE == (
        "artifact://documents/{document_id}/runs/{run_id}/{artifact_type}/{artifact_id}"
    )


# ------------------------------------------------------- storing every scope


def test_document_artefact_is_created_and_read(store: LocalFileArtifactStore) -> None:
    metadata = document_artifact(store)
    assert metadata.scope is ArtifactScope.DOCUMENT
    assert metadata.document_id == DOCUMENT
    assert metadata.run_id == RUN
    assert store.get_by_id(metadata.artifact_id) == b"%PDF-1.7"
    assert store.get_metadata_by_id(metadata.artifact_id).uri == metadata.uri


def test_run_artefact_needs_no_document(store: LocalFileArtifactStore) -> None:
    metadata = run_artifact(store)
    assert metadata.scope is ArtifactScope.RUN
    assert metadata.document_id is None
    assert metadata.run_id == RUN
    assert metadata.uri.startswith("artifact://runs/")
    assert store.get_by_id(metadata.artifact_id) == b'{"trace": true}'
    assert store.exists_id(metadata.artifact_id) is True


def test_platform_artefact_needs_neither_document_nor_run(
    store: LocalFileArtifactStore,
) -> None:
    metadata = platform_artifact(store)
    assert metadata.scope is ArtifactScope.PLATFORM
    assert metadata.document_id is None
    assert metadata.run_id is None
    assert metadata.uri.startswith("artifact://platform/")
    assert store.get_by_id(metadata.artifact_id) == b"platform diagnostics"


def test_listing_respects_the_scope(store: LocalFileArtifactStore) -> None:
    document_artifact(store)
    run_artifact(store)
    platform_artifact(store)

    assert [item.artifact_id for item in store.list_by_document(DOCUMENT)] == [
        "art_doc_source_0001"
    ]
    assert sorted(item.artifact_id for item in store.list_by_run(RUN)) == [
        "art_doc_source_0001",
        "art_run_trace_0001",
    ]
    assert [item.artifact_id for item in store.list_platform()] == ["art_platform_report_0001"]


def test_uri_matches_metadata_for_every_scope(store: LocalFileArtifactStore) -> None:
    for metadata in (document_artifact(store), run_artifact(store), platform_artifact(store)):
        parts = parse_artifact_uri(metadata.uri)
        assert parts.scope is metadata.scope
        assert parts.artifact_id == metadata.artifact_id
        assert parts.artifact_type is metadata.artifact_type
        assert parts.document_id == metadata.document_id
        assert parts.run_id == metadata.run_id


# ------------------------------------------------------------- compatibility


def test_legacy_document_uri_still_works(store: LocalFileArtifactStore) -> None:
    metadata = document_artifact(store, data=b"legacy payload")
    reference = ArtifactReference(
        artifact_id=metadata.artifact_id,
        artifact_type=metadata.artifact_type,
        uri=metadata.uri,
        media_type=metadata.media_type,
        size_bytes=metadata.size_bytes,
        sha256=metadata.sha256,
        version="1",
        created_at=metadata.created_at,
    )
    assert reference.uri.startswith("artifact://documents/")
    assert store.get(reference) == b"legacy payload"
    assert store.get_metadata(reference).artifact_id == metadata.artifact_id


def test_published_reference_rejects_run_and_platform_uris(
    store: LocalFileArtifactStore,
) -> None:
    run_metadata = run_artifact(store)
    with pytest.raises(ValueError):
        ArtifactReference(
            artifact_id=run_metadata.artifact_id,
            artifact_type=run_metadata.artifact_type,
            uri=run_metadata.uri,
            media_type=run_metadata.media_type,
            size_bytes=run_metadata.size_bytes,
            sha256=run_metadata.sha256,
            version="1",
            created_at=run_metadata.created_at,
        )


def test_artifact_service_refuses_to_publish_a_non_document_reference(
    store: LocalFileArtifactStore,
) -> None:
    service = ArtifactService(store=store, logger=silent_logger())
    run_metadata = run_artifact(store)
    with pytest.raises(ValueError):
        service.reference_for(run_metadata)

    document_metadata = document_artifact(store)
    reference = service.reference_for(document_metadata)
    assert reference.uri == document_metadata.uri


def test_artifact_service_handles_all_scopes(store: LocalFileArtifactStore) -> None:
    service = ArtifactService(store=store, logger=silent_logger())
    platform = service.store_bytes(
        data=b"report",
        artifact_id="art_platform_0001",
        artifact_type=ArtifactType.RUN_LOG,
        media_type="text/plain",
    )
    assert platform.scope is ArtifactScope.PLATFORM
    assert service.read_by_id("art_platform_0001") == b"report"
    assert service.exists_id("art_platform_0001") is True
    assert [item.artifact_id for item in service.list_platform()] == ["art_platform_0001"]
    assert service.scan().ok is True


# ------------------------------------------------------------ invalid addresses


@pytest.mark.parametrize(
    "uri",
    [
        "artifact://documents/doc_alpha_0001/artifact_doc/art_x_0001",
        "artifact://documents/doc_alpha_0001/runs/run_1/artifact_doc/art_x_0001",
        "artifact://unknown_scope/trace/art_x_0001",
        "artifact://platform/art_x_0001",
        "artifact://platform/trace",
        "artifact://documents/doc_alpha_0001/runs/run_1/trace/art_x_0001/extra",
        "artifact://platform/TRACE/art_x_0001",
        "artifact://platform/trace/ART_X_0001",
        "",
    ],
)
def test_invalid_uris_are_rejected(uri: str) -> None:
    with pytest.raises(ValueError):
        parse_artifact_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        r"artifact://platform/trace/..\..\windows\system32",
        "artifact://platform/trace/../../etc/passwd",
        "C:\\Users\\owner\\artifacts\\trace\\art_x_0001",
        "/var/lib/artifacts/trace/art_x_0001",
        "artifact://platform/trace/%2e%2e%2fescape",
        "artifact://platform/trace/art%5fx%5f0001",
        "\\\\server\\share\\artifacts\\trace\\art_x_0001",
    ],
)
def test_non_logical_or_traversing_addresses_are_rejected(uri: str) -> None:
    with pytest.raises(ValueError):
        parse_artifact_uri(uri)


def test_scope_metadata_mismatch_is_rejected() -> None:
    payload = {
        "artifact_id": "art_run_trace_0001",
        "artifact_type": "trace",
        "uri": f"artifact://runs/{RUN}/trace/art_run_trace_0001",
        "sha256": "a" * 64,
        "size_bytes": 3,
        "media_type": "application/json",
        "run_id": RUN,
        "document_id": None,
        "created_at": "2026-11-12T12:00:00Z",
        "storage_backend": "local_file",
    }
    ArtifactMetadata.model_validate(payload)  # the run shape is valid

    with pytest.raises(ValueError):
        ArtifactMetadata.model_validate({**payload, "document_id": DOCUMENT})
    with pytest.raises(ValueError):
        ArtifactMetadata.model_validate(
            {**payload, "uri": "artifact://platform/trace/art_run_trace_0001"}
        )


def test_store_rejects_unsafe_identifiers_in_every_scope(store: LocalFileArtifactStore) -> None:
    for document_id, run_id in ((DOCUMENT, RUN), (None, RUN), (None, None)):
        with pytest.raises(ValueError):
            store.put(
                artifact_id="../escape",
                artifact_type=ArtifactType.TRACE,
                data=b"x",
                media_type="text/plain",
                document_id=document_id,
                run_id=run_id,
            )


# -------------------------------------------------------------------- conflicts


def test_conflicting_content_in_run_scope(store: LocalFileArtifactStore) -> None:
    original = run_artifact(store)
    with pytest.raises(ArtifactConflictError):
        store.put(
            artifact_id=original.artifact_id,
            artifact_type=ArtifactType.TRACE,
            data=b"different",
            media_type="application/json",
            run_id=RUN,
        )
    assert store.get_by_id(original.artifact_id) == b'{"trace": true}'


def test_same_id_in_a_different_scope_is_a_conflict(store: LocalFileArtifactStore) -> None:
    metadata = run_artifact(store)
    with pytest.raises(ArtifactConflictError):
        store.put(
            artifact_id=metadata.artifact_id,
            artifact_type=ArtifactType.TRACE,
            data=b'{"trace": true}',
            media_type="application/json",
            document_id=DOCUMENT,
            run_id=RUN,
        )


def test_repeated_identical_write_is_idempotent_in_every_scope(
    store: LocalFileArtifactStore,
) -> None:
    first = platform_artifact(store)
    second = platform_artifact(store)
    assert first.sha256 == second.sha256
    assert first.created_at == second.created_at


def test_two_runs_can_store_the_same_bytes_under_different_ids(
    store: LocalFileArtifactStore,
) -> None:
    first = run_artifact(store, artifact_id="art_run_trace_0001")
    second = store.put(
        artifact_id="art_run_trace_0002",
        artifact_type=ArtifactType.TRACE,
        data=b'{"trace": true}',
        media_type="application/json",
        run_id=OTHER_RUN,
    )
    assert first.sha256 == second.sha256
    assert len(store.list_by_run(RUN)) == 1
    assert len(store.list_by_run(OTHER_RUN)) == 1


def test_corrupted_object_is_reported_as_integrity_error(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    metadata = platform_artifact(store)
    path = tmp_path / "artifacts" / "sha256" / metadata.sha256[:2] / "art_platform_report_0001.txt"
    path.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        store.get_by_id(metadata.artifact_id)
    assert store.scan().ok is False


def test_store_implements_the_port(store: LocalFileArtifactStore) -> None:
    assert isinstance(store, ArtifactStorePort)


def test_unsafe_id_error_is_a_scope_error(store: LocalFileArtifactStore) -> None:
    with pytest.raises(ArtifactScopeError):
        store.put(
            artifact_id="UPPERCASE",
            artifact_type=ArtifactType.TRACE,
            data=b"x",
            media_type="text/plain",
        )
