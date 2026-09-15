import io
import json
from pathlib import Path

from diagram_raster.cli import main
from diagram_raster.hook_gates import BRIEFBACK_AGENT
from diagram_raster.ledger import Ledger
from tests.conftest import StubRenderer

PLAN = "# Plan\n\n```mermaid\nflowchart TD\n  A --> B\n```\n"


def run(args: list[str], ledger: Ledger, renderer: StubRenderer, stdin: str = "") -> tuple[int, str]:
    out = io.StringIO()
    code = main(args, ledger=ledger, renderer=renderer, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue()


def test_render_then_briefback_then_record_clears_status(
    ledger: Ledger, renderer: StubRenderer, tmp_path: Path
) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)

    code, out = run(["render", str(plan)], ledger, renderer)
    assert code == 0
    [diagram] = renderer.rendered
    png = str(ledger.png_path(diagram.digest))
    assert png in out

    assert run(["status", str(plan)], ledger, renderer)[0] == 1

    code, out = run(["record", diagram.digest, "--verdict", "pass", "--intent", "A to B"], ledger, renderer)
    assert code == 1
    assert "brief-back" in out

    hook_input = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": BRIEFBACK_AGENT, "prompt": png},
        "tool_response": "Box A with an arrow down to box B.",
    }
    assert run(["hook", "agent-post"], ledger, renderer, json.dumps(hook_input))[0] == 0
    assert (
        run(["record", diagram.digest, "--verdict", "pass", "--intent", "A to B"], ledger, renderer)[0] == 0
    )

    plan.write_text(PLAN + f"\nRendered: {png}\n")
    assert run(["status", str(plan)], ledger, renderer)[0] == 0


def test_render_single_diagram_file(ledger: Ledger, renderer: StubRenderer, tmp_path: Path) -> None:
    source = tmp_path / "flow.d2"
    source.write_text("a -> b\n")
    code, _ = run(["render", str(source)], ledger, renderer)
    assert code == 0
    assert renderer.rendered[0].lang.value == "d2"


def test_exit_plan_hook_prints_deny_json(ledger: Ledger, renderer: StubRenderer) -> None:
    hook_input = {"tool_name": "ExitPlanMode", "tool_input": {"plan": PLAN}}
    code, out = run(["hook", "exit-plan"], ledger, renderer, json.dumps(hook_input))
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_allows_silently(ledger: Ledger, renderer: StubRenderer) -> None:
    hook_input = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "find"}}
    assert run(["hook", "agent-pre"], ledger, renderer, json.dumps(hook_input)) == (0, "")
