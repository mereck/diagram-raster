import glob
import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from diagram_raster.ledger import Ledger
from diagram_raster.models import Diagram, DiagramLang, RenderResult

type Runner = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]]

MERMAID_CLI = "@mermaid-js/mermaid-cli@11"
RENDER_TIMEOUT_SECONDS = 120
SOURCE_SUFFIX = {
    DiagramLang.MERMAID: ".mmd",
    DiagramLang.D2: ".d2",
    DiagramLang.DOT: ".dot",
    DiagramLang.ASCII: ".txt",
}
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
PLAYWRIGHT_CHROME_GLOB = (
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)
ASCII_FONT_SIZE = 28
ASCII_PADDING = 24


class RenderError(Exception):
    pass


class DiagramRenderer(Protocol):
    def render(self, diagram: Diagram, out: Path) -> None: ...


def run_command(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=RENDER_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError as error:
        raise RenderError(f"{cmd[0]} is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise RenderError(f"{cmd[0]} timed out after {RENDER_TIMEOUT_SECONDS}s") from error


def find_chrome() -> Path | None:
    """Mermaid needs a browser; puppeteer's bundled download is skipped."""
    configured = os.environ.get("DIAGRAM_RASTER_CHROME")
    if configured:
        return Path(configured)
    playwright = sorted(glob.glob(os.path.expanduser(PLAYWRIGHT_CHROME_GLOB)), reverse=True)
    return next((Path(c) for c in [*CHROME_CANDIDATES, *playwright] if Path(c).is_file()), None)


def find_font() -> Path | None:
    configured = os.environ.get("DIAGRAM_RASTER_FONT")
    if configured:
        return Path(configured)
    return next((Path(c) for c in FONT_CANDIDATES if Path(c).is_file()), None)


@dataclass(frozen=True)
class Renderer:
    runner: Runner = run_command
    chrome: Path | None = field(default_factory=find_chrome)
    font: Path | None = field(default_factory=find_font)

    def render(self, diagram: Diagram, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        source = out.with_name("source" + SOURCE_SUFFIX[diagram.lang])
        source.write_text(diagram.source + "\n")
        if diagram.lang is DiagramLang.ASCII:
            self.render_ascii(diagram.source, out)
            return
        result = self.runner(
            self.command(diagram.lang, source, out), {**os.environ, "PUPPETEER_SKIP_DOWNLOAD": "1"}
        )
        if result.returncode != 0 or not out.exists():
            detail = (result.stderr or result.stdout).strip()[-800:] or f"exit {result.returncode}"
            raise RenderError(f"{diagram.lang} render failed: {detail}")

    def command(self, lang: DiagramLang, source: Path, out: Path) -> list[str]:
        match lang:
            case DiagramLang.MERMAID:
                config = out.with_name("puppeteer.json")
                settings: dict[str, object] = {"args": ["--no-sandbox"]}
                if self.chrome is not None:
                    settings["executablePath"] = str(self.chrome)
                config.write_text(json.dumps(settings))
                return [
                    "npx", "-y", MERMAID_CLI, "-i", str(source), "-o", str(out),
                    "-b", "white", "-s", "2", "-p", str(config),
                ]  # fmt: skip
            case DiagramLang.D2:
                return ["d2", "--pad", "20", str(source), str(out)]
            case DiagramLang.DOT:
                return ["dot", "-Tpng", "-Gdpi=144", str(source), "-o", str(out)]
        raise RenderError(f"no external renderer for {lang}")

    def render_ascii(self, source: str, out: Path) -> None:
        """Draw each character on a fixed grid so box-drawing columns stay aligned."""
        if self.font is None:
            raise RenderError("no monospace font found; set DIAGRAM_RASTER_FONT to a .ttf or .ttc path")
        font = ImageFont.truetype(str(self.font), ASCII_FONT_SIZE)
        ascent, descent = font.getmetrics()
        line_height = ascent + descent
        cell = font.getlength("M")
        lines = source.splitlines() or [""]
        width = round(max(len(line) for line in lines) * cell) + 2 * ASCII_PADDING
        image = Image.new("RGB", (width, line_height * len(lines) + 2 * ASCII_PADDING), "white")
        draw = ImageDraw.Draw(image)
        for row, line in enumerate(lines):
            for column, char in enumerate(line):
                if not char.isspace():
                    position = (ASCII_PADDING + column * cell, ASCII_PADDING + row * line_height)
                    draw.text(position, char, font=font, fill="black")
        image.save(out)


def ensure_rendered(
    diagrams: Iterable[Diagram], ledger: Ledger, renderer: DiagramRenderer, force: bool = False
) -> list[RenderResult]:
    results: list[RenderResult] = []
    for diagram in diagrams:
        png = ledger.png_path(diagram.digest)
        error = None
        if force or not png.exists():
            try:
                renderer.render(diagram, png)
            except RenderError as failure:
                error = str(failure)
        results.append(RenderResult(diagram=diagram, png=png, error=error))
    return results
