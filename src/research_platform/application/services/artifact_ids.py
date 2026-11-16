"""Deterministic artefact identifiers for one document run.

An artefact id must be unique **per run**, not only per document: re-processing the same
document in a new run produces new artefacts with new URIs, while the original stays
untouched.  The id therefore mixes the document id (identity of the content) with the run id
(identity of the attempt), and stays short enough for the identifier contract.
"""

from __future__ import annotations

__all__ = ["run_artifact_id"]


def run_artifact_id(prefix: str, *, document_id: str, run_id: str) -> str:
    """Build ``<prefix>_<document-tail>_<run-tail>``.

    Raises:
        ValueError: the resulting identifier does not satisfy the platform identifier rules.
    """
    tail_document = document_id[-12:]
    last_segment = run_id.rsplit("_", 1)[-1]
    tail_run = last_segment if len(last_segment) >= 4 else run_id[-8:]
    candidate = f"{prefix}_{tail_document}_{tail_run}"
    if not candidate[0].isalnum() or len(candidate) > 64:
        raise ValueError(f"artifact id {candidate!r} is not a valid identifier")
    return candidate
