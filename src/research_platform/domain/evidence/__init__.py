"""Evidence contracts.

``models`` holds contract **2.1** (current).  ``v2_0`` is a frozen snapshot of contract
**2.0** kept only so that already published packages keep validating; the aliases below
make the frozen shapes explicit wherever they are still needed.
"""

from __future__ import annotations

from research_platform.domain.evidence import v2_0
from research_platform.domain.evidence.models import (
    BoundingBox,
    ElementType,
    EvidenceCandidate,
    EvidenceCandidateList,
    EvidenceRecord,
    ExtractionMethod,
    MatchType,
    ResolutionStatus,
    TableRef,
    VerificationMethod,
)

EvidenceCandidateV20 = v2_0.EvidenceCandidate
EvidenceRecordV20 = v2_0.EvidenceRecord
MatchStatusV20 = v2_0.MatchStatus
VerificationMethodV20 = v2_0.VerificationMethod

__all__ = [
    "BoundingBox",
    "ElementType",
    "EvidenceCandidate",
    "EvidenceCandidateList",
    "EvidenceCandidateV20",
    "EvidenceRecord",
    "EvidenceRecordV20",
    "ExtractionMethod",
    "MatchStatusV20",
    "MatchType",
    "ResolutionStatus",
    "TableRef",
    "VerificationMethod",
    "VerificationMethodV20",
]
