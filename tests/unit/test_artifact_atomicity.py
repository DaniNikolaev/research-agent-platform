"""Atomic sidecar publication, crash states and concurrency for the local artefact store.

The object file and the metadata sidecar are two files published one after another, so the
pair is *not* atomic.  These tests pin down the resulting states: which combinations are
visible, which are crash residue, and which are reported by ``scan`` — and that nothing is
repaired behind the operator's back.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from research_platform.application.ports.artifact_store import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactIssueKind,
    ArtifactMetadata,
    ArtifactPublishUnsupportedError,
)
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.platform.artifact_store import LocalFileArtifactStore
from research_platform.platform.artifact_store.metadata import (
    load_metadata,
    serialize_metadata,
)

RUN = "run_20261112_120000_abcdef"


@pytest.fixture
def store(tmp_path: Path) -> LocalFileArtifactStore:
    return LocalFileArtifactStore(root=tmp_path / "artifacts")


def put_artifact(
    store: LocalFileArtifactStore,
    *,
    data: bytes = b"payload",
    artifact_id: str = "art_run_trace_0001",
):
    return store.put(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.TRACE,
        data=data,
        media_type="application/json",
        run_id=RUN,
    )


def sidecar_path(store: LocalFileArtifactStore, artifact_id: str) -> Path:
    return store.root / "metadata" / f"{artifact_id}.json"


def object_path(store: LocalFileArtifactStore, metadata: ArtifactMetadata) -> Path:
    return store.root / "sha256" / metadata.sha256[:2] / f"{metadata.artifact_id}.json"


def staging_files(store: LocalFileArtifactStore) -> list[Path]:
    found = [path for path in (store.root / "tmp").glob("*") if path.is_file()]
    found += list((store.root / "metadata").glob("*.part"))
    return found


# ------------------------------------------------------------ sidecar mechanics


def test_sidecar_is_deterministic_json(store: LocalFileArtifactStore) -> None:
    metadata = put_artifact(store)
    raw = sidecar_path(store, metadata.artifact_id).read_bytes()
    text = raw.decode("utf-8")

    assert json.loads(text) == metadata.model_dump(mode="json")
    assert text.endswith("\n")
    assert serialize_metadata(metadata) == raw  # byte-stable re-serialization
    assert "\r" not in text


def test_sidecar_round_trips_through_the_model(store: LocalFileArtifactStore) -> None:
    metadata = put_artifact(store)
    loaded = load_metadata(sidecar_path(store, metadata.artifact_id))
    assert loaded == metadata


def test_no_staging_files_remain_after_a_successful_write(
    store: LocalFileArtifactStore,
) -> None:
    put_artifact(store)
    assert staging_files(store) == []


def test_oversized_metadata_is_refused_before_it_can_be_published(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size guard is defensive: a sidecar must stay a small record."""
    from research_platform.platform.artifact_store import metadata as metadata_module

    monkeypatch.setattr(metadata_module, "MAX_METADATA_BYTES", 200)
    with pytest.raises(ValueError) as excinfo:
        put_artifact(store)

    assert "above the 200-byte limit" in str(excinfo.value)
    assert sidecar_path(store, "art_run_trace_0001").exists() is False
    assert store.exists_id("art_run_trace_0001") is False
    assert staging_files(store) == []


def test_metadata_carrying_a_secret_is_rejected() -> None:
    with pytest.raises(ValueError):
        ArtifactMetadata.model_validate(
            {
                "artifact_id": "art_run_trace_0001",
                "artifact_type": "trace",
                "uri": f"artifact://runs/{RUN}/trace/art_run_trace_0001",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "media_type": "application/json",
                "run_id": RUN,
                "created_at": "2026-11-12T12:00:00Z",
                "storage_backend": "local_file",
                "metadata": {"api_key": "sk-live-abcdef0123456789"},
            }
        )


def test_metadata_must_not_carry_a_physical_path() -> None:
    with pytest.raises(ValueError):
        ArtifactMetadata.model_validate(
            {
                "artifact_id": "art_run_trace_0001",
                "artifact_type": "trace",
                "uri": "D:\\artifacts\\trace\\art_run_trace_0001",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "media_type": "application/json",
                "run_id": RUN,
                "created_at": "2026-11-12T12:00:00Z",
                "storage_backend": "local_file",
            }
        )


# ------------------------------------------------------------- fault injection


def test_scenario_a_failure_before_the_object_is_published(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise OSError("disk full while staging")

    monkeypatch.setattr(store, "_stage_bytes", boom)
    with pytest.raises(OSError):
        put_artifact(store)

    assert store.scan().objects_scanned == 0
    assert store.scan().metadata_scanned == 0
    assert staging_files(store) == []


def test_scenario_b_object_published_but_sidecar_missing(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_metadata: ArtifactMetadata) -> None:
        raise OSError("crash before the sidecar was published")

    monkeypatch.setattr(store, "_publish_metadata", boom)
    with pytest.raises(OSError):
        put_artifact(store)
    monkeypatch.undo()

    assert sidecar_path(store, "art_run_trace_0001").exists() is False
    assert store.exists_id("art_run_trace_0001") is False
    with pytest.raises(FileNotFoundError):
        store.get_by_id("art_run_trace_0001")
    assert ArtifactIssueKind.OBJECT_WITHOUT_METADATA in {
        issue.kind for issue in store.scan().issues
    }

    # an identical retry completes the pair instead of failing
    metadata = put_artifact(store)
    assert store.get_by_id(metadata.artifact_id) == b"payload"
    assert store.scan().ok is True


def test_scenario_b_retry_never_rewrites_the_existing_object(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_metadata: ArtifactMetadata) -> None:
        raise OSError("crash before the sidecar was published")

    monkeypatch.setattr(store, "_publish_metadata", boom)
    with pytest.raises(OSError):
        put_artifact(store)
    monkeypatch.undo()

    orphan = next((store.root / "sha256").rglob("*.json"))
    before = (orphan.stat().st_mtime_ns, orphan.read_bytes())

    metadata = put_artifact(store)
    assert metadata.sha256
    assert (orphan.stat().st_mtime_ns, orphan.read_bytes()) == before


def test_scenario_c_failure_while_staging_the_sidecar(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = store._stage_bytes

    def failing_stage(directory: Path, data: bytes, *, prefix: str) -> Path:
        if directory == store.root / "metadata":
            raise OSError("disk full while staging metadata")
        return original(directory, data, prefix=prefix)

    monkeypatch.setattr(store, "_stage_bytes", failing_stage)
    with pytest.raises(OSError):
        put_artifact(store)

    assert sidecar_path(store, "art_run_trace_0001").exists() is False
    assert store.exists_id("art_run_trace_0001") is False
    assert staging_files(store) == []
    objects = [path for path in (store.root / "sha256").rglob("*") if path.is_file()]
    assert len(objects) == 1  # the object is intact, just invisible


def test_scenario_c_corrupt_staging_sidecar_is_not_published(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = store._verify_sidecar

    def failing_verify(path: Path, expected: ArtifactMetadata) -> None:
        calls["count"] += 1
        if calls["count"] == 1:  # the staging read-back fails
            raise ArtifactIntegrityError("staged sidecar does not parse")
        original(path, expected)

    monkeypatch.setattr(store, "_verify_sidecar", failing_verify)
    with pytest.raises(ArtifactIntegrityError):
        put_artifact(store)

    assert sidecar_path(store, "art_run_trace_0001").exists() is False


def test_scenario_c_post_publish_verification_failure_removes_the_sidecar(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = store._verify_sidecar

    def failing_verify(path: Path, expected: ArtifactMetadata) -> None:
        calls["count"] += 1
        original(path, expected)
        if calls["count"] == 2:  # verification of the published file fails
            raise ArtifactIntegrityError("published sidecar is unreadable")

    monkeypatch.setattr(store, "_verify_sidecar", failing_verify)
    with pytest.raises(ArtifactIntegrityError):
        put_artifact(store)

    assert sidecar_path(store, "art_run_trace_0001").exists() is False
    assert store.exists_id("art_run_trace_0001") is False
    assert staging_files(store) == []


def test_scenario_d_published_artefact_is_idempotent_and_protected(
    store: LocalFileArtifactStore,
) -> None:
    first = put_artifact(store)
    second = put_artifact(store)
    assert first == second

    with pytest.raises(ArtifactConflictError):
        put_artifact(store, data=b"other payload")
    assert store.get_by_id(first.artifact_id) == b"payload"
    assert load_metadata(sidecar_path(store, first.artifact_id)) == first


def test_scenario_e_corrupt_sidecar_is_reported_not_repaired(
    store: LocalFileArtifactStore,
) -> None:
    metadata = put_artifact(store)
    path = sidecar_path(store, metadata.artifact_id)
    path.write_bytes(b"{ this is not json")

    with pytest.raises(ArtifactIntegrityError):
        store.get_by_id(metadata.artifact_id)
    assert path.read_bytes() == b"{ this is not json"  # nothing was "fixed"

    report = store.scan()
    assert ArtifactIssueKind.CORRUPT_METADATA in {issue.kind for issue in report.issues}
    assert report.ok is False


def test_scenario_e_sidecar_without_object_is_reported(
    store: LocalFileArtifactStore,
) -> None:
    metadata = put_artifact(store)
    object_path(store, metadata).unlink()

    with pytest.raises(ArtifactIntegrityError):
        store.get_by_id(metadata.artifact_id)
    report = store.scan()
    assert ArtifactIssueKind.METADATA_WITHOUT_OBJECT in {issue.kind for issue in report.issues}


def test_scenario_e_tampered_sidecar_hash_is_detected(
    store: LocalFileArtifactStore,
) -> None:
    metadata = put_artifact(store)
    path = sidecar_path(store, metadata.artifact_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sha256"] = "b" * 64
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        store.get_by_id(metadata.artifact_id)
    report = store.scan()
    kinds = {issue.kind for issue in report.issues}
    assert kinds & {
        ArtifactIssueKind.SHA_MISMATCH,
        ArtifactIssueKind.METADATA_WITHOUT_OBJECT,
        ArtifactIssueKind.OBJECT_WITHOUT_METADATA,
    }


def test_scan_reports_a_consistent_store(store: LocalFileArtifactStore) -> None:
    put_artifact(store)
    report = store.scan()
    assert report.ok is True
    assert report.objects_scanned == 1
    assert report.metadata_scanned == 1


# ---------------------------------------------------------------- concurrency


def test_scenario_f_concurrent_identical_writes_publish_one_artifact(
    store: LocalFileArtifactStore,
) -> None:
    barrier = threading.Barrier(4)
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            metadata = put_artifact(store)
            with lock:
                results.append(metadata)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len({item.artifact_id for item in results}) == 1  # type: ignore[attr-defined]
    assert store.get_by_id("art_run_trace_0001") == b"payload"
    assert store.scan().ok is True

    objects = [path for path in (store.root / "sha256").rglob("*") if path.is_file()]
    assert len(objects) == 1


def test_scenario_f_concurrent_conflicting_writes_have_one_winner(
    store: LocalFileArtifactStore,
) -> None:
    barrier = threading.Barrier(2)
    payloads = {b"first payload", b"second payload"}
    successes: list[bytes] = []
    conflicts: list[BaseException] = []
    lock = threading.Lock()

    def worker(data: bytes) -> None:
        barrier.wait()
        try:
            put_artifact(store, data=data)
            with lock:
                successes.append(data)
        except BaseException as exc:
            with lock:
                conflicts.append(exc)

    threads = [threading.Thread(target=worker, args=(data,)) for data in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], ArtifactConflictError)
    assert store.get_by_id("art_run_trace_0001") == successes[0]
    assert store.scan().ok is True


# ------------------------------------------------------- unsupported filesystem


def test_metadata_publication_fails_closed_without_hard_links(
    store: LocalFileArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem without hard links must fail the sidecar write, not write in place."""
    real_link = os.link

    def no_links(source: object, destination: object) -> None:
        if str(destination).endswith(".json"):
            raise OSError("hard links are not supported here")
        real_link(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(ArtifactPublishUnsupportedError):
        put_artifact(store)

    assert sidecar_path(store, "art_run_trace_0001").exists() is False
    assert store.exists_id("art_run_trace_0001") is False
