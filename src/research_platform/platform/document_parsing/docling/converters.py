"""Mapping between Docling objects and the platform's own document model.

Two rules drive this module:

1. **No Docling type crosses the boundary.**  Labels, coordinate origins and table frames
   are converted here into :class:`ElementType`, :class:`BBox` and plain cells.
2. **Coordinates are translated, never guessed.**  Docling reports bounding boxes with a
   bottom-left origin and ``t``/``b`` measured from the bottom of the page.  The platform
   stores PDF points with a top-left origin, so a box is converted with the page height; a
   box without a page height keeps its own system and is marked ``UNKNOWN`` instead of
   pretending to be normalised.
"""

from __future__ import annotations

from typing import Any, Final

from research_platform.domain.documents.elements import ElementType
from research_platform.domain.documents.provenance import (
    BBox,
    CoordinateOrigin,
    CoordinateUnit,
)

__all__ = [
    "DOCLING_LABEL_MAP",
    "element_type_for",
    "normalise_bbox",
    "table_cells_from_frame",
    "table_text_from_cells",
]

DOCLING_LABEL_MAP: Final[dict[str, ElementType]] = {
    "section_header": ElementType.HEADING,
    "title": ElementType.HEADING,
    "text": ElementType.PARAGRAPH,
    "paragraph": ElementType.PARAGRAPH,
    "list_item": ElementType.LIST_ITEM,
    "list": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "table_caption": ElementType.TABLE_CAPTION,
    "picture": ElementType.FIGURE,
    "figure": ElementType.FIGURE,
    "picture_caption": ElementType.FIGURE_CAPTION,
    "caption": ElementType.FIGURE_CAPTION,
    "footnote": ElementType.FOOTNOTE,
    "page_header": ElementType.HEADER,
    "page_footer": ElementType.FOOTER,
    "formula": ElementType.FORMULA,
    "code": ElementType.PARAGRAPH,
    "reference": ElementType.PARAGRAPH,
    "document_index": ElementType.PARAGRAPH,
}

_LIST_LABELS: Final = {"list_item"}


def element_type_for(label: object) -> ElementType:
    """Map a Docling label to a platform element type.

    Unknown labels become ``unknown`` and keep their raw label in element metadata, so a
    new Docling version cannot silently reclassify content.
    """
    raw = str(getattr(label, "value", label)).strip().lower()
    return DOCLING_LABEL_MAP.get(raw, ElementType.UNKNOWN)


def raw_label(label: object) -> str:
    """Raw lower-case label value, kept as metadata evidence."""
    return str(getattr(label, "value", label)).strip().lower()


def normalise_bbox(
    bbox: Any, *, page_height: float | None, page_width: float | None = None
) -> BBox | None:
    """Convert a Docling bbox (bottom-left origin) into top-left PDF points.

    Returns ``None`` when the item has no bounding box.  When the page height is unknown the
    coordinates are preserved as-is and explicitly marked as an unknown system: a caller can
    still see them, but cannot mistake them for normalised values.
    """
    if bbox is None:
        return None
    left = float(getattr(bbox, "l", 0.0))
    right = float(getattr(bbox, "r", 0.0))
    top = float(getattr(bbox, "t", 0.0))
    bottom = float(getattr(bbox, "b", 0.0))
    if right < left:
        left, right = right, left
    if top < bottom:
        top, bottom = bottom, top

    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if page_height is None:
        return BBox(
            x0=min(left, right),
            y0=min(top, bottom),
            x1=max(left, right),
            y1=max(top, bottom),
            unit=CoordinateUnit.UNKNOWN,
            origin=CoordinateOrigin.UNKNOWN,
            system=f"docling/{origin or 'unknown'}",
        )

    if "BOTTOM" in origin:
        y0 = page_height - top
        y1 = page_height - bottom
        y0, y1 = min(y0, y1), max(y0, y1)
    else:  # TOPLEFT or unspecified: coordinates already start at the top
        y0, y1 = min(top, bottom), max(top, bottom)
    return BBox(
        x0=min(left, right),
        y0=y0,
        x1=max(left, right),
        y1=y1,
        unit=CoordinateUnit.PT,
        origin=CoordinateOrigin.TOP_LEFT,
        page_width=page_width,
        page_height=page_height,
    )


def table_cells_from_frame(frame: Any) -> list[list[str]]:
    """Convert a table frame into plain string cells."""
    if frame is None:
        return []
    cells: list[list[str]] = []
    try:
        values = frame.astype(str).values.tolist()
    except Exception:
        return []
    for row in values:
        cells.append(["" if cell in {"nan", "None"} else str(cell) for cell in row])
    return cells


def table_text_from_cells(cells: list[list[str]]) -> str:
    """Deterministic pipe-separated rendering of a table.

    This is the searchable form; the cell matrix stays the authoritative structure, so a
    reader can always rebuild rows and columns.
    """
    if not cells:
        return ""
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in cells)
