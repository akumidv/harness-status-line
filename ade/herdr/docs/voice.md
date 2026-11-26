# Voice input: dictate a prompt into the active agent

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status: **proposed** (2026-11-26; owner's answers so far: all agents, fully local, Russian + English + mixed,
a marker in front of the text; the open choices are at the end). Cross-area: a hotkey, a status indicator, a local helper process and a rule for the agents.

## Goal

A hotkey starts recording, the same hotkey stops it, the speech is transcribed **on this computer**, and the text lands in the input line of the terminal that was focused
when recording began, as an unsent draft. It works for any agent in the pane (Claude, Qwen, Codex, MiniMax), because it only types into the terminal.

## What is known (checked 2026-11-26)

| Thing | Fact | How it shapes the design |
|---|---|---|
| Microphone | WSLg gives `PULSE_SERVER=unix:/mnt/wslg/PulseServer`; `ffmpeg -f pulse -i default` records. A real test peaked at -21 dB (quiet) | the recorder adds gain; levels are checked in the first spike |
| CPU / RAM | 16 cores, 20 GB (about 10 GB free), no CUDA | `faster-whisper` on CPU is the first backend |
| Python | 3.14; a `cp314` wheel of `ctranslate2` exists; `uv` is installed | a venv under `~/.local/state/ai_env/voice/` (not in the repo) |
| GPU | Intel Arc B390 on Windows; WSL has `/dev/dxg` but no Intel compute runtime and no `/dev/dri`; `faster-whisper` has no Intel backend | backend sits behind a fixed interface; GPU (OpenVINO, or whisper.cpp with SYCL/Vulkan, or the daemon on Windows) is a later phase, only if the CPU is too slow |
| Hotkey | `keys.json` is key-down only (no hold / release); an action may be `<mod>.<command>`; a mod command exists only while a copy of the mod is shown | **toggle** (press to start, press to stop); the command is hosted by a mod that is always in the layout |
| Focused terminal | mod API `focused().terminal` gives its address (null for a mod pane); no CLI prints it | the mod reads it when recording **starts** and passes it on, so the text goes where the owner was, even after a switch |
| Text without Enter | `hc send` / `terminal.send` always press Enter; there is no draft-only text command. Candidates: `hc keys` with text, clipboard + `terminal.paste` | **not decided, must be tested** (spike S1) |
| Long processes | a mod `exec` is capped at 10 min and an `exec` outlives a mod reload | the mod only runs short commands against a daemon that owns the recording and the model |

## Design

```
hotkey ──► mod `voice` (command voice.toggle)
              │  focused().terminal ─┐
              ▼                      │
        voice-ctl start|stop  ──► daemon (Python, venv under ~/.local/state/ai_env/voice/)
                                    ├─ ffmpeg -f pulse … -af volume  → rec/<time>.wav
                                    ├─ faster-whisper (model kept loaded; unloaded after idle)
                                    └─ result text ─► deliver(text, terminal)   (S1 decides how)
mod `voice` ──► shared("voice.state") = idle | recording | transcribing ──► indicator
```

- **Mod `voice`** (new, `mods/voice/`): commands `voice.toggle`, `voice.cancel`; a small block in the status row with the state (a red dot while recording, a spinner
  while transcribing). It owns the shared key `voice.state` (single writer). It reads no files in `render()`; state comes from a short poll of the daemon, skipped while `checking()`.
- **Daemon** (`voice/voice_daemon.py`, stdlib + `faster-whisper`): started on demand by `voice-ctl`, listens on a Unix socket under `~/.local/state/ai_env/voice/` (mode 0600).
  Records with `ffmpeg`, stops on `stop`, on `cancel`, or after 120 s. The audio file is deleted right after transcription; transcripts are never logged. The model is unloaded after N idle minutes.
- **Backend interface**: `transcribe(wav_path, language, glossary) -> text`. First implementation `faster-whisper` on CPU; a GPU backend replaces only this function.
- **Layout**: one block for the indicator, placed by editing `layouts/my-main.json` (the owner sees the diff). Never `layout.set`.

### Languages and normalization (three layers, none needs a skill)

1. **At the source.** Whisper gets a glossary of the owner's terms (`herdr`, `akmon`, `worktree`, product and tool names) as `initial_prompt` / `hotwords`, with `language: ru` by default
   (a config value; `auto` as an option). This is what makes English words inside Russian speech come out in Latin letters. The glossary is a plain file in the repo (`voice/glossary.txt`), no secrets.
2. **A marker.** Before the text the tool inserts a fixed prefix (default `🎙 `; fallback ASCII `[voice] ` if the terminal path cannot carry it) so the agent knows the text is dictated.
3. **A rule for the agents** (one line, English): "Text that starts with the marker is dictated: silently correct likely recognition errors (homophones, mangled English terms, numbers),
   take spoken punctuation by meaning, and ask if the meaning is ambiguous." A skill would reach only Claude Code; an instruction file reaches every agent that reads it.
   Where the line lives is open (below).

### Privacy

Audio and text never leave the computer (no network calls except the one-time model download). Recordings live only until transcription ends. The model cache and venv are runtime data
under `~/.local/state/ai_env/voice/`, never in the repo.

## Phases

- **S (spikes, before the design is accepted).** Cheap facts that can change the design:
  - **S1 text delivery.** Does `hc keys <t> …` take a word, a space, Cyrillic, an emoji, a long line, at what speed? Does `terminal.paste` use bracketed paste, and is it acceptable to touch the clipboard?
    Is a draft preserved when the agent is working? Test on a scratch terminal, not on the owner's pane.
  - **S2 quality and speed.** Record 5 real phrases (Russian, English, mixed), run `small` and `medium` (and the turbo variant if available) on this CPU: time per 10 s of speech, errors with and without the glossary.
  - **S3 plumbing.** `herdr-app do voice.toggle` from the CLI and from the key; the mod command reaches a copy that is shown.
- **1. Build.** Daemon, `voice-ctl`, mod `voice`, layout block, key binding, tests (fake recorder and fake backend), `deploy` through `herdr_sync.py` (the mod is plain mod folder) and a sync line for the venv setup.
- **2. GPU** (optional). Only if S2 shows the CPU is too slow: OpenVINO or whisper.cpp behind the same interface; or the daemon on the Windows side, reached over localhost.

## Open decisions (owner)

1. **Draft or send.** Default: a draft (text in the input line, the owner presses Enter). If S1 finds no way to type without Enter, the fallback is `hc send` (sends at once) behind an explicit option.
2. **Hotkey.** Toggle only (hold is impossible in `keys.json`). The exact keys for `voice.toggle` / `voice.cancel` are picked after reading the live `keys.json` for conflicts.
3. **Marker text.** `🎙` or `[voice]`.
4. **Where the agent rule lives.** This repo's `AGENTS.md` only reaches sessions inside `ai_env`; for every project it must go into each agent's global instruction file
   (`~/.claude/CLAUDE.md`, the Qwen / Codex / MiniMax equivalents), which is a separate component with its own sync.
5. **Model.** Decided by S2 (accuracy against delay).

## Gotchas (this area)

- A mod command reaches only shown copies: the `voice` block must stay in the layout (an indicator block satisfies this).
- Saving any mod file reloads it and an already started `exec` keeps running: the daemon, not the mod, owns the recording; the mod holds no state it cannot rebuild from the daemon.
- A terminal can be a mod pane (`focused().terminal` is null): the toggle then refuses with a toast instead of recording into nothing.
- The recorder must run with `PULSE_SERVER=unix:/mnt/wslg/PulseServer` when the environment does not set it.
