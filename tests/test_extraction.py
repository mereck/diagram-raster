import string

from hypothesis import given
from hypothesis import strategies as st

from diagram_raster.extraction import diagram_digest, extract_diagrams
from diagram_raster.models import DiagramLang

PROSE_LINE = st.text(alphabet=string.ascii_letters + " .,;:!?()'", max_size=40)
SOURCE_LINE = st.text(alphabet=string.ascii_letters + " []{}()|:;=-", max_size=30)
SOURCE = st.lists(SOURCE_LINE, min_size=1, max_size=8).filter(
    lambda lines: any(line.strip() for line in lines)
)
EXPLICIT_LANG = st.sampled_from(["mermaid", "d2", "dot"])
CODE_LANG = st.sampled_from(["python", "bash", "json", "sql", "yaml"])


def fenced(info: str, lines: list[str], indent: int = 0) -> list[str]:
    pad = " " * indent
    return [f"{pad}```{info}", *(pad + line for line in lines), f"{pad}```"]


def normalized(lines: list[str]) -> str:
    stripped = [line.rstrip() for line in lines]
    while stripped and not stripped[0]:
        stripped.pop(0)
    while stripped and not stripped[-1]:
        stripped.pop()
    return "\n".join(stripped)


def test_finds_each_supported_fence_kind() -> None:
    doc = "\n".join(
        [
            "# Plan",
            *fenced("mermaid", ["flowchart TD", "  A --> B"]),
            *fenced("d2", ["a -> b"]),
            *fenced("graphviz", ["digraph { a -> b }"]),
            *fenced("", ["+-----+     +-----+", "| api | --> | db  |", "+-----+     +-----+"]),
            *fenced("text", ["┌───┐", "│ x │", "└───┘"]),
        ]
    )
    langs = [d.lang for d in extract_diagrams(doc)]
    assert langs == [
        DiagramLang.MERMAID,
        DiagramLang.D2,
        DiagramLang.DOT,
        DiagramLang.ASCII,
        DiagramLang.ASCII,
    ]


def test_plain_block_without_drawing_is_not_a_diagram() -> None:
    doc = "\n".join(fenced("", ["uv run pytest", "make check"]))
    assert extract_diagrams(doc) == []


def test_reports_line_of_opening_fence() -> None:
    doc = "\n".join(["intro", "", *fenced("mermaid", ["graph TD", "A-->B"])])
    assert extract_diagrams(doc)[0].line == 3


@given(blocks=st.lists(st.tuples(st.lists(PROSE_LINE, max_size=3), EXPLICIT_LANG, SOURCE), max_size=5))
def test_extracts_exactly_the_diagram_blocks_in_order(
    blocks: list[tuple[list[str], str, list[str]]],
) -> None:
    doc: list[str] = []
    for prose, lang, source in blocks:
        doc += prose + fenced(lang, source)
    found = extract_diagrams("\n".join(doc))
    assert [(d.lang.value, d.source) for d in found] == [
        ("dot" if lang == "dot" else lang, normalized(source)) for _, lang, source in blocks
    ]


@given(lang=CODE_LANG, source=st.lists(st.sampled_from(["a --> b", "┌─┐", "x", "+--+"]), max_size=5))
def test_code_blocks_are_never_diagrams(lang: str, source: list[str]) -> None:
    assert extract_diagrams("\n".join(fenced(lang, source))) == []


@given(lang=EXPLICIT_LANG, source=SOURCE, trailing=st.text(alphabet=" \t", max_size=3))
def test_digest_ignores_trailing_whitespace_and_surrounding_blank_lines(
    lang: str, source: list[str], trailing: str
) -> None:
    padded = ["", *(line + trailing for line in source), "", ""]
    assert diagram_digest(DiagramLang(lang), "\n".join(padded)) == diagram_digest(
        DiagramLang(lang), "\n".join(source)
    )


@given(lang=EXPLICIT_LANG, source=SOURCE, indent=st.integers(min_value=1, max_value=8))
def test_indenting_the_whole_block_keeps_the_digest(lang: str, source: list[str], indent: int) -> None:
    """A diagram copied from a plan into a nested list item must still match its render."""
    flat = extract_diagrams("\n".join(fenced(lang, source)))
    nested = extract_diagrams("\n".join(["- step", *fenced(lang, source, indent)]))
    assert [d.digest for d in nested] == [d.digest for d in flat]


@given(lang=EXPLICIT_LANG, a=SOURCE, b=SOURCE)
def test_different_sources_get_different_digests(lang: str, a: list[str], b: list[str]) -> None:
    if normalized(a) != normalized(b):
        assert diagram_digest(DiagramLang(lang), "\n".join(a)) != diagram_digest(
            DiagramLang(lang), "\n".join(b)
        )
