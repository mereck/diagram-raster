import hashlib
import re

from diagram_raster.models import Diagram, DiagramLang

OPENING_FENCE = re.compile(r"^(?P<indent> *)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
LANG_ALIASES = {
    "mermaid": DiagramLang.MERMAID,
    "mmd": DiagramLang.MERMAID,
    "d2": DiagramLang.D2,
    "dot": DiagramLang.DOT,
    "graphviz": DiagramLang.DOT,
    "gv": DiagramLang.DOT,
    "ascii": DiagramLang.ASCII,
    "diagram": DiagramLang.ASCII,
}
PLAIN_INFO = {"", "text", "txt", "plain", "plaintext"}
# Box drawing, arrows, and the filled triangles people use as arrowheads.
DRAWING_CHARS = re.compile(r"[─-╿←-⇿▲-◄]")
ASCII_STROKES = re.compile(r"\+-{2,}|-{2,}\+|-+>|<-+|=+>")


def normalize_source(source: str) -> str:
    lines = [line.rstrip() for line in source.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def diagram_digest(lang: DiagramLang, source: str) -> str:
    payload = f"{lang}\n{normalize_source(source)}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def looks_like_ascii_diagram(source: str) -> bool:
    return bool(DRAWING_CHARS.search(source) or ASCII_STROKES.search(source))


def classify(info: str, source: str) -> DiagramLang | None:
    words = info.strip().lower().split()
    word = words[0] if words else ""
    if word in LANG_ALIASES:
        return LANG_ALIASES[word]
    if word in PLAIN_INFO and looks_like_ascii_diagram(source):
        return DiagramLang.ASCII
    return None


def dedent_fence_line(line: str, indent: int) -> str:
    leading = len(line) - len(line.lstrip(" "))
    return line[min(indent, leading) :]


def extract_diagrams(markdown: str) -> list[Diagram]:
    """Fenced blocks that hold diagrams, in document order.

    Fences may be indented any amount (plans nest them in list items); content is
    dedented by the fence's indent so a copied diagram keeps its digest.
    """
    lines = markdown.splitlines()
    diagrams: list[Diagram] = []
    i = 0
    while i < len(lines):
        opening = OPENING_FENCE.match(lines[i])
        if opening is None or (opening["fence"][0] == "`" and "`" in opening["info"]):
            i += 1
            continue
        fence = opening["fence"]
        closing = re.compile(rf"^ *{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        body: list[str] = []
        j = i + 1
        while j < len(lines) and not closing.match(lines[j]):
            body.append(dedent_fence_line(lines[j], len(opening["indent"])))
            j += 1
        source = normalize_source("\n".join(body))
        lang = classify(opening["info"], source)
        if lang is not None and source:
            diagrams.append(
                Diagram(lang=lang, source=source, line=i + 1, digest=diagram_digest(lang, source))
            )
        i = j + 1
    return diagrams
