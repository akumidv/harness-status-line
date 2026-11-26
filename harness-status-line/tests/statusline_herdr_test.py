#!/usr/bin/env python3
"""Tests for the in-Herdr mode of claude/statusline-command.sh: python3 tests/statusline_herdr_test.py

Every run uses a temp folder (honours TMPDIR) with a fake HOME (a copy of shared/hsl-lib.sh, a synthetic `conn`
profile, synthetic transcripts), HSL_RT_BASE / HSL_RT_COLLECT pointing into it and a synthetic stdin JSON with
rate_limits. The live ~/.claude, real transcripts and the real caches (/tmp/claude-sl-$UID, /tmp/hsl-$UID,
~/.local/state/harness-status-line) are never touched; no analyzer is installed in the fake home, so nothing runs
in the background.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "claude" / "statusline-command.sh"
LIB = HERE.parent / "shared" / "hsl-lib.sh"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MODE_ICON = {"mode-eco": "❄", "mode-normal": "◆", "mode-peak": "🔥"}
SUMMARY_IDS = ["burn", "next", "work", "spd", "five", "wk", "forecast"]
TONES = {"none", "dim", "good", "cool", "warn", "bad", "mode-eco", "mode-normal", "mode-peak"}
HERDR_ENV = ("HC_TERMINAL_SOCKET", "HC_TERMINAL_ID", "HSL_FULL", "CLAUDE_CONFIG_DIR", "OUT_TTL")


def iso(epoch: float) -> str:
    """UTC timestamp in the transcript format (milliseconds, Z)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def write_transcript(path: Path, now: int) -> None:
    """A user prompt and one 500-token answer 10 s later, 30 s ago: 50 t/s, prompt cache 5 min."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "timestamp": iso(now - 40), "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "timestamp": iso(now - 30),
         "message": {"id": "m1", "usage": {"output_tokens": 500, "input_tokens": 10}}},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


class StatuslineHerdrTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hsl-herdr-test-"))
        self.home = self.tmp / "home"
        self.rt = self.tmp / "rt"
        self.work = self.tmp / "work"
        self.work.mkdir(parents=True)
        share = self.home / ".local/share/harness-status-line"
        share.mkdir(parents=True)
        shutil.copy(LIB, share / "hsl-lib.sh")
        # default account's connection profile: every hour of the week "normal" load (▸), speed 1.2× (green)
        conn = self.home / ".local/state/harness-status-line/api.anthropic.com/conn"
        conn.parent.mkdir(parents=True)
        conn.write_text("0 limited 14 tok 100\n" + " ".join(["1"] * 168) + "\n" + " ".join(["120"] * 168)
                        + "\n" + "1" * 168 + "\nopus-5-5=50\nfit\n")
        self.now = int(time.time())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, session: str, transcript: Path) -> str:
        write_transcript(transcript, self.now)
        return json.dumps({
            "session_id": session,
            "model": {"display_name": "Opus 5.5", "id": "claude-opus-5-5"},
            "effort": {"level": "high"},
            "context_window": {"used_percentage": 27, "context_window_size": 1000000,
                               "current_usage": {"input_tokens": 270000}},
            "transcript_path": str(transcript),
            "workspace": {"current_dir": str(self.work)},
            "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": self.now + 3600},
                            "seven_day": {"used_percentage": 30, "resets_at": self.now + 3 * 86400}},
        })

    def run_line(self, session: str = "s1", transcript: Path | None = None, **extra: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in HERDR_ENV}
        env.update(HOME=str(self.home), HSL_RT_BASE=str(self.rt), HSL_RT_COLLECT=str(self.tmp / "hsl"), **extra)
        transcript = transcript or self.home / ".claude/projects/p/s1.jsonl"
        res = subprocess.run(["bash", str(SCRIPT)], input=self.payload(session, transcript), env=env,
                             capture_output=True, text=True, timeout=30, check=True)
        return ANSI.sub("", res.stdout)

    def inside(self, terminal: str = "t99", **extra: str) -> dict[str, str]:
        return {"HC_TERMINAL_SOCKET": "x", "HC_TERMINAL_ID": terminal, **extra}

    def read_json(self, path: Path) -> dict:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
        return json.loads(path.read_text())

    def test_outside_full_line_and_no_files(self) -> None:
        line = self.run_line()
        self.assertIn("5h 40%↑", line)
        self.assertIn("wk 30%↑", line)
        self.assertIn("▸1.2× 50 [50]", line)
        self.assertFalse((self.rt / "terminals").exists())
        self.assertFalse((self.rt / "summary.json").exists())

    def test_inside_short_line_and_summary(self) -> None:
        line = self.run_line(**self.inside())
        self.assertNotIn("5h ", line)
        self.assertNotIn("wk ", line)
        self.assertNotIn("▸", line)
        self.assertRegex(line, r"^Opus 5\.5 high · ctx 270K/1M ◷ 4m · 50 \[50\] · ")
        self.assertEqual(stat.S_IMODE((self.rt / "terminals").stat().st_mode), 0o700)
        term = self.read_json(self.rt / "terminals/t99.json")
        self.assertEqual(term, {"v": 1, "t": term["t"], "account": "default", "dir": str(self.rt)})
        self.assertGreaterEqual(term["t"], self.now)
        summ = self.read_json(self.rt / "summary.json")
        self.assertEqual((summ["v"], summ["account"]), (1, "default"))
        self.assertGreaterEqual(summ["t"], self.now)
        segs = {s["id"]: s for s in summ["segments"]}
        self.assertEqual([s["id"] for s in summ["segments"]], SUMMARY_IDS)
        for s in summ["segments"]:
            self.assertEqual(set(s), {"id", "text", "tone", "tip"})
            self.assertIn(s["tone"], TONES)
            self.assertNotIn("\x1b", s["text"])
        self.assertIn(segs["burn"]["tone"], MODE_ICON)                     # no profile: icon only, in the mode's tone
        self.assertEqual(segs["burn"]["text"], MODE_ICON[segs["burn"]["tone"]])
        self.assertIn(segs["next"]["tone"], MODE_ICON)
        self.assertNotEqual(segs["next"]["tone"], segs["burn"]["tone"])
        self.assertRegex(segs["next"]["text"], r"^\d+(m|h\d\dm|d\d+h)$")
        self.assertEqual((segs["work"]["text"], segs["work"]["tone"]), (".5×", "cool"))   # even-spend pace .52
        self.assertEqual((segs["spd"]["text"], segs["spd"]["tone"]), ("▸1.2×", "good"))
        self.assertRegex(segs["five"]["text"], r"^5h 40%↑ (1h00m|59m)$")
        self.assertRegex(segs["wk"]["text"], r"^wk 30%↑ 3d0h$")
        self.assertEqual(segs["five"]["tone"], "none")
        self.assertEqual(segs["wk"]["tone"], "none")
        self.assertRegex(segs["forecast"]["text"], r"^\[~9d\d+h\]$")      # this week's pace: runs out after the reset
        self.assertEqual(segs["forecast"]["tone"], "dim")

    def test_line_cache_does_not_mix_modes(self) -> None:
        self.assertIn("5h 40%↑", self.run_line())
        self.assertNotIn("5h ", self.run_line(**self.inside()))
        self.assertIn("5h 40%↑", self.run_line())

    def test_hsl_full_forces_full_line(self) -> None:
        line = self.run_line(**self.inside(HSL_FULL="1"))
        self.assertIn("5h 40%↑", line)
        self.assertIn("wk 30%↑", line)
        self.assertFalse((self.rt / "terminals").exists())
        self.assertFalse((self.rt / "summary.json").exists())

    def test_personal_account(self) -> None:
        cfg = self.home / ".claude-personal"
        line = self.run_line(session="p1", transcript=cfg / "projects/p/p1.jsonl",
                             CLAUDE_CONFIG_DIR=str(cfg), **self.inside(terminal="t7"))
        self.assertNotIn("5h ", line)
        acct_dir = self.rt / "api.anthropic.com_claude-personal"
        term = self.read_json(self.rt / "terminals/t7.json")
        self.assertEqual((term["account"], term["dir"]), ("personal", str(acct_dir)))
        summ = self.read_json(acct_dir / "summary.json")
        self.assertEqual(summ["account"], "personal")
        ids = [s["id"] for s in summ["segments"]]
        self.assertEqual(ids, [i for i in SUMMARY_IDS if i != "spd"])   # no conn profile for this account yet
        self.assertFalse((self.rt / "summary.json").exists())

    def test_terminal_id_is_sanitised(self) -> None:
        self.run_line(**self.inside(terminal="../t 1"))
        self.assertEqual(sorted(p.name for p in (self.rt / "terminals").iterdir()), ["t1.json"])


if __name__ == "__main__":
    unittest.main()
