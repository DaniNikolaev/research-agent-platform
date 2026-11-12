"""Local file artifact store: atomicity, immutability, integrity, path safety, listing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from research_platform.application.ports.artifact_store import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactStorePort,
)
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactType,
    build_artifact_uri,
)
from research_platform.platform.artifact_store import LocalFileArtifactStore

DOCUMENT = "doc_alpha_0001"
RUN = "run_20261112_120000_abcdef"


@pytest.fixture
def store(tmp_path: Path) -> LocalFileArtifactStore:
    return LocalFileArtifactStore(root=tmp_path / "artifacts")


def put(
    store: LocalFileArtifactStore,
    *,
    artifact_id: str = "art_doc_source_0001",
    data: bytes = b"%PDF-1.7 fake",
    **kwargs: object,
):
    return store.put(
        artifact_id=artifact_id,
        artifact_type=kwargs.get("artifact_type", ArtifactType.ORIGINAL_DOCUMENT),  # type: ignore[arg-type]
        data=data,
        media_type=kwargs.get("media_type", "application/pdf"),  # type: ignore[arg-type]
        document_id=kwargs.get("document_id", DOCUMENT),  # type: ignore[arg-type]
        run_id=kwargs.get("run_id", RUN),  # type: ignore[arg-type]
        schema_version=kwargs.get("schema_version"),  # type: ignore[arg-type]
    )


def reference(metadata) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=metadata.artifact_id,
        artifact_type=metadata.artifact_type,
        uri=metadata.uri,
        media_type=metadata.media_type,
        size_bytes=metadata.size_bytes,
        sha256=metadata.sha256,
        version="1",
        created_at=metadata.created_at,
    )


# ----------------------------------------------------------------------- basics


def test_store_implements_the_port(store: LocalFileArtifactStore) -> None:
    assert isinstance(store, ArtifactStorePort)
    assert store.backend_name == "local_file"


def test_put_stores_bytes_and_metadata(store: LocalFileArtifactStore) -> None:
    payload = b"%PDF-1.7 fake"
    metadata = put(store, data=payload)

    assert metadata.size_bytes == len(payload)
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()
    assert metadata.run_id == RUN
    assert metadata.document_id == DOCUMENT
    assert metadata.storage_backend == "local_file"
    assert metadata.uri == build_artifact_uri(
        document_id=DOCUMENT,
        run_id=RUN,
        artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
        artifact_id="art_doc_source_0001",
    )

    stored = store.get(reference(metadata))
    assert stored == payload
    assert store.exists(reference(metadata)) is True
    assert store.verify_checksum(reference(metadata)) is True
    assert store.get_metadata(reference(metadata)).sha256 == metadata.sha256


def test_object_lives_under_the_content_addressed_layout(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    metadata = put(store)
    expected = tmp_path / "artifacts" / "sha256" / metadata.sha256[:2] / "art_doc_source_0001.pdf"
    assert expected.exists()
    assert (tmp_path / "artifacts" / "metadata" / "art_doc_source_0001.json").exists()


def test_empty_file_is_a_valid_artifact(store: LocalFileArtifactStore) -> None:
    metadata = put(store, data=b"", media_type="text/plain")
    assert metadata.size_bytes == 0
    assert store.get(reference(metadata)) == b""


def test_unicode_content_round_trips_byte_exactly(store: LocalFileArtifactStore) -> None:
    payload = "Ключевые слова: нейросеть; 中文; émoji 🚀\n".encode()
    metadata = put(store, data=payload, media_type="text/plain")
    assert store.get(reference(metadata)) == payload
    assert store.verify_checksum(reference(metadata)) is True


def test_same_content_is_idempotent(store: LocalFileArtifactStore) -> None:
    first = put(store, data=b"same bytes")
    second = put(store, data=b"same bytes")
    assert first.sha256 == second.sha256
    assert first.created_at == second.created_at


# ------------------------------------------------------------------ immutability


def test_different_content_under_same_id_is_a_conflict(store: LocalFileArtifactStore) -> None:
    original = put(store, data=b"original")
    with pytest.raises(ArtifactConflictError):
        put(store, data=b"replacement")

    # the original is untouched
    assert store.get(reference(original)) == b"original"
    assert store.verify_checksum(reference(original)) is True


def test_conflict_does_not_leave_a_staging_file(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    put(store, data=b"original")
    with pytest.raises(ArtifactConflictError):
        put(store, data=b"replacement")
    staging = list((tmp_path / "artifacts" / "tmp").glob("*"))
    assert staging == []


def test_conflict_does_not_publish_a_second_object(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    put(store, data=b"original")
    with pytest.raises(ArtifactConflictError):
        put(store, data=b"replacement")
    objects = [path for path in (tmp_path / "artifacts" / "sha256").rglob("*") if path.is_file()]
    assert len(objects) == 1


# -------------------------------------------------------------------- integrity


def test_corrupted_object_is_detected(store: LocalFileArtifactStore, tmp_path: Path) -> None:
    metadata = put(store, data=b"payload")
    reference_ = reference(metadata)
    path = tmp_path / "artifacts" / "sha256" / metadata.sha256[:2] / "art_doc_source_0001.pdf"
    path.write_bytes(b"corrupted")

    assert store.verify_checksum(reference_) is False
    with pytest.raises(ArtifactIntegrityError):
        store.get(reference_)


def test_orphan_object_is_adopted_when_the_content_matches(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    """A crash between publishing the object and writing metadata must not lose the artefact."""
    payload = b"payload written before the crash"
    first = put(store, data=payload)
    (tmp_path / "artifacts" / "metadata" / "art_doc_source_0001.json").unlink()

    adopted = put(store, data=payload)
    assert adopted.sha256 == first.sha256
    assert store.get(reference(adopted)) == payload
    assert store.get_metadata(reference(adopted)).sha256 == first.sha256
    assert len(store.list_by_run(RUN)) == 1


def test_orphan_object_with_other_content_still_conflicts(
    store: LocalFileArtifactStore, tmp_path: Path
) -> None:
    put(store, data=b"first content")
    (tmp_path / "artifacts" / "metadata" / "art_doc_source_0001.json").unlink()

    with pytest.raises(ArtifactConflictError):
        put(store, data=b"second content")
    assert store.list_by_run(RUN) == []


def test_reference_with_a_foreign_checksum_is_an_integrity_error(
    store: LocalFileArtifactStore,
) -> None:
    metadata = put(store)
    reference_ = reference(metadata)
    assert store.exists(reference_) is True
    foreign = ArtifactReference(
        artifact_id=reference_.artifact_id,
        artifact_type=reference_.artifact_type,
        uri=reference_.uri,
        media_type=reference_.media_type,
        size_bytes=reference_.size_bytes,
        sha256="0" * 64,
        version="1",
        created_at=reference_.created_at,
    )
    with pytest.raises(ArtifactIntegrityError):
        store.get(foreign)
    assert store.exists(foreign) is False
    assert store.verify_checksum(foreign) is False


def test_unknown_artefact_is_not_found(store: LocalFileArtifactStore) -> None:
    assert store.exists_id("art_does_not_exist_0001") is False
    with pytest.raises(FileNotFoundError):
        store.get_by_id("art_does_not_exist_0001")
    with pytest.raises(FileNotFoundError):
        store.get_metadata_by_id("art_does_not_exist_0001")


def test_metadata_lookup_is_available_separately(store: LocalFileArtifactStore) -> None:
    metadata = put(store, data=b"x", schema_version="2.1")
    loaded = store.get_metadata(reference(metadata))
    assert loaded.schema_version == "2.1"
    assert loaded.artifact_type is ArtifactType.ORIGINAL_DOCUMENT


# --------------------------------------------------------------------- path safety


@pytest.mark.parametrize(
    "artifact_id",
    [
        "../escape",
        "..\\escape",
        "art/../../escape",
        "C:\\absolute\\path",
        "/absolute/path",
        "art id with spaces",
        "art.id.with.dots",
        "арт_идентификатор",
        "UPPERCASE",
        "a",
        "x" * 200,
    ],
)
def test_unsafe_artifact_ids_are_rejected(store: LocalFileArtifactStore, artifact_id: str) -> None:
    with pytest.raises(ValueError):
        put(store, artifact_id=artifact_id)


def test_traversal_cannot_escape_the_root(store: LocalFileArtifactStore, tmp_path: Path) -> None:
    from research_platform.platform.artifact_store.paths import ArtifactStorePaths

    paths = ArtifactStorePaths(root=tmp_path / "artifacts")
    with pytest.raises(ValueError):
        paths.contained(tmp_path / "artifacts" / ".." / "outside.txt")


def test_media_type_maps_to_a_safe_extension(store: LocalFileArtifactStore, tmp_path: Path) -> None:
    json_meta = put(store, artifact_id="art_trace_0001", data=b"{}", media_type="application/json")
    put(store, artifact_id="art_trace_0002", data=b"hi", media_type="text/plain")
    weird = put(store, artifact_id="art_trace_0003", data=b"??", media_type="application/x-weird")

    assert json_meta.uri.endswith("/art_trace_0001")
    assert (tmp_path / "artifacts" / "sha256").rglob("art_trace_0001.json")
    assert list((tmp_path / "artifacts" / "sha256").rglob("art_trace_0002.txt"))
    assert list((tmp_path / "artifacts" / "sha256").rglob("art_trace_0003.bin"))
    assert weird.media_type == "application/x-weird"


# ------------------------------------------------------------------- enumerating


def test_listing_by_run_and_document(store: LocalFileArtifactStore) -> None:
    put(store, artifact_id="art_one_0001", data=b"one")
    put(store, artifact_id="art_two_0002", data=b"two")
    put(
        store,
        artifact_id="art_three_0003",
        data=b"three",
        run_id="run_20261112_130000_bbbbbb",
        media_type="text/plain",
    )

    assert sorted(item.artifact_id for item in store.list_by_run(RUN)) == [
        "art_one_0001",
        "art_two_0002",
    ]
    assert [item.artifact_id for item in store.list_by_run("run_20261112_130000_bbbbbb")] == [
        "art_three_0003"
    ]
    assert len(store.list_by_document(DOCUMENT)) == 3
    assert store.list_by_run("run_20990101_000000_ffffff") == []


def test_listing_is_ordered_by_creation(store: LocalFileArtifactStore) -> None:
    first = put(store, artifact_id="art_a_0001", data=b"a")
    second = put(store, artifact_id="art_b_0002", data=b"b")
    items = store.list_by_run(RUN)
    assert [item.artifact_id for item in items] == [first.artifact_id, second.artifact_id]


# ------------------------------------------------------------------------ health


def test_check_writable_is_true_for_a_usable_root(store: LocalFileArtifactStore) -> None:
    assert store.check_writable() is True


def test_check_writable_leaves_no_probe_file(store: LocalFileArtifactStore) -> None:
    store.check_writable()
    assert list(store.root.joinpath("tmp").iterdir()) == []


def test_closed_store_refuses_operations(tmp_path: Path) -> None:
    store = LocalFileArtifactStore(root=tmp_path / "artifacts")
    metadata = put(store)
    store.close()
    with pytest.raises(RuntimeError):
        store.get(reference(metadata))


def test_missing_document_or_run_id_is_rejected(store: LocalFileArtifactStore) -> None:
    with pytest.raises(ValueError):
        store.put(
            artifact_id="art_doc_source_0001",
            artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
            data=b"x",
            media_type="application/pdf",
            document_id="",
            run_id=RUN,
        )
    with pytest.raises(ValueError):
        store.put(
            artifact_id="art_doc_source_0001",
            artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
            data=b"x",
            media_type="application/pdf",
            document_id=DOCUMENT,
            run_id="../escape",
        )
