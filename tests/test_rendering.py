import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from diagram_raster.extraction import diagram_digest
from diagram_raster.ledger import Ledger
from diagram_raster.models import Diagram, DiagramLang
from diagram_raster.rendering import (
    Renderer,
    RenderError,
    ensure_rendered,
    find_font,
)
from tests.conftest import StubRenderer


def make(lang: DiagramLang, source: str) -> Diagram:
    return Diagram(lang=lang, source=source, line=1, digest=diagram_digest(lang, source))


class FakeRunner:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self.returncode == 0:
            out = Path(cmd[cmd.index("-o") + 1]) if "-o" in cmd else Path(cmd[-1])
            Image.new("RGB", (10, 10), "white").save(out)
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


@pytest.mark.parametrize(
    ("lang", "tool", "suffix"),
    [
        (DiagramLang.MERMAID, "@mermaid-js/mermaid-cli@11.17.1", ".mmd"),
        (DiagramLang.D2, "d2", ".d2"),
        (DiagramLang.DOT, "dot", ".dot"),
    ],
)
def test_external_renderers_get_the_source_file_and_output_path(
    tmp_path: Path, lang: DiagramLang, tool: str, suffix: str
) -> None:
    runner = FakeRunner()
    out = tmp_path / "abc" / "render.png"
    Renderer(runner=runner, chrome=None).render(make(lang, "a -> b"), out)
    [cmd] = runner.calls
    assert tool in cmd
    assert str(out) in cmd
    assert str(out.with_name(f"source{suffix}")) in cmd
    assert out.with_name(f"source{suffix}").read_text() == "a -> b\n"


def test_mermaid_uses_configured_chrome(tmp_path: Path) -> None:
    runner = FakeRunner()
    chrome = tmp_path / "chrome"
    out = tmp_path / "abc" / "render.png"
    Renderer(runner=runner, chrome=chrome).render(make(DiagramLang.MERMAID, "graph TD"), out)
    config = out.with_name("puppeteer.json").read_text()
    assert str(chrome) in config


def test_failed_render_raises_with_tool_stderr(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=1, stderr="Parse error on line 2")
    with pytest.raises(RenderError, match="Parse error on line 2"):
        Renderer(runner=runner, chrome=None).render(
            make(DiagramLang.MERMAID, "graph TD\nA-->"), tmp_path / "x" / "render.png"
        )


def test_ascii_render_width_follows_longest_line(tmp_path: Path) -> None:
    renderer = Renderer(runner=FakeRunner(), chrome=None, font=find_font())
    short, long = tmp_path / "s" / "render.png", tmp_path / "l" / "render.png"
    renderer.render(make(DiagramLang.ASCII, "+--+\n|a |\n+--+"), short)
    renderer.render(make(DiagramLang.ASCII, "+" + "-" * 40 + "+"), long)
    short_w, short_h = Image.open(short).size
    long_w, long_h = Image.open(long).size
    assert long_w > short_w * 5
    assert short_h > long_h


def test_ensure_rendered_skips_existing_renders(ledger: Ledger, renderer: StubRenderer) -> None:
    diagram = make(DiagramLang.D2, "a -> b")
    ensure_rendered([diagram], ledger, renderer)
    ensure_rendered([diagram], ledger, renderer)
    assert renderer.rendered == [diagram]


def test_ensure_rendered_reports_errors_per_diagram(ledger: Ledger) -> None:
    class Failing:
        def render(self, diagram: Diagram, out: Path) -> None:
            raise RenderError("boom")

    [result] = ensure_rendered([make(DiagramLang.D2, "a")], ledger, Failing())
    assert result.error == "boom"


@settings(max_examples=25, deadline=None)
@given(
    top=st.sampled_from("┌┐┬│"),
    middle=st.lists(st.sampled_from("│┼├┤"), max_size=4),
    bottom=st.sampled_from("└┘┴│"),
)
def test_ascii_vertical_strokes_meet_between_rows(top: str, middle: list[str], bottom: str) -> None:
    """Box sides stacked on consecutive rows must touch, or the reader sees a dashed line."""
    column = [top, *middle, bottom]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "render.png"
        Renderer(runner=FakeRunner(), chrome=None, font=find_font()).render(
            make(DiagramLang.ASCII, "\n".join(column)), out
        )
        image = Image.open(out).convert("L")
    width, height = image.size
    pixels = image.tobytes()
    darkest = max(range(width), key=lambda x: sum(pixels[y * width + x] < 128 for y in range(height)))
    dark_rows = [y for y in range(height) if pixels[y * width + darkest] < 128]
    assert dark_rows == list(range(dark_rows[0], dark_rows[-1] + 1))
