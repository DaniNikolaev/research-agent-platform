"""Local filesystem reader for user-supplied sources.

Untrusted input rules live here, in the adapter:

* only regular files are accepted — a directory, a device or a symbol link is refused;
* the size is checked from the file metadata *before* the bytes are loaded, so a huge file
  cannot exhaust memory;
* the file is opened read-only and read once;
* a fingerprint (size + mtime) lets the ingestion service detect that the file changed while
  it was being processed.

Symbol links are refused on purpose: following one would let a caller point the platform at
an arbitrary file outside the intended input, which is exactly the "bypass path" the
iteration brief forbids.
"""

from __future__ import annotations

from pathlib import Path

from research_platform.application.ports.document_pipeline import SourceFile
from research_platform.application.services.document_errors import DocumentIngestionError

__all__ = ["LocalSourceReader"]


class LocalSourceReader:
    """Implementation of :class:`SourceReaderPort` on the local filesystem."""

    def read(self, path: str, *, max_size_bytes: int) -> SourceFile:
        """Read one regular file.

        Raises:
            DocumentIngestionError: with a normalised code describing what is wrong.
        """
        candidate = Path(path)
        try:
            if candidate.is_symlink():
                raise DocumentIngestionError(
                    "source_not_readable",
                    f"the input {candidate.name!r} is a symbol link; provide a regular file",
                )
        except OSError as exc:  # pragma: no cover - defensive
            raise DocumentIngestionError(
                "source_not_readable", f"cannot inspect {candidate.name!r}: {exc}"
            ) from exc

        if not candidate.exists():
            raise DocumentIngestionError(
                "source_not_found", f"the input file {path!r} does not exist"
            )
        if not candidate.is_file():
            raise DocumentIngestionError(
                "source_not_readable", f"the input {path!r} is not a regular file"
            )

        try:
            stat = candidate.stat()
        except OSError as exc:
            raise DocumentIngestionError(
                "source_not_readable", f"cannot stat {path!r}: {exc}"
            ) from exc
        if stat.st_size <= 0:
            raise DocumentIngestionError("file_empty", "the input file is empty")
        if stat.st_size > max_size_bytes:
            raise DocumentIngestionError(
                "file_too_large",
                f"the input file is {stat.st_size} bytes, above the {max_size_bytes}-byte limit",
            )

        try:
            with candidate.open("rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise DocumentIngestionError(
                "source_not_readable", f"cannot read {path!r}: {exc}"
            ) from exc
        if not data:
            raise DocumentIngestionError("file_empty", "the input file is empty")
        return SourceFile(
            data=data,
            size_bytes=len(data),
            file_name=candidate.name,
            extension=candidate.suffix.lower(),
        )

    def fingerprint(self, path: str) -> tuple[int, int]:
        """``(size, mtime_ns)`` of the file, used for the post-copy integrity check."""
        stat = Path(path).stat()
        return stat.st_size, stat.st_mtime_ns
