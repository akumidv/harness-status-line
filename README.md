# ai_env

One repository with everything needed to set up my AI working environment: on a new machine
from scratch, or to adapt an existing one. It keeps only what I wrote or tuned (scripts, config
fragments, mods, layouts) plus a small sync tool per component that deploys it into place.
Whole configs, keys, logs and collected data stay on the machine.

## Components

| Folder | What it sets up | Lands in |
|---|---|---|
| [`harness-status-line/`](harness-status-line/README.md) | Status lines for Claude Code, Qwen Code, Codex, MiniMax Code and a shared analyzer of all harness logs (limits, burn rate, load modes) | `~/.claude`, `~/.qwen`, `~/.codex*`, `~/.minimax`, `~/.local/share/harness-status-line`, a cron line |
| [`ade/`](ade/) | Add-ons for ADEs (agentic development environments): one subfolder per app | the app's config home |
| [`ade/herdr/`](ade/herdr/README.md) | Herdr mods (git panel, files with preview and terminal file links, a dock of bash tabs under the right panel, a tab bar) and the `my-main` layout | `~/.config/herdr-app` |

## How it works

The repository is the source of truth. Work happens here; the machine gets it by deploying:

```
edit in ai_env/<component>/  ->  <sync tool> status  ->  <sync tool> deploy  ->  check it works  ->  commit
```

Every component has the same three commands in its own sync tool:

- `status` — what differs between the repository and the machine;
- `deploy [--dry-run]` — repository → machine, merging fragments into existing configs and backing up whatever it replaces;
- `extract [--dry-run]` — machine → repository, only to bring back a change made in place (tried live first, then kept).

| Component | Sync tool |
|---|---|
| `harness-status-line` | `python3 harness_status_line.py status\|deploy\|extract [--only <harness>]` |
| `ade/herdr` | `python3 herdr_sync.py status\|deploy\|extract` |

## New machine

```bash
git clone <this repo> ~/workspace/ai_env && cd ~/workspace/ai_env
# Dev layer: akmon is a git-pinned dev dependency in .venv (needs uv and python >= 3.12)
uv sync
# Status lines (needs python3 ≥ 3.11, bash ≥ 4.2, jq, awk); see harness-status-line/README.md for the cron line
(cd harness-status-line && python3 harness_status_line.py deploy --dry-run && python3 harness_status_line.py deploy)
# Herdr add-ons (install Herdr first and start it once so ~/.config/herdr-app exists)
(cd ade/herdr && python3 herdr_sync.py deploy && herdr-app do layout.switch '{"name":"my-main"}')
# Herdr profiles (optional): the profile names/colours are local data, not in the repo
# mkdir -p ~/.config/ai_env && cp ade/herdr/profiles.example.json ~/.config/ai_env/herdr-profiles.json   # then edit it
```

Each `deploy` can be rerun: it changes nothing when the machine is already in sync.

## Layout

```
README.md, AGENTS.md      this overview; rules for agents working in the repo
harness-status-line/      component: status lines + analyzer (own README, AGENTS, docs/)
ade/
  herdr/                  component: Herdr mods + layout (own README)
pyproject.toml, uv.lock   dev environment only: the akmon pin, nothing to build
_aitna/                   dev layer: the project's own backlog/agents/memory + .akmon/ (materialized guardrails)
```

## akmon (the dev layer)

akmon is installed as a package from its git repository, pinned to a release in `pyproject.toml`
(`akmon @ git+https://github.com/akumidv/ai_akmon@v0.4.0`), mount mode `package`. Its files are in
`.venv`; `.venv/bin/akmon path` prints where. The generated hook wiring calls `.venv/bin/akmon`, so
`uv sync` has to have run. Check: `.venv/bin/akmon sync --check && .venv/bin/akmon verify --strict`.

To work against a local checkout instead of the pin (the files are then a git working copy), on this machine only:

```bash
uv pip install -e ../akmon     # ~/workspace/akmon laid over the pin; `akmon path` now shows the checkout
uv sync                        # back to the pin (uv run and uv sync always do this)
```

A checkout that is ahead of the pinned release generates different pointer files, so `sync --check` and
`verify` report them as stale until `akmon sync` is run (or the checkout sits on the pinned release).
Bump the pin: change the version in `pyproject.toml`, `uv lock --upgrade-package akmon && uv sync`,
then `.venv/bin/akmon init --mode package`.

Agents (and people continuing the work): read [AGENTS.md](AGENTS.md), then the component's own README / AGENTS.

License: [MIT](LICENSE).
