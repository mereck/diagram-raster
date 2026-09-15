from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

BRIEFBACK_AGENT = "diagram-briefback"


class DiagramLang(StrEnum):
    MERMAID = "mermaid"
    D2 = "d2"
    DOT = "dot"
    ASCII = "ascii"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class Diagram:
    lang: DiagramLang
    source: str
    line: int
    digest: str


@dataclass(frozen=True)
class RenderResult:
    diagram: Diagram
    png: Path
    error: str | None = None


@dataclass(frozen=True)
class Briefback:
    at: str
    text: str


@dataclass(frozen=True)
class VerdictRecord:
    verdict: Verdict
    intent: str
    at: str
    briefback_count: int
