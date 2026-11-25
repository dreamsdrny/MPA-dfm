"""Shared provenance, coverage and release contracts for all analysis services."""
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Confidence(str, Enum):
    UNKNOWN = "unknown"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"


class Verdict(str, Enum):
    FAIL = "fail"
    REVIEW = "review"
    PASS = "pass"


@dataclass(frozen=True)
class InputSource:
    path: str
    kind: str = "file"
    version: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class Evidence:
    field: str
    value: Any
    source: InputSource
    unit: str = ""
    confidence: Confidence = Confidence.UNKNOWN


@dataclass(frozen=True)
class Coverage:
    total: int
    evaluated: int
    missing: tuple[str, ...] = ()
    complete: bool = False

    def __post_init__(self):
        if self.total < 0 or not 0 <= self.evaluated <= self.total:
            raise ValueError("覆盖项数量无效")
        if self.complete and (not self.total or self.evaluated != self.total or self.missing):
            raise ValueError("存在缺失数据，不能声明完整覆盖")

    @property
    def ratio(self):
        return self.evaluated / self.total if self.total else 0.0


@dataclass(frozen=True)
class AnalysisSummary:
    service: str
    verdict: Verdict
    coverage: Coverage
    reasons: tuple[str, ...] = ()
    sources: tuple[InputSource, ...] = ()
    scope: str = "所选能力项；不代表完整制造放行"
    evidence: tuple[Evidence, ...] = ()

    @property
    def releasable(self):
        return self.verdict == Verdict.PASS and self.coverage.complete

    def to_dict(self):
        data = asdict(self)
        data.update(schema_version=1, verdict=self.verdict.value,
                    releasable=self.releasable)
        data["coverage"]["ratio"] = self.coverage.ratio
        return data


def release_verdict(*, failed=False, review=False, coverage: Coverage):
    """Failures dominate; missing/partial/empty coverage never grants release."""
    if failed:
        return Verdict.FAIL
    if review or not coverage.complete:
        return Verdict.REVIEW
    return Verdict.PASS
