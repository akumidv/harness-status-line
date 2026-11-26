#!/usr/bin/env python3
"""Tests for herdr_profile.py: python3 tests/herdr_profile_test.py

Every run uses a temp HOME and a fake `herdr-app` shell script (HERDR_APP_BIN, also first on PATH so that
herdr_sync.py's `check` step finds a stub): `status` exits 1 until the fake window has started, the fake window
writes its environment and arguments into the app home. herdr_sync.py runs for real against the temp home.
The real GUI and the live homes are never touched. The profile table is a temp file (HERDR_PROFILES_CONFIG) with neutral names.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import herdr_profile as hp  # noqa: E402

TOOL = HERE.parent / "herdr_profile.py"
FAKE_APP = """#!/bin/sh
case "$1" in
  status) [ -e "$HERDR_APP_HOME/.fake-running" ]; exit $? ;;
  check) exit 0 ;;
esac
env > "$HERDR_APP_HOME/.fake-window.env"
echo "$@" > "$HERDR_APP_HOME/.fake-window.args"
touch "$HERDR_APP_HOME/.fake-running"
"""
PROFILE_TABLE = {"profiles": [
    {"name": "main", "label": "MN", "color": "#3b6fd4"},
    {"name": "work-a", "label": "WA", "color": "#2f9e6b", "theme": "moss", "stop_prompt": "Write a handoff note.\nThen stop."},
    {"name": "work-b", "color": "#7a5ad6", "theme": "ink"},
]}
DEFAULT_KEYS = {"global": {**{f"alt-{n}": f"space.select:{n}" for n in range(1, 10)}, "alt-h": "pane.go:left",
                           "ctrl-shift-pagedown": "space.next", "ctrl-shift-pageup": "space.prev"},
                "terminal": {"ctrl-shift-c": "terminal.copy"}}


class ProfileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr-profile-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"
        self.main = self.home / ".config/herdr-app"
        self.state = self.home / ".local/state/ai_env/herdr"
        self.backups = self.home / ".local/state/ai_env/backups/herdr"
        self.main.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.fake = self.bin / "herdr-app"
        self.fake.write_text(FAKE_APP)
        self.fake.chmod(0o755)
        self.table = self.tmp / "herdr-profiles.json"
        self.table.write_text(json.dumps(PROFILE_TABLE))
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}{os.defpath}", "HERDR_APP_BIN": str(self.fake),
                    "HERDR_PROFILES_CONFIG": str(self.table)}

    def home_of(self, profile: str) -> Path:
        return self.home / ".config" / ("herdr-app" if profile == "main" else f"herdr-app-{profile}")

    def config(self, **env):
        """The parsed Config (profile table included), read through Config.from_env() with the test environment."""
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update({**self.env, **env})
        try:
            return hp.Config.from_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def run_tool(self, *args, **env):
        full = {**self.env, **env}
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=full, timeout=120)

    def launch(self, profile, *args, **env):
        res = self.run_tool("launch", profile, *args, **env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def wait_for(self, path: Path):
        for _ in range(100):
            if path.exists():
                return
            time.sleep(0.05)
        self.fail(f"{path} never appeared")

    def close_window(self, home: Path):
        """The detached fake window may not have run yet: wait for it, then pretend it was closed."""
        self.wait_for(home / ".fake-running")
        (home / ".fake-running").unlink()

    def snapshot(self):
        return sorted((p.relative_to(self.tmp).as_posix(), p.stat().st_mtime_ns if p.is_file() else 0) for p in self.tmp.rglob("*"))

    def write_main(self, name, data):
        (self.main / name).write_text(json.dumps(data, indent=2) + "\n")

    def read(self, path: Path):
        return json.loads(path.read_text())


class SeedingTest(ProfileCase):
    def test_first_start_seeds_home_from_main(self):
        self.write_main("keys.json", DEFAULT_KEYS)
        self.write_main("settings.json", {"zoom": 1.25, "theme": "dusk"})
        res = self.launch("work-a")
        home = self.home_of("work-a")
        self.assertEqual(self.read(home / "shared.json"), {"layout": "my-main"})
        self.assertEqual(self.read(home / "settings.json"), {"zoom": 1.25, "theme": "moss"})
        self.assertTrue((home / ".seeded").exists())
        self.assertTrue((home / "mods/space-tree/main.js").is_file())  # deployed from the repo
        self.assertTrue((home / "layouts/my-main.json").is_file())
        self.assertEqual((home / "profile.name").read_text(), "work-a\n")
        self.assertEqual((home / "profile.color").read_text(), "#2f9e6b\n")
        self.assertEqual((home / "profile.stop_prompt").read_text(), "Write a handoff note.\nThen stop.\n")
        self.assertIn("alt-1", self.read(home / "keys.json")["global"])
        self.assertIn("started pid", res.stdout)

    def test_stop_prompt_file_is_removed_when_unset(self):
        self.launch("work-b")
        home = self.home_of("work-b")
        self.assertFalse((home / "profile.stop_prompt").exists())
        (home / "profile.stop_prompt").write_text("stale\n")
        self.launch("work-b")
        self.assertFalse((home / "profile.stop_prompt").exists())

    def test_second_start_does_not_recopy(self):
        self.write_main("keys.json", DEFAULT_KEYS)
        self.write_main("settings.json", {"zoom": 1.0})
        self.launch("work-b")
        home = self.home_of("work-b")
        self.assertEqual(self.read(home / "settings.json"), {"zoom": 1.0, "theme": "ink"})
        (home / "settings.json").write_text('{"theme": "mine"}\n')
        shared = {"layout": "my-main", "space.groups": {}}
        (home / "shared.json").write_text(json.dumps(shared))
        self.write_main("settings.json", {"zoom": 2.0})
        self.close_window(home)
        self.launch("work-b")
        self.assertEqual(self.read(home / "settings.json"), {"theme": "mine"})
        self.assertEqual(self.read(home / "shared.json"), shared)

    def test_theme_override_and_missing_main_files(self):
        self.launch("work-b", HERDR_PROFILE_THEME="dusk")
        home = self.home_of("work-b")
        self.assertEqual(self.read(home / "settings.json"), {"theme": "dusk"})
        self.assertFalse((home / "keys.json").exists())

    def test_main_profile_is_not_seeded(self):
        self.write_main("settings.json", {"theme": "dusk"})
        self.launch("main")
        self.assertEqual(self.read(self.main / "settings.json"), {"theme": "dusk"})
        self.assertFalse((self.main / ".seeded").exists())
        self.assertFalse((self.main / "mods").exists())
        self.assertEqual((self.main / "profile.name").read_text(), "main\n")
        self.assertEqual((self.main / "profile.color").read_text(), "#3b6fd4\n")
        self.assertFalse((self.home / ".config/herdr-app-main").exists())

    def test_main_home_env_override_is_ignored(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        self.launch("main", HERDR_APP_HOME=str(other))
        self.assertEqual(os.listdir(other), [])
        self.assertTrue((self.main / "profile.name").exists())


class ProfilesFileTest(ProfileCase):
    def test_created_once_and_never_overwritten(self):
        self.launch("main")
        target = self.state / "profiles.json"
        self.assertEqual(self.read(target), {"default": "main", "spaces": {}})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        target.write_text('{"default": "main", "spaces": {"sp1": "work-b"}}\n')
        self.close_window(self.main)
        self.launch("main")
        self.assertEqual(self.read(target), {"default": "main", "spaces": {"sp1": "work-b"}})

    def test_state_dir_override(self):
        self.launch("main", HERDR_PROFILE_STATE_DIR=str(self.tmp / "st"))
        self.assertTrue((self.tmp / "st/profiles.json").is_file())
        self.assertTrue((self.tmp / "st/launch-main.log").is_file())


class KeysTest(ProfileCase):
    def test_merge_only_default_bindings(self):
        data = json.loads(json.dumps(DEFAULT_KEYS))
        data["global"]["alt-3"] = "tab.new"           # user rebound: must stay out
        data["global"]["ctrl-shift-pageup"] = "x.y"  # same
        self.write_main("keys.json", data)
        self.launch("main")
        keys = self.read(self.main / "keys.json")
        section = keys["layout:my-main"]
        expected = {f"alt-{n}": f"space-tree.select-{n}" for n in range(1, 10) if n != 3}
        expected["ctrl-shift-pagedown"] = "space-tree.next"
        self.assertEqual(section, expected)
        self.assertEqual(keys["global"], data["global"])
        self.assertEqual(keys["terminal"], data["terminal"])
        self.assertEqual(list(keys)[:2], ["global", "terminal"])
        self.assertTrue((self.main / "keys.json").read_text().startswith('{\n  "global": {\n    "alt-1"'))  # indent 2

    def test_backup_made_with_original_content(self):
        self.write_main("keys.json", DEFAULT_KEYS)
        before = (self.main / "keys.json").read_text()
        self.launch("main")
        backups = list(self.backups.glob("*/keys-main.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), before)

    def test_idempotent_and_keeps_existing_section_entries(self):
        data = json.loads(json.dumps(DEFAULT_KEYS))
        data["layout:my-main"] = {"alt-1": "mine.cmd", "ctrl-shift-pageup": "space-tree.prev"}
        self.write_main("keys.json", data)
        self.launch("main")
        first = (self.main / "keys.json").read_text()
        section = self.read(self.main / "keys.json")["layout:my-main"]
        self.assertEqual(section["alt-1"], "mine.cmd")
        self.assertEqual(section["alt-2"], "space-tree.select-2")
        self.close_window(self.main)
        self.launch("main")
        self.assertEqual((self.main / "keys.json").read_text(), first)
        self.assertEqual(len(list(self.backups.glob("*/keys-main.json"))), 1)

    def test_missing_keys_file_is_not_created(self):
        self.launch("main")
        self.assertFalse((self.main / "keys.json").exists())

    def test_invalid_keys_file_fails_untouched(self):
        (self.main / "keys.json").write_text("{not json")
        res = self.run_tool("launch", "main")
        self.assertEqual(res.returncode, 1)
        self.assertIn("keys.json", res.stderr)
        self.assertEqual((self.main / "keys.json").read_text(), "{not json")

    def test_section_override_beats_global_is_documented_semantics(self):
        self.assertEqual(hp.keys_additions({"global": {"alt-1": "space.select:1"}}), {"alt-1": "space-tree.select-1"})
        self.assertEqual(hp.keys_additions({"global": {"alt-1": "space.select:2"}}), {})
        self.assertEqual(hp.keys_additions({}), {})


class StartTest(ProfileCase):
    def test_window_started_with_env_and_args(self):
        self.launch("work-b", "--", "--backend", "x11")
        home = self.home_of("work-b")
        self.wait_for(home / ".fake-running")
        env = dict(line.split("=", 1) for line in (home / ".fake-window.env").read_text().splitlines() if "=" in line)
        self.assertEqual(env["HERDR_APP_HOME"], str(home))
        self.assertEqual(env["HERDR_PROFILE"], "work-b")
        self.assertEqual(env["HERDR_APP_ID"], "herdr-app-work-b", "own application id: windows of a profile group apart")
        self.assertEqual((home / ".fake-window.args").read_text().split(), ["--backend", "x11"])

    def test_deploy_reaches_every_existing_home(self):
        self.launch("work-b")
        res = self.run_tool("deploy")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("work-b: deployed", res.stdout)
        self.assertIn("work-a: no home yet", res.stdout)

    def test_desktop_entries_and_icons_are_staged(self):
        out = self.tmp / "desk"
        res = self.run_tool("desktop", "--out", str(out), "--exec", "/w/herdr-launch-{profile}.sh")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        profiles = self.config().profiles
        for profile in profiles:
            entry = (out / "share/applications" / f"{profile.app_id}.desktop").read_text()
            self.assertIn(f"StartupWMClass={profile.app_id}", entry)
            self.assertIn(f"Icon={profile.app_id}", entry)
            self.assertIn(f"Exec=/w/herdr-launch-{profile.name}.sh", entry)
            for size in hp.ICON_SIZES:
                png = out / "share/icons/hicolor" / f"{size}x{size}/apps/{profile.app_id}.png"
                self.assertEqual(png.read_bytes()[:4], b"\x89PNG")
        self.assertEqual(len({p.app_id for p in profiles}), len(profiles), "one application id per profile")

    def test_desktop_draws_icons_into_the_state_dir_on_demand(self):
        icons = self.state / "icons"
        self.assertFalse(icons.exists())
        self.assertEqual(self.run_tool("desktop", "--out", str(self.tmp / "desk")).returncode, 0)
        names = sorted(p.name for p in icons.iterdir())
        self.assertEqual(names, sorted(f"{n}-{size}.png" for n in ("main", "work-a", "work-b") for size in hp.ICON_SIZES))
        first = {p.name: p.stat().st_mtime_ns for p in icons.iterdir()}
        self.assertEqual(self.run_tool("desktop", "--out", str(self.tmp / "desk")).returncode, 0)
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in icons.iterdir()}, first, "up to date icons are not redrawn")
        self.assertEqual([p for p in HERE.parent.joinpath("icons").iterdir() if p.suffix == ".png"], [], "nothing lands in the repo")

    def test_icons_follow_a_changed_table_and_a_state_dir_override(self):
        other = self.tmp / "st"
        self.assertEqual(self.run_tool("desktop", "--out", str(self.tmp / "desk"), HERDR_PROFILE_STATE_DIR=str(other)).returncode, 0)
        self.assertTrue((other / "icons/work-a-256.png").is_file())
        before = (other / "icons/work-a-256.png").read_bytes()
        self.table.write_text(json.dumps({"profiles": [{"name": "work-a", "label": "XY", "color": "#aa0000"}]}))
        os.utime(other / "icons/work-a-256.png", (1, 1))  # older than the table
        self.assertEqual(self.run_tool("desktop", "--out", str(self.tmp / "desk"), HERDR_PROFILE_STATE_DIR=str(other)).returncode, 0)
        self.assertNotEqual((other / "icons/work-a-256.png").read_bytes(), before, "the newer table redraws the icons")

    def test_already_running_is_skipped(self):
        home = self.home_of("work-b")
        home.mkdir(parents=True)
        (home / ".fake-running").touch()
        res = self.launch("work-b")
        self.assertIn("already running", res.stdout)
        self.assertFalse((home / ".fake-window.env").exists())
        self.assertEqual((home / "profile.name").read_text(), "work-b\n")  # the other steps still ran
        self.assertEqual((home / "profile.color").read_text(), "#7a5ad6\n")

    def test_launcher_wins_over_binary(self):
        wrapper = self.bin / "wrap.sh"
        wrapper.write_text('#!/bin/sh\necho "wrapped $@" > "$HERDR_APP_HOME/.wrapped"\n')
        wrapper.chmod(0o755)
        self.launch("main", "--", "extra", HERDR_LAUNCHER=f"{wrapper} herdr")
        self.wait_for(self.main / ".wrapped")
        self.assertEqual((self.main / ".wrapped").read_text().strip(), "wrapped herdr extra")
        self.assertFalse((self.main / ".fake-window.env").exists())

    def test_binary_from_path_when_no_explicit_one(self):
        env = {k: v for k, v in self.env.items() if k != "HERDR_APP_BIN"}
        self.env = env
        self.launch("main")
        self.wait_for(self.main / ".fake-running")

    def test_no_way_to_start_is_an_error(self):
        env = {"HOME": str(self.home), "PATH": os.defpath, "HERDR_PROFILES_CONFIG": str(self.table)}
        res = subprocess.run([sys.executable, str(TOOL), "launch", "main"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("HERDR_LAUNCHER", res.stderr)
        self.assertFalse((self.main / ".fake-running").exists())

    def test_no_platform_specific_code(self):
        source = TOOL.read_text().lower()
        for word in ("wsl", "/proc/version", "launch.sh"):
            self.assertNotIn(word, source)


class DryRunAndCommandsTest(ProfileCase):
    def test_dry_run_changes_nothing(self):
        self.write_main("keys.json", DEFAULT_KEYS)
        self.write_main("settings.json", {"zoom": 1.0})
        before = self.snapshot()
        for profile in ("work-a", "main"):
            res = self.launch(profile, "--dry-run", "--", "--x")
            self.assertIn("would: start", res.stdout)
            self.assertIn("--x", res.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.home_of("work-a").exists())
        self.assertFalse(self.state.exists())

    def test_dry_run_on_seeded_home_changes_nothing(self):
        self.write_main("keys.json", DEFAULT_KEYS)
        self.launch("work-b")
        self.close_window(self.home_of("work-b"))
        before = self.snapshot()
        res = self.launch("work-b", "--dry-run")
        self.assertIn("would: start", res.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_profile(self):
        res = self.run_tool("launch", "nope")
        self.assertEqual(res.returncode, 2)
        for name in ("main", "work-a", "work-b"):
            self.assertIn(name, res.stderr)
        self.assertIn("'nope'", res.stderr)
        self.assertFalse(self.state.exists())

    def test_list_and_status(self):
        (self.main / ".fake-running").touch()
        res = self.run_tool("list")
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = {line.split()[0]: line for line in res.stdout.splitlines()[1:]}
        self.assertEqual(set(lines), {"main", "work-a", "work-b"})
        self.assertTrue(lines["main"].rstrip().endswith("yes"))
        self.assertTrue(lines["work-b"].rstrip().endswith("no"))
        res = self.run_tool("status", "main")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("window running: yes", res.stdout)
        self.assertIn("color: #3b6fd4 (label MN", res.stdout)
        self.assertEqual(self.run_tool("status", "nope").returncode, 2)


class ProfileTableTest(ProfileCase):
    def parse(self, data):
        self.table.write_text(json.dumps(data))
        return self.config().profiles

    def test_defaults_main_is_first_and_keeps_the_app_home(self):
        main, work_a, work_b = self.config().profiles
        self.assertEqual((main.name, main.home_name, main.app_id, main.is_main), ("main", "herdr-app", "herdr-app", True))
        self.assertEqual((work_a.home_name, work_a.app_id, work_a.theme, work_a.label), ("herdr-app-work-a", "herdr-app-work-a", "moss", "WA"))
        self.assertEqual((work_b.label, work_b.color, work_b.is_main), ("WO", "#7a5ad6", False), "label defaults to two upper-case letters")
        self.assertIsNone(main.theme)

    def test_main_names_a_later_profile_and_its_home_is_always_the_app_home(self):
        profiles = self.parse({"main": "work-a", "profiles": [
            {"name": "main", "color": "#111111"},
            {"name": "work-a", "color": "#222222", "home": "ignored", "app_id": "ignored-too"}]})
        by_name = {p.name: p for p in profiles}
        self.assertTrue(by_name["work-a"].is_main)
        self.assertEqual((by_name["work-a"].home_name, by_name["work-a"].app_id), ("herdr-app", "herdr-app"))
        self.assertEqual(by_name["main"].home_name, "herdr-app-main")

    def test_explicit_home_app_id_and_colour_case(self):
        profiles = self.parse({"profiles": [{"name": "m", "color": "#AbCdEf"},
                                            {"name": "w", "color": "#000000", "home": "my-home", "app_id": "my-id"}]})
        self.assertEqual((profiles[1].home_name, profiles[1].app_id), ("my-home", "my-id"))
        self.assertEqual(profiles[0].color, "#abcdef")

    def test_main_home_rule_in_a_launch(self):
        self.table.write_text(json.dumps({"main": "work-a", "profiles": [
            {"name": "main", "color": "#111111"}, {"name": "work-a", "color": "#222222"}]}))
        self.launch("work-a")
        self.assertEqual((self.main / "profile.name").read_text(), "work-a\n")
        self.assertEqual(self.read(self.state / "profiles.json")["default"], "work-a")
        self.assertFalse((self.home / ".config/herdr-app-work-a").exists())

    def test_invalid_tables_name_the_value(self):
        good = {"name": "a", "color": "#123456"}
        cases = {
            "no profiles": ({"profiles": []}, "'profiles'"),
            "profiles not a list": ({"profiles": {"a": 1}}, "'profiles'"),
            "entry not an object": ({"profiles": ["a"]}, "'a'"),
            "missing name": ({"profiles": [{"color": "#123456"}]}, "'name'"),
            "missing colour": ({"profiles": [{"name": "a"}]}, "'color'"),
            "bad colour": ({"profiles": [{"name": "a", "color": "blue"}]}, "'blue'"),
            "short colour": ({"profiles": [{"name": "a", "color": "#123"}]}, "'#123'"),
            "duplicate name": ({"profiles": [good, {**good, "home": "x"}]}, "name 'a'"),
            "duplicate home": ({"profiles": [good, {"name": "b", "color": "#123456", "home": "herdr-app"}]}, "home_name 'herdr-app'"),
            "duplicate app id": ({"profiles": [good, {"name": "b", "color": "#123456", "app_id": "herdr-app"}]}, "app_id 'herdr-app'"),
            "unknown main": ({"main": "zzz", "profiles": [good]}, "'zzz'"),
            "path in home": ({"profiles": [good, {"name": "b", "color": "#123456", "home": "../x"}]}, "'../x'"),
            "theme not text": ({"profiles": [{**good, "theme": 3}]}, "'theme'"),
        }
        for label, (data, fragment) in cases.items():
            with self.subTest(label):
                self.table.write_text(json.dumps(data))
                res = self.run_tool("list")
                self.assertEqual(res.returncode, 1, res.stdout)
                self.assertIn(fragment, res.stderr)

    def test_invalid_json_and_non_object(self):
        for text in ("{nope", "[]"):
            self.table.write_text(text)
            res = self.run_tool("list")
            self.assertEqual(res.returncode, 1)
            self.assertIn(str(self.table), res.stderr)

    def test_missing_table_tells_where_to_copy_from(self):
        self.table.unlink()
        for command in (["list"], ["launch", "main"], ["desktop", "--out", str(self.tmp / "d")]):
            res = self.run_tool(*command)
            self.assertEqual(res.returncode, 1)
            self.assertIn("profiles.example.json", res.stderr)
            self.assertIn(str(self.table), res.stderr)
        self.assertFalse(self.state.exists())

    def test_default_path_is_under_the_home_config_dir(self):
        default = self.home / ".config/ai_env/herdr-profiles.json"
        default.parent.mkdir(parents=True)
        default.write_text(json.dumps({"profiles": [{"name": "solo", "color": "#123456"}]}))
        env = {k: v for k, v in self.env.items() if k != "HERDR_PROFILES_CONFIG"}
        res = subprocess.run([sys.executable, str(TOOL), "list"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("solo", res.stdout)

    def test_example_file_is_valid_and_neutral(self):
        example = HERE.parent / "profiles.example.json"
        profiles = hp.parse_profiles(json.loads(example.read_text()), example)
        self.assertEqual([p.name for p in profiles], ["main", "work-a", "work-b"])
        self.assertTrue(profiles[0].is_main)


if __name__ == "__main__":
    unittest.main()
