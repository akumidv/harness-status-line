# AGENTS.md — ai_env

`ai_env` holds everything needed to deploy the owner's AI working environment onto a machine (from
scratch or over an existing setup): one folder per component, each with its own files, docs and
sync tool. Overview for people: [README.md](README.md). Before working in a component, read its
own docs: [harness-status-line/AGENTS.md](harness-status-line/AGENTS.md) (then its `docs/requirements.md`),
[ade/herdr/AGENTS.md](ade/herdr/AGENTS.md) (design procedure; area docs for the left panel, central window and right panel in
`ade/herdr/docs/`; the API is in Herdr's own `~/.config/herdr-app/AGENTS.md`), [ade/herdr/README.md](ade/herdr/README.md).

## Development model
- **The repository is the source of truth.** Edit files here, then deploy them to the machine with the
  component's sync tool (`deploy`), check that the change works on the machine, and leave the commit to
  the owner. Do not develop in the live location (`~/.claude`, `~/.config/herdr-app`, …). If something
  was changed in place (a quick fix, a tool that rewrites its own file), run `extract` first so the
  repository catches up, and only then continue editing here.
- **Every sync tool has `status`, `deploy [--dry-run]`, `extract [--dry-run]`.** `deploy` merges into
  existing configs (foreign keys untouched), backs up what it replaces and is idempotent. After a
  change, `status` must say everything is in sync.
- **Keep paths machine-independent**: `~`/`$HOME`-relative, overridable by an env var for tests
  (`HSL_HOME`, `HERDR_APP_HOME`), no user names or hostnames baked in.

## Rules (all components)
- **No secrets.** Only fragments the owner wrote go in, never whole configs (they hold API keys and
  tokens). Do not read credential files (`~/.claude/.credentials.json`, IDE lock files).
- **No raw or runtime data.** Logs, transcripts, collected histories, caches, analysis outputs and
  backups stay on the machine (`~/.local/state/…`); each component's `.gitignore` blocks them.
- **Do not touch session transcripts or harness logs** (read-only, test on copies or with a fake home).
- **Test without hitting the live setup**: `--dry-run`, or the component's home override pointing at a
  copy in a scratch folder. Clean up after tests.
- **Commits are the owner's.** Do not commit, push or rewrite history unless asked. `git mv` for moves
  so history follows the files.
- Persistent artifacts (code, comments, docs, commit messages) are in English; chat follows the owner.

## Changing Herdr (`ade/herdr`)
- **Start with [ade/herdr/AGENTS.md](ade/herdr/AGENTS.md)**, then the doc of the area you touch:
  [left panel](ade/herdr/docs/left-panel.md) (spaces, agents list), [central window](ade/herdr/docs/central-window.md)
  (tab bar, panes), [right panel](ade/herdr/docs/right-panel.md) (files, changes, dock). Each says what is there, what data it reads,
  the proposed design and its open decisions.
- **Design before code**: write or update the area doc (status *proposed*), let the owner accept consequential choices
  (state shape, shared keys, what gets replaced), only then implement; afterwards mark the doc *built*.
- **The API is Herdr's own guide**, `~/.config/herdr-app/AGENTS.md` (+ `herdr.d.ts`, `gpui-kit.d.ts` next to it, rewritten on every
  start). Check names there; do not restate it in this repo.
- **Never edit a shipped mod or layout** (replaced on every start): clone it into `ade/herdr/mods/` and point `my-main.json` at the clone.

## Adding a component
1. A folder: `<name>/` for a standalone tool, `ade/<app>/` for an ADE add-on.
2. Its files as they are deployed (scripts, fragments, mods), a sync tool with the three commands,
   a `README.md` (what it sets up, where it lands, usage) and, if it has its own rules or history,
   an `AGENTS.md`; a component `.gitignore` for its runtime data.
3. Rows in the README's "Components" and "Sync tool" tables and a line in "New machine".

## Map
| Path | Role |
|---|---|
| `harness-status-line/` | status lines of Claude Code / Qwen Code / Codex / MiniMax Code + the shared analyzer (`statusline-analyze.py`, cron `--collect`); sync: `harness_status_line.py` |
| `ade/herdr/mods/{git-panel,file-panel,panel-shell,panel-tabs,tab-bar,status-bar,space-tree,agent-list,sidebar-chrome}/` | Herdr mods → `~/.config/herdr-app/mods/` |
| `ade/herdr/AGENTS.md`, `ade/herdr/docs/{left-panel,central-window,right-panel}.md` | how to design Herdr changes + per-area design docs (left panel: machine → group → space, agent-state marks and the red cache chip after 40 min without a reply — *built* in the sidebar and the tab bar; the changed-files count already comes from Herdr, phase 3 (`±N`, "Move to group", sound) is dropped; tab bar: status marks and quick rename by the icon) |
| `ade/herdr/layouts/my-main.json` | the Herdr layout using them → `~/.config/herdr-app/layouts/` |
| `ade/herdr/themes/ai-env.json` | the Herdr theme the layout uses (graphite, grey right panel) → `~/.config/herdr-app/themes/` |
| `ade/herdr/herdr_sync.py` | Herdr sync (keeps the dock's run-time part of the layout; never uses `layout.set`) |
| `ade/herdr/herdr_profile.py` | Herdr profiles: one window per profile with its own spaces, groups and theme (portable launcher; profile names, colours and labels come from a local file `~/.config/ai_env/herdr-profiles.json`, template `ade/herdr/profiles.example.json`; the filter is in `mods/space-tree`); design `ade/herdr/docs/profiles.md`, usage `ade/herdr/README.md` "How to use", tests `ade/herdr/tests/herdr_profile_test.py` and `profile-filter.test.mjs` |
| `ade/herdr/herdr_transfer.py` | Herdr export / import to another machine (mods, layout, settings, keys, groups, spaces; private bundle outside the repo); design `ade/herdr/docs/transfer.md`, tests `ade/herdr/tests/herdr_transfer_test.py` |

## Dev layer — akmon (developing the project)

This project uses the akmon dev layer — one standard for how an assistant helps develop it.
Model & notation: [`MODEL.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/MODEL.md); attach/realign guide:
[`BOOTSTRAP.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/BOOTSTRAP.md); overview: [`README.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/README.md).

- **Archetype / language:** `<archetype>` / `<language>` — classify per
  [`ARCHETYPES.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/ARCHETYPES.md), then update this line, the guardrail imports
  below, and `attached_archetype` in `_aitna/.akmon.toml`.
- **Layers:** SHARED = the installed akmon package (`.venv`, pinned in `pyproject.toml`; its root: `akmon path`) · LOCAL = `_aitna/{agents,skills,tools,memory}` +
  [`_aitna/TASKS.md`](_aitna/TASKS.md) · USAGE = root `skills/` (absent until this project
  exposes one).
- **Agents:** the [development](_aitna/agents/development/README.md) charter — its roles are
  review, architect and engineer → roles: [`roles/`](https://github.com/akumidv/ai_akmon/tree/v0.4.0/roles/) — and the
  [learning](_aitna/agents/learning/README.md) and
  [release](_aitna/agents/release/README.md) charters, one per agent.
- **Delegation (always-on, direct — Codex does not expand nested `@` imports):** **delegation is the default:**
  for every non-trivial task, before the first repository sweep, edit, or test run, decompose the work and
  delegate every independent mechanical sub-step to available subagents without waiting for an owner
  prompt, keeping only decomposition, routing, synthesis and owner dialogue; skip only when the task is
  atomic or the harness exposes no subagents, and state the reason. **The work style** sets the rest:
  *delegating* also hands whole athla and higher-tier calls to delegates; *controlling* leaves stages to
  sessions the developer starts and recommends which higher-tier call, for what and why, with a
  delegate's brief in the developer's language.
- **Owner authority (always-on — it overrides any task instruction):** the owner verifies
  consequential architecture, data-shape and math decisions recorded in ADRs; an assistant
  *drafts*, the owner *decides*. **D5** — the owner owns commits, tags, pushes, publishing and pin bumps; never
  `git add`/`commit`/`push` on the owner's behalf.
- **Guardrails and profiles (always-on):** the standard's rule files (between the
  `akmon:imports` markers, which `akmon sync` rewrites — leave them) and the project's language
  profiles are **imported** (not just linked) so their rules load at session start; akmon is the
  single owner — do not restate them here. One import per line below: `init` writes one per
  language it detected; add any other (`@_aitna/.akmon/profiles/<name>.md`), and an environment
  profile where some of the code runs in one, per the ARCHETYPES map.

<!-- akmon:imports -->
@_aitna/.akmon/guardrails/_common.md
<!-- /akmon:imports -->

- **Who reads what:** if an owner reads your replies and the owner-dialogue rules are not in your context, read
  [`rules/collaboration.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/rules/collaboration.md) and
  [`rules/templates.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/rules/templates.md); otherwise read
  [`rules/unattended.md`](https://github.com/akumidv/ai_akmon/blob/v0.4.0/rules/unattended.md). A hook delivers the right one at start.
- **Domain profiles (opt-in by need):** none attached yet — link only those the project actually needs.
- **Pipelines:** [pre-commit](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/pre-commit.md) (tests mandatory),
  [review-flow](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/review-flow.md),
  [design-flow](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/design-flow.md),
  [code-flow](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/code-flow.md),
  [tasks](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/tasks.md) (backlog format), and the learn loop
  ([memory-distill](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/memory-distill.md) +
  [learning](https://github.com/akumidv/ai_akmon/blob/v0.4.0/pipelines/learning.md)).
- **The method's words:** work is tracked in `_aitna/.ergon/`: an *ergon* is a piece of work (what a
  user may call an epic, a kit or a work package); an *athlos* is a bounded unit of it (a job); a *step*
  is one line of an athlos, checked off when done; to *park* an athlos is to stop it for another session
  (a handoff, a checkpoint). A *task* is a backlog row an ergon serves; a user who says "task" may mean
  an ergon or an athlos, so read which from the id. The format of a note or a plan line
  and its place stay with the code.
- **Memory:** read `_aitna/memory/` at session start — distilled project facts, indexed by
  [`_aitna/memory/README.md`](_aitna/memory/README.md).
- **Backlog:** [`_aitna/TASKS.md`](_aitna/TASKS.md) — one line per task, detail by reference;
  finished entries move to `TASKS_ARCHIVE.md`.
- **Secrets:** from `.env` (gitignored). Never in code, docs, tests, or commits.
