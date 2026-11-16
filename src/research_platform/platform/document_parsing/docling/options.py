"""Docling pipeline options: explicit, hashable, reproducible.

Every option that can change the extraction result is declared here and hashed into
``parser_options_hash``, which is stored in the parsed document.  Two parses with the same
hash were produced under the same settings.

OCR stays **off**: the OCR engine is not chosen by the owner (``OI-008``), so the pipeline
must not silently enable one.  Table structure extraction is on, because tables are part of
the document content the platform must keep.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Final

__all__ = ["DOCLING_PIPELINE_VERSION", "DoclingParserOptions"]

DOCLING_PIPELINE_VERSION: Final = "standard-pdf/1"


@dataclass(frozen=True, slots=True)
class DoclingParserOptions:
    """Settings of the standard PDF pipeline."""

    do_ocr: bool = False
    """Always false in this iteration: no OCR engine is approved (OI-008)."""

    do_table_structure: bool = True
    generate_picture_images: bool = False
    """Picture pixels are not rendered: no image content is produced or interpreted."""

    do_cell_matching: bool = True
    images_scale: float = 1.0
    force_full_page_ocr: bool = False
    layout_model: str = "docling-layout-heron"
    table_model: str = "tableformer"
    backend: str = "docling-parse"

    def as_dict(self) -> dict[str, Any]:
        """Deterministic mapping of the effective options."""
        return asdict(self)

    @property
    def options_hash(self) -> str:
        """Stable hash of the effective options (16 hex characters)."""
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_pipeline_options(self) -> Any:
        """Build the Docling ``PdfPipelineOptions`` object (imported lazily)."""
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        options = PdfPipelineOptions()
        options.do_ocr = self.do_ocr
        options.do_table_structure = self.do_table_structure
        options.generate_picture_images = self.generate_picture_images
        options.images_scale = self.images_scale
        return options
