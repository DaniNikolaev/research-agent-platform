"""Geometric provenance of extracted elements.

Coordinates are a *claim about the source*, so they are typed: a rectangle without a unit,
an origin and the page extent it belongs to cannot be compared, rendered or trusted.  A
parser that cannot express its coordinate system declares ``unit=None`` and keeps its own
system, instead of passing an unknown frame off as normalised PDF points.

Conventions for normalised values (the configuration default):

* origin at the **top-left** corner of the page;
* units in **PDF points** (1/72 inch), page width and height in the same units;
* the frame follows the page rotation as rendered, so a rotated page reports its visible
  width and height.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from research_platform.domain.base import ContractModel, NonEmptyStr

__all__ = ["BBox", "CoordinateOrigin", "CoordinateUnit"]


class CoordinateUnit(StrEnum):
    """Unit of a bounding box."""

    PT = "pt"
    PX = "px"
    UNKNOWN = "unknown"


class CoordinateOrigin(StrEnum):
    """Where the coordinate origin sits."""

    TOP_LEFT = "top_left"
    BOTTOM_LEFT = "bottom_left"
    UNKNOWN = "unknown"


class BBox(ContractModel):
    """Rectangle of an element inside one physical page."""

    x0: float
    y0: float
    x1: float
    y1: float
    unit: CoordinateUnit = CoordinateUnit.UNKNOWN
    origin: CoordinateOrigin = CoordinateOrigin.UNKNOWN
    page_width: float | None = Field(default=None, gt=0)
    page_height: float | None = Field(default=None, gt=0)
    system: NonEmptyStr | None = None
    """Name of the parser coordinate system when ``unit`` is unknown."""

    @model_validator(mode="after")
    def _rectangle_is_ordered(self) -> BBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(
                f"bbox must satisfy x0 <= x1 and y0 <= y1, got "
                f"({self.x0}, {self.y0}, {self.x1}, {self.y1})"
            )
        return self

    @model_validator(mode="after")
    def _declared_unit_brings_a_frame(self) -> BBox:
        if self.unit is not CoordinateUnit.UNKNOWN and self.page_width is None:
            raise ValueError(
                "a bbox with a declared unit must also declare page_width/page_height, "
                "otherwise the coordinates cannot be placed on a page"
            )
        return self

    @model_validator(mode="after")
    def _fits_the_page_within_tolerance(self) -> BBox:
        if self.page_width is None or self.page_height is None:
            return self
        tolerance = 2.0
        if (
            self.x1 > self.page_width + tolerance
            or self.y1 > self.page_height + tolerance
            or self.x0 < -tolerance
            or self.y0 < -tolerance
        ):
            raise ValueError(
                f"bbox ({self.x0}, {self.y0}, {self.x1}, {self.y1}) lies outside the "
                f"{self.page_width}x{self.page_height} page"
            )
        return self

    @property
    def width(self) -> float:
        """Width of the rectangle."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Height of the rectangle."""
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        """Area of the rectangle (negative values are impossible by validation)."""
        return max(0.0, self.width) * max(0.0, self.height)
