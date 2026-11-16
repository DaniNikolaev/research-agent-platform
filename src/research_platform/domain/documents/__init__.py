"""Document contracts: identity, ingestion manifest, parsed content and quality."""

from __future__ import annotations

from research_platform.domain.documents.elements import (
    CANONICALIZATION_VERSION,
    DocumentElement,
    ElementType,
    FigureRecord,
    PageRecord,
    PageStatus,
    ParsedSection,
    ProvenanceSource,
    TableRecord,
)
from research_platform.domain.documents.identity import (
    DOCUMENT_ID_HEX_LENGTH,
    DocumentIdentity,
    DocumentSourceType,
    document_id_for,
)
from research_platform.domain.documents.models import (
    SOURCE_MANIFEST_CONTRACT_VERSION,
    DocumentLanguage,
    DocumentManifest,
    SourceDocumentManifest,
    SourceMediaType,
)
from research_platform.domain.documents.parsed import (
    PARSED_DOCUMENT_CONTRACT_VERSION,
    CanonicalTextInfo,
    ParsedDocument,
    ParserProvenance,
    ParsingStatus,
)
from research_platform.domain.documents.provenance import (
    BBox,
    CoordinateOrigin,
    CoordinateUnit,
)
from research_platform.domain.documents.quality import (
    QUALITY_POLICY_VERSION,
    CheckSeverity,
    CheckStatus,
    PageQualityRecord,
    PageQualityStatus,
    QualityCheckResult,
    QualityPolicy,
    QualityReport,
    QualityStatus,
)

__all__ = [
    "CANONICALIZATION_VERSION",
    "DOCUMENT_ID_HEX_LENGTH",
    "PARSED_DOCUMENT_CONTRACT_VERSION",
    "QUALITY_POLICY_VERSION",
    "SOURCE_MANIFEST_CONTRACT_VERSION",
    "BBox",
    "CanonicalTextInfo",
    "CheckSeverity",
    "CheckStatus",
    "CoordinateOrigin",
    "CoordinateUnit",
    "DocumentElement",
    "DocumentIdentity",
    "DocumentLanguage",
    "DocumentManifest",
    "DocumentSourceType",
    "ElementType",
    "FigureRecord",
    "PageQualityRecord",
    "PageQualityStatus",
    "PageRecord",
    "PageStatus",
    "ParsedDocument",
    "ParsedSection",
    "ParserProvenance",
    "ParsingStatus",
    "ProvenanceSource",
    "QualityCheckResult",
    "QualityPolicy",
    "QualityReport",
    "QualityStatus",
    "SourceDocumentManifest",
    "SourceMediaType",
    "TableRecord",
    "document_id_for",
]
