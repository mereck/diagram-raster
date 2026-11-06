from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from diagram_raster.hook_gates import (
    BRIEFBACK_AGENT,
    gate_agent_call,
    gate_plan_exit,
    record_briefback_result,
)
from diagram_raster.ledger import Ledger
from diagram_raster.models import Diagram, Verdict
from diagram_raster.rendering import RenderError
from tests.conftest import StubRenderer

MERMAID = "```mermaid\nflowchart TD\n  A --> B\n```"
D2 = "```d2\nplan -> review\n```"


def reason(decision: dict | None) -> str:
    assert decision is not None
    return decision["hookSpecificOutput"]["permissionDecisionReason"]


def png_for(text: str, ledger: Ledger, renderer: StubRenderer) -> list[str]:
    """Run the gate once so it renders, then read back the paths it wants."""
    gate_agent_call({"subagent_type": "Plan", "prompt": text}, ledger, renderer)
    return [str(ledger.png_path(d.digest)) for d in renderer.rendered]


def verify(ledger: Ledger, png: str) -> None:
    digest = Path(png).parent.name
    ledger.add_briefback(digest, "A points to B")
    ledger.record_verdict(digest, Verdict.PASS, intent="A leads to B")


def test_subagent_prompt_with_diagram_must_carry_its_png(ledger: Ledger, renderer: StubRenderer) -> None:
    decision = gate_agent_call({"subagent_type": "Plan", "prompt": MERMAID}, ledger, renderer)
    [png] = [str(ledger.png_path(d.digest)) for d in renderer.rendered]
    assert png in reason(decision)
    assert (
        gate_agent_call({"subagent_type": "Plan", "prompt": f"{MERMAID}\nRendered: {png}"}, ledger, renderer)
        is None
    )


def test_subagent_prompt_accepts_tilde_path(ledger: Ledger, renderer: StubRenderer) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    tilde = png.replace(str(Path.home()), "~", 1)
    prompt = f"{MERMAID}\nRendered: {tilde}"
    if tilde != png:
        assert gate_agent_call({"subagent_type": "Plan", "prompt": prompt}, ledger, renderer) is None


def test_prompt_without_diagrams_passes(ledger: Ledger, renderer: StubRenderer) -> None:
    assert gate_agent_call({"subagent_type": "Explore", "prompt": "find X"}, ledger, renderer) is None


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
@given(
    count=st.integers(min_value=1, max_value=3),
    separator=st.sampled_from([" ", "\n", "\n\n", "  \t"]),
    stray=st.text(alphabet="abcdefghij ", min_size=1, max_size=20).filter(str.strip),
)
def test_briefback_prompt_is_png_paths_and_nothing_else(
    ledger: Ledger, count: int, separator: str, stray: str
) -> None:
    pngs = []
    for n in range(count):
        png = ledger.png_path(f"d{n}")
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"png")
        pngs.append(str(png))
    clean = separator.join(pngs)
    call = {"subagent_type": BRIEFBACK_AGENT}
    assert gate_agent_call({**call, "prompt": clean}, ledger, StubRenderer()) is None
    assert gate_agent_call({**call, "prompt": f"{clean}{separator}{stray}"}, ledger, StubRenderer())


def test_briefback_prompt_cannot_carry_diagram_source(ledger: Ledger, renderer: StubRenderer) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    decision = gate_agent_call(
        {"subagent_type": BRIEFBACK_AGENT, "prompt": f"{png}\n{MERMAID}"}, ledger, renderer
    )
    assert "only PNG paths" in reason(decision)


# What the context-mode plugin appends to every Agent prompt (hooks/core/routing.mjs); it carries
# routing advice, nothing about the diagram.
CONTEXT_MODE_BLOCK = (
    "\n<context_window_protection>\n  <priority_instructions>\n    Every byte a tool returns enters "
    "your conversation memory.\n  </priority_instructions>\n</context_window_protection>"
)


@pytest.mark.parametrize("copies", [1, 2])
def test_briefback_allows_trailing_context_mode_blocks(
    ledger: Ledger, renderer: StubRenderer, copies: int
) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    prompt = png + CONTEXT_MODE_BLOCK * copies
    assert gate_agent_call({"subagent_type": BRIEFBACK_AGENT, "prompt": prompt}, ledger, renderer) is None


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(stray=st.text(min_size=1).filter(lambda text: text.strip() != ""))
def test_text_outside_a_trailing_context_mode_block_is_still_denied(
    ledger: Ledger, renderer: StubRenderer, stray: str
) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    call = {"subagent_type": BRIEFBACK_AGENT}
    for prompt in (
        f"{png} {stray}{CONTEXT_MODE_BLOCK}",
        f"{png}{CONTEXT_MODE_BLOCK}\n{stray}",
        f"{png}{CONTEXT_MODE_BLOCK}\n{stray}{CONTEXT_MODE_BLOCK}",
    ):
        if ledger.digest_for_png(stray.strip()) is None:
            assert gate_agent_call({**call, "prompt": prompt}, ledger, renderer), prompt


def test_context_mode_block_alone_is_an_empty_prompt(ledger: Ledger, renderer: StubRenderer) -> None:
    decision = gate_agent_call(
        {"subagent_type": BRIEFBACK_AGENT, "prompt": CONTEXT_MODE_BLOCK}, ledger, renderer
    )
    assert "empty" in reason(decision)


def test_empty_briefback_prompt_is_denied(ledger: Ledger, renderer: StubRenderer) -> None:
    assert gate_agent_call({"subagent_type": BRIEFBACK_AGENT, "prompt": "  "}, ledger, renderer)


def test_plan_exit_needs_render_verdict_and_path_for_every_diagram(
    ledger: Ledger, renderer: StubRenderer
) -> None:
    mermaid_png, d2_png = png_for(f"{MERMAID}\n{D2}", ledger, renderer)
    plan = f"# Plan\n{MERMAID}\n{mermaid_png}\n\n{D2}\n{d2_png}\n"

    verify(ledger, mermaid_png)
    unverified = reason(gate_plan_exit({"plan": plan}, ledger, renderer))
    assert "line 8" in unverified
    assert "line 2 " not in unverified

    verify(ledger, d2_png)
    assert gate_plan_exit({"plan": plan}, ledger, renderer) is None

    without_path = plan.replace(d2_png, "")
    assert d2_png in reason(gate_plan_exit({"plan": without_path}, ledger, renderer))


def test_editing_a_verified_diagram_invalidates_it(ledger: Ledger, renderer: StubRenderer) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    verify(ledger, png)
    edited = MERMAID.replace("A --> B", "A --> C")
    decision = gate_plan_exit({"plan": f"{edited}\n{png}"}, ledger, renderer)
    assert "brief-back" in reason(decision)


def test_only_a_pass_verdict_clears_the_plan_gate(ledger: Ledger, renderer: StubRenderer) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    digest = Path(png).parent.name
    ledger.add_briefback(digest, "B points to A")
    for verdict in Verdict:
        ledger.record_verdict(digest, verdict, intent="A leads to B")
        decision = gate_plan_exit({"plan": f"{MERMAID}\n{png}"}, ledger, renderer)
        assert (decision is None) == (verdict is Verdict.PASS), verdict


def test_plan_exit_reads_plan_file_when_plan_text_absent(
    ledger: Ledger, renderer: StubRenderer, tmp_path: Path
) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(MERMAID)
    assert gate_plan_exit({"planFilePath": str(plan_file)}, ledger, renderer)


def test_plan_without_diagrams_passes(ledger: Ledger, renderer: StubRenderer) -> None:
    assert gate_plan_exit({"plan": "# Plan\n1. do it"}, ledger, renderer) is None


def test_render_failure_is_reported_in_the_denial(ledger: Ledger) -> None:
    class Failing:
        def render(self, diagram: Diagram, out: Path) -> None:
            raise RenderError("Parse error on line 2")

    decision = gate_plan_exit({"plan": MERMAID}, ledger, Failing())
    assert "Parse error on line 2" in reason(decision)


def test_briefback_output_is_captured_per_png(ledger: Ledger, renderer: StubRenderer) -> None:
    mermaid_png, d2_png = png_for(f"{MERMAID}\n{D2}", ledger, renderer)
    record_briefback_result(
        {"subagent_type": BRIEFBACK_AGENT, "prompt": f"{mermaid_png}\n{d2_png}"},
        {"content": [{"type": "text", "text": "A flows to B."}]},
        ledger,
    )
    for png in (mermaid_png, d2_png):
        assert [b.text for b in ledger.briefbacks(Path(png).parent.name)] == ["A flows to B."]


def test_other_agents_output_is_not_captured(ledger: Ledger, renderer: StubRenderer) -> None:
    [png] = png_for(MERMAID, ledger, renderer)
    record_briefback_result({"subagent_type": "Plan", "prompt": png}, "looks fine", ledger)
    assert ledger.briefbacks(Path(png).parent.name) == []
