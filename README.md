# diagram-raster

Renders the text diagrams in a markdown file (mermaid, d2, graphviz dot, ASCII and
box-drawing art) to PNG. It also records clean-room brief-backs of each render and
exposes Claude Code hooks, so a plan can't leave plan mode until every diagram in it has
been read back correctly by an agent that saw only the image.

## Install

```sh
brew install graphviz d2        # mermaid runs through npx @mermaid-js/mermaid-cli@11
make install                    # uv tool install --editable .
```

Mermaid needs a Chromium build. The first match wins: `DIAGRAM_RASTER_CHROME`, Google
Chrome, Brave, Chromium, then Playwright's Chrome for Testing cache. ASCII art is drawn
with Menlo or DejaVu Sans Mono (`DIAGRAM_RASTER_FONT` overrides).

## Commands

```sh
diagram-raster render plan.md          # every diagram in the file; or one .mmd/.d2/.dot/.txt
diagram-raster status plan.md          # exit 0 when all rendered, referenced and passed
diagram-raster record <digest> --verdict pass|fail --intent "what it should say"
diagram-raster show <digest>           # png, source, verdict, captured brief-backs
diagram-raster hook exit-plan|agent-pre|agent-post   # Claude Code hook entry points (stdin JSON)
```

Renders are stored by content under `~/.cache/diagram-raster/<digest>/`
(`DIAGRAM_RASTER_HOME` overrides). The digest covers language and normalized source:
trailing whitespace, surrounding blank lines and the fence's indentation don't change
it, but any real edit does, which voids the old verdict.

## Hooks

| Event | Matcher | Denies when |
|---|---|---|
| PreToolUse | `ExitPlanMode` | a plan diagram has no render, no passing verdict for its current digest, or no PNG path in the plan |
| PreToolUse | `Agent` | a subagent prompt contains a diagram without its PNG path; a `diagram-briefback` prompt contains anything but PNG paths |
| PostToolUse | `Agent` | never; stores the `diagram-briefback` agent's output against each PNG it was shown |

`record --verdict pass` refuses a digest with no captured brief-back, so a pass always
follows at least one clean-room read of that exact render. Whether the brief-back
matches intent is still the calling model's judgment.

The agent definition (`diagram-briefback`) and the loop procedure (`diagram-loop`
skill) live in the dotfiles repo under `claude/`.

## Development

```sh
make check    # ruff check, ruff format --check, ty, pytest (coverage floor 90%)
```
