"""Deterministic text decoding for ``.txt`` sources.

The order matters and is therefore explicit: BOM first (a BOM is a declaration, not a
guess), then strict UTF-8, then the two Russian legacy encodings, then the Western legacy
encoding.  A codec is accepted only if the whole file decodes strictly — "decode with
replacement" would silently destroy characters, which the platform must never do.

If several candidates decode successfully, the winner is chosen by the documented order,
not by a heuristic score: a deterministic rule can be explained and reproduced, a score
cannot be reviewed.

Undecodable bytes are reported as ``encoding_uncertain`` together with the number of
characters that had to be replaced; the caller decides what status the document gets.  The
original bytes are always the source of truth and are stored unchanged in any case.
"""

from __future__ import annotations

import codecs

from research_platform.application.ports.document_pipeline import DecodedText

__all__ = ["MAX_DECODED_CHARS", "TxtDecoder"]

MAX_DECODED_CHARS = 5_000_000
"""Upper bound on decoded characters (~10 MB of memory); larger inputs are refused."""

# (codec, label) in the documented order.  BOM handling is explicit below, so the plain
# UTF-8 entries here are only reached when no BOM is present.
_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("utf-8", "utf-8"),
    ("cp1251", "cp1251"),
    ("koi8-r", "koi8-r"),
    ("cp1252", "cp1252"),
)

_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig", "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16", "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16", "utf-16-be"),
)


class TxtDecoder:
    """Deterministic decoder implementing :class:`TextDecoderPort`."""

    order: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1251", "koi8-r", "cp1252")
    """Documented attempt order, exposed so the report can state it."""

    def decode(self, data: bytes) -> DecodedText:
        """Decode text bytes using the documented order.

        Raises:
            ValueError: the payload is empty or decodes to more than
                :data:`MAX_DECODED_CHARS` characters.
        """
        if not data:
            raise ValueError("text source is empty")

        for bom, codec, label in _BOMS:
            if data.startswith(bom):
                text = data.decode(codec)  # strict: a declared BOM must decode cleanly
                self._check_size(text)
                return DecodedText(
                    text=text,
                    encoding=label,
                    encoding_uncertain=False,
                    had_bom=True,
                    replacement_count=0,
                )

        if b"\x00" in data:
            for codec, label in (("utf-16-le", "utf-16-le"), ("utf-16-be", "utf-16-be")):
                try:
                    text = data.decode(codec)
                except UnicodeDecodeError:
                    continue
                if "\x00" in text:
                    continue
                self._check_size(text)
                return DecodedText(
                    text=text,
                    encoding=label,
                    encoding_uncertain=False,
                    had_bom=False,
                    replacement_count=0,
                    warnings=("UTF-16 without a BOM: the byte order was inferred from NUL parity",),
                )

        for codec, label in _CANDIDATES:
            try:
                text = data.decode(codec)
            except UnicodeDecodeError:
                continue
            self._check_size(text)
            return DecodedText(
                text=text,
                encoding=label,
                encoding_uncertain=False,
                had_bom=False,
                replacement_count=0,
            )

        # Nothing decoded strictly.  Keep the bytes, report the damage, do not pretend.
        text = data.decode("utf-8", errors="replace")
        self._check_size(text)
        replaced = text.count("\ufffd")
        return DecodedText(
            text=text,
            encoding="utf-8",
            encoding_uncertain=True,
            had_bom=False,
            replacement_count=replaced,
            warnings=(
                f"no candidate encoding decoded the file strictly; "
                f"{replaced} character(s) were replaced",
                f"attempt order: {', '.join(self.order)}",
            ),
        )

    @staticmethod
    def _check_size(text: str) -> None:
        if len(text) > MAX_DECODED_CHARS:
            raise ValueError(
                f"decoded text has {len(text)} characters, above the {MAX_DECODED_CHARS} limit"
            )
