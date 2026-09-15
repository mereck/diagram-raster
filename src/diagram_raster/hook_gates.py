"""Claude Code hook decisions. Each gate returns a PreToolUse deny payload or None to allow."""

import json
from pathlib import Path
from typing import Any

from diagram_raster.extraction import extract_diagrams
from diagram_raster.ledger import Ledger
from diagram_raster.models import BRIEFBACK_AGENT, Verdict
from diagram_raster.rendering import DiagramRenderer, ensure_rendered

type HookDecision = dict[str, Any]

LOOP_HINT = (
    "Use the diagram-loop skill: render, give a fresh diagram-briefback agent only the PNG path, "
    "compare its description with what you meant, fix and re-render until they match, then run "
    "`diagram-raster record <digest> --verdict pass --intent '...'`."
)


def deny(reason: str) -> HookDecision:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def mentions(text: str, png: Path) -> bool:
    absolute = str(png)
    home = str(Path.home())
    forms = [absolute]
    if absolute.startswith(home + "/"):
        forms.append("~" + absolute[len(home) :])
    return any(form in text for form in forms)


def diagram_problems(
    text: str, ledger: Ledger, renderer: DiagramRenderer, require_verdict: bool
) -> list[str]:
    problems: list[str] = []
    for result in ensure_rendered(extract_diagrams(text), ledger, renderer):
        diagram = result.diagram
        label = f"{diagram.lang} diagram at line {diagram.line} (digest {diagram.digest})"
        if result.error:
            problems.append(f"{label}: render failed: {result.error}")
            continue
        if not mentions(text, result.png):
            problems.append(f"{label}: put its rendered image path next to the source: {result.png}")
        if require_verdict:
            record = ledger.verdict(diagram.digest)
            if record is None or record.verdict is not Verdict.PASS:
                problems.append(f"{label}: no passing clean-room brief-back for this exact version")
    return problems


def check_clean_room(prompt: str, ledger: Ledger) -> HookDecision | None:
    tokens = prompt.split()
    if not tokens:
        return deny(f"{BRIEFBACK_AGENT} prompt is empty. Pass the PNG paths from `diagram-raster render`.")
    stray = [token for token in tokens if ledger.digest_for_png(token) is None]
    if stray:
        return deny(
            f"Clean-room brief-back: the {BRIEFBACK_AGENT} prompt may contain only PNG paths printed by "
            "`diagram-raster render`. No source, intent, or context, or the brief-back stops being "
            f"independent. Remove: {' '.join(stray)[:200]}"
        )
    return None


def gate_agent_call(
    tool_input: dict[str, Any], ledger: Ledger, renderer: DiagramRenderer
) -> HookDecision | None:
    prompt = str(tool_input.get("prompt", ""))
    if tool_input.get("subagent_type") == BRIEFBACK_AGENT:
        return check_clean_room(prompt, ledger)
    problems = diagram_problems(prompt, ledger, renderer, require_verdict=False)
    if not problems:
        return None
    return deny(
        "This prompt contains diagrams. Subagents get the rendered image beside the text and are told "
        "to Read both:\n- " + "\n- ".join(problems)
    )


def plan_text(tool_input: dict[str, Any]) -> str:
    plan = tool_input.get("plan")
    if isinstance(plan, str) and plan.strip():
        return plan
    path = tool_input.get("planFilePath")
    if isinstance(path, str) and Path(path).expanduser().is_file():
        return Path(path).expanduser().read_text()
    return ""


def gate_plan_exit(
    tool_input: dict[str, Any], ledger: Ledger, renderer: DiagramRenderer
) -> HookDecision | None:
    problems = diagram_problems(plan_text(tool_input), ledger, renderer, require_verdict=True)
    if not problems:
        return None
    return deny(
        "Plan diagrams are not verified. Every diagram in a plan needs its rendered PNG path beside the "
        "source and a passing clean-room brief-back of that exact version.\n- "
        + "\n- ".join(problems)
        + "\n"
        + LOOP_HINT
    )


def response_text(tool_response: Any) -> str:
    content = (
        tool_response.get("content", tool_response) if isinstance(tool_response, dict) else tool_response
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"] if isinstance(part, dict) and isinstance(part.get("text"), str) else str(part)
            for part in content
        )
    return json.dumps(content)


def record_briefback_result(tool_input: dict[str, Any], tool_response: Any, ledger: Ledger) -> list[str]:
    """PostToolUse: store what the clean-room agent said, keyed by each PNG it was shown."""
    if tool_input.get("subagent_type") != BRIEFBACK_AGENT:
        return []
    digests = [
        d for token in str(tool_input.get("prompt", "")).split() if (d := ledger.digest_for_png(token))
    ]
    text = response_text(tool_response)
    for digest in digests:
        ledger.add_briefback(digest, text)
    return digests
