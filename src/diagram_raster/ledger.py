import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from diagram_raster.models import BRIEFBACK_AGENT, Briefback, Verdict, VerdictRecord

PNG_NAME = "render.png"
BRIEFBACKS_NAME = "briefbacks.jsonl"
VERDICT_NAME = "verdict.json"
# Digests come from diagram_digest (16 hex chars). Anything else, such as "../x",
# would name a directory outside the store.
DIGEST = re.compile(r"[0-9a-f]{1,64}")


def validate_digest(digest: str) -> str:
    if not DIGEST.fullmatch(digest):
        raise ValueError(f"invalid digest {digest!r}: expected lowercase hex")
    return digest


class NoBriefbackError(Exception):
    pass


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Ledger:
    """Content-addressed store: one directory per diagram digest.

    Holds the render, every clean-room brief-back captured for it, and the verdict.
    Editing a diagram changes its digest, so old verdicts never carry over.
    """

    root: Path

    def entry_dir(self, digest: str) -> Path:
        return self.root / validate_digest(digest)

    def png_path(self, digest: str) -> Path:
        return self.entry_dir(digest) / PNG_NAME

    def digest_for_png(self, path: str) -> str | None:
        try:
            candidate = Path(path).expanduser()
        except RuntimeError:  # "~someone" for a user that doesn't exist
            return None
        if candidate.name != PNG_NAME or not candidate.is_file():
            return None
        try:
            relative = candidate.resolve().relative_to(self.root.resolve())
        except ValueError:
            return None
        return relative.parts[0] if len(relative.parts) == 2 else None

    def add_briefback(self, digest: str, text: str) -> None:
        entry = self.entry_dir(digest)
        entry.mkdir(parents=True, exist_ok=True)
        with (entry / BRIEFBACKS_NAME).open("a") as log:
            log.write(json.dumps({"at": timestamp(), "text": text}) + "\n")

    def briefbacks(self, digest: str) -> list[Briefback]:
        log = self.entry_dir(digest) / BRIEFBACKS_NAME
        if not log.exists():
            return []
        return [Briefback(**json.loads(line)) for line in log.read_text().splitlines() if line.strip()]

    def record_verdict(self, digest: str, verdict: Verdict, intent: str) -> VerdictRecord:
        count = len(self.briefbacks(digest))
        if verdict is Verdict.PASS and count == 0:
            raise NoBriefbackError(
                f"No clean-room brief-back captured for {digest}. Spawn a fresh {BRIEFBACK_AGENT} "
                "agent whose prompt is only the PNG path, compare its description to your intent, "
                "then record the verdict."
            )
        record = VerdictRecord(verdict=verdict, intent=intent, at=timestamp(), briefback_count=count)
        entry = self.entry_dir(digest)
        entry.mkdir(parents=True, exist_ok=True)
        (entry / VERDICT_NAME).write_text(
            json.dumps(
                {"verdict": verdict.value, "intent": intent, "at": record.at, "briefback_count": count},
                indent=2,
            )
        )
        return record

    def verdict(self, digest: str) -> VerdictRecord | None:
        path = self.entry_dir(digest) / VERDICT_NAME
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return VerdictRecord(
            verdict=Verdict(data["verdict"]),
            intent=data["intent"],
            at=data["at"],
            briefback_count=data["briefback_count"],
        )


def default_ledger() -> Ledger:
    return Ledger(Path(os.environ.get("DIAGRAM_RASTER_HOME", "~/.cache/diagram-raster")).expanduser())
