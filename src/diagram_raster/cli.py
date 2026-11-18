import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from PIL import Image

from diagram_raster.extraction import diagram_digest, extract_diagrams, normalize_source
from diagram_raster.hook_gates import gate_agent_call, gate_plan_exit, mentions, record_briefback_result
from diagram_raster.ledger import Ledger, NoBriefbackError, default_ledger, validate_digest
from diagram_raster.models import Diagram, DiagramLang, Verdict
from diagram_raster.rendering import DiagramRenderer, Renderer, ensure_rendered

SUFFIX_LANG = {
    ".mmd": DiagramLang.MERMAID,
    ".mermaid": DiagramLang.MERMAID,
    ".d2": DiagramLang.D2,
    ".dot": DiagramLang.DOT,
    ".gv": DiagramLang.DOT,
    ".txt": DiagramLang.ASCII,
    ".ascii": DiagramLang.ASCII,
}


def diagrams_in(path: Path) -> list[Diagram]:
    """A standalone diagram file is one diagram; anything else is scanned as markdown."""
    text = path.read_text()
    lang = SUFFIX_LANG.get(path.suffix.lower())
    if lang is None:
        return extract_diagrams(text)
    return [Diagram(lang=lang, source=normalize_source(text), line=1, digest=diagram_digest(lang, text))]


def briefback_state(ledger: Ledger, digest: str) -> str:
    record = ledger.verdict(digest)
    captured = len(ledger.briefbacks(digest))
    if record is None:
        return f"no verdict ({captured} brief-back{'s' if captured != 1 else ''} captured)"
    return (
        f"{record.verdict} ({record.briefback_count} brief-back{'s' if record.briefback_count != 1 else ''})"
    )


def image_size(png: Path) -> str:
    with Image.open(png) as image:
        return f"{image.width}x{image.height}"


def cmd_render(args: argparse.Namespace, ledger: Ledger, renderer: DiagramRenderer, out: TextIO) -> int:
    diagrams = diagrams_in(Path(args.path))
    if not diagrams:
        print("no diagrams found", file=out)
        return 0
    failed = False
    for n, result in enumerate(ensure_rendered(diagrams, ledger, renderer, force=args.force), start=1):
        d = result.diagram
        print(f"{n}. {d.lang} at line {d.line}  digest {d.digest}", file=out)
        if result.error:
            failed = True
            print(f"   render failed: {result.error}", file=out)
            continue
        state = briefback_state(ledger, d.digest)
        print(f"   {result.png}  {image_size(result.png)}  brief-back: {state}", file=out)
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace, ledger: Ledger, renderer: DiagramRenderer, out: TextIO) -> int:
    path = Path(args.path)
    text = path.read_text()
    standalone = path.suffix.lower() in SUFFIX_LANG
    ready = True
    for n, d in enumerate(diagrams_in(path), start=1):
        png = ledger.png_path(d.digest)
        record = ledger.verdict(d.digest)
        rendered = png.exists()
        passed = record is not None and record.verdict is Verdict.PASS
        referenced = standalone or mentions(text, png)
        ready = ready and rendered and passed and referenced
        print(
            f"{n}. {d.lang} at line {d.line}  digest {d.digest}  rendered: {'yes' if rendered else 'no'}"
            f"  brief-back: {briefback_state(ledger, d.digest)}"
            + ("" if standalone else f"  png path in doc: {'yes' if referenced else 'no'}"),
            file=out,
        )
    return 0 if ready else 1


def cmd_record(args: argparse.Namespace, ledger: Ledger, renderer: DiagramRenderer, out: TextIO) -> int:
    if not ledger.png_path(args.digest).exists():
        print(f"unknown digest {args.digest}: render the diagram first", file=out)
        return 1
    try:
        record = ledger.record_verdict(args.digest, Verdict(args.verdict), args.intent)
    except NoBriefbackError as error:
        print(error, file=out)
        return 1
    print(f"{args.digest}: {record.verdict} after {record.briefback_count} brief-back(s)", file=out)
    return 0


def cmd_show(args: argparse.Namespace, ledger: Ledger, renderer: DiagramRenderer, out: TextIO) -> int:
    entry = ledger.entry_dir(args.digest)
    if not entry.is_dir():
        print(f"unknown digest {args.digest}", file=out)
        return 1
    print(f"png: {ledger.png_path(args.digest)}", file=out)
    for source in sorted(entry.glob("source.*")):
        print(f"source: {source}", file=out)
    record = ledger.verdict(args.digest)
    if record is not None:
        print(f"verdict: {record.verdict} at {record.at}\nintent: {record.intent}", file=out)
    for n, briefback in enumerate(ledger.briefbacks(args.digest), start=1):
        print(f"\n--- brief-back {n} ({briefback.at}) ---\n{briefback.text}", file=out)
    return 0


def cmd_hook(
    args: argparse.Namespace, ledger: Ledger, renderer: DiagramRenderer, out: TextIO, stdin: TextIO
) -> int:
    payload = json.load(stdin)
    tool_input = payload.get("tool_input") or {}
    match args.event:
        case "exit-plan":
            decision = gate_plan_exit(tool_input, ledger, renderer)
        case "agent-pre":
            decision = gate_agent_call(tool_input, ledger, renderer)
        case _:
            record_briefback_result(tool_input, payload.get("tool_response"), ledger)
            decision = None
    if decision is not None:
        out.write(json.dumps(decision))
    return 0


def digest_arg(value: str) -> str:
    try:
        return validate_digest(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diagram-raster", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render every diagram in a markdown file, or one diagram file")
    render.add_argument("path")
    render.add_argument("--force", action="store_true", help="re-render even if a render exists")

    status = sub.add_parser("status", help="exit 0 only if every diagram is rendered, referenced, passed")
    status.add_argument("path")

    record = sub.add_parser("record", help="record your verdict after comparing a brief-back to intent")
    record.add_argument("digest", type=digest_arg)
    record.add_argument("--verdict", choices=[v.value for v in Verdict], required=True)
    record.add_argument("--intent", required=True, help="what the diagram is meant to communicate")

    show = sub.add_parser("show", help="print render path, verdict and captured brief-backs")
    show.add_argument("digest", type=digest_arg)

    hook = sub.add_parser("hook", help="Claude Code hook entry point; reads hook JSON on stdin")
    hook.add_argument("event", choices=["exit-plan", "agent-pre", "agent-post"])
    return parser


def main(
    argv: list[str] | None = None,
    *,
    ledger: Ledger | None = None,
    renderer: DiagramRenderer | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    ledger = ledger or default_ledger()
    renderer = renderer or Renderer()
    out = stdout or sys.stdout
    match args.command:
        case "render":
            return cmd_render(args, ledger, renderer, out)
        case "status":
            return cmd_status(args, ledger, renderer, out)
        case "record":
            return cmd_record(args, ledger, renderer, out)
        case "show":
            return cmd_show(args, ledger, renderer, out)
        case _:
            return cmd_hook(args, ledger, renderer, out, stdin or sys.stdin)


def run() -> None:
    sys.exit(main())
