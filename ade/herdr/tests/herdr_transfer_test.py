#!/usr/bin/env python3
"""Tests for herdr_transfer.py: python3 tests/herdr_transfer_test.py

Everything runs on a fake app home and a fake home folder in a temp dir with made-up projects. `herdr-app` and `hc`
are never executed: a stub runner answers `ls`, `status` and the like and records every `do` call. herdr_sync.py runs
for real (as a subprocess) against the fake home, with a stub `herdr-app` first on PATH for its `check` step.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import herdr_transfer as ht  # noqa: E402


class FakeWindow:
    """A runner standing in for `herdr-app` and `hc`: serves the state it was given, records `do` calls."""

    def __init__(self, home: Path, spaces=(), terminals=None, machines=(), groups_file: Path = None):
        self.home, self.calls, self.n = home, [], 0
        self.spaces = [dict(s) for s in spaces]
        self.terminals = terminals or {}  # address -> {"cwd": ..., "argv": [...]}
        self.machines, self.groups_file = list(machines), groups_file
        self.groups_payload = self.groups_file_mode = None

    def do_calls(self):
        return [(c[2], json.loads(c[3]) if len(c) > 3 else {}) for c in self.calls if c[:2] == ["herdr-app", "do"]]

    def new_ids(self, kind):
        self.n += 1
        return f"{kind}{self.n:03d}"

    def new_tab(self, name, space):
        terminal = f"t{self.n + 100}"
        tab = {"id": self.new_ids("tb"), "name": name, "active": True, "settled": False,
               "terminals": [{"terminal": terminal, "pane": self.new_ids("p"), "status": None, "app": None, "name": None, "sub_agents": []}]}
        space["tabs"].append(tab)
        return tab

    def answer(self, command, args):
        if command == "space.new":
            folder = args.get("folder", str(self.home))
            space = {"id": self.new_ids("sp"), "name": args.get("name") or Path(folder).name, "folder": folder,
                     "machine": args.get("machine"), "active": False, "tabs": []}
            tab = self.new_tab(args.get("tab", "1"), space)
            self.spaces.append(space)
            return {"space": space["id"], "tab": tab["id"], "terminal": tab["terminals"][0]["terminal"]}
        if command == "tab.new":
            space = next(s for s in self.spaces if s["id"] == args["space"])
            tab = self.new_tab(args.get("name", str(len(space["tabs"]) + 1)), space)
            return {"tab": tab["id"], "terminal": tab["terminals"][0]["terminal"]}  # no pane: the tool must look it up
        if command == "pane.split":
            return {"pane": self.new_ids("p"), "terminal": f"t{self.n + 100}"}
        if command == "space-tree.import-groups":
            self.groups_file_mode = stat.S_IMODE(self.groups_file.stat().st_mode)
            self.groups_payload = json.loads(self.groups_file.read_text())
            self.groups_file.unlink()  # what the mod does
        return {}

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        ok = lambda data: ht.Completed(0, json.dumps(data), "")
        if argv[:2] == ["herdr-app", "do"]:
            return ok(self.answer(argv[2], json.loads(argv[3]) if len(argv) > 3 else {}))
        if argv == ["herdr-app", "ls"]:
            return ok({"spaces": self.spaces, "recent": []})
        if argv == ["herdr-app", "status"]:
            return ok({"layout": "my-main", "errors": []})
        if argv == ["herdr-app", "--version"]:
            return ht.Completed(0, "herdr-app 0.1.18\n", "")
        if argv == ["hc", "ls", "--json"]:
            return ok({"terminals": [{"address": a, "cwd": t["cwd"], "agent": "bash"} for a, t in self.terminals.items()]})
        if argv[:2] == ["hc", "get"]:
            return ok({"foreground": {"argv": self.terminals[argv[2]].get("argv", ["bash"])}})
        if argv[:3] == ["hc", "machine", "ls"]:
            return ok({"machines": [{"name": m} for m in self.machines]})
        return ht.Completed(1, "", f"unexpected {argv}")


class TransferCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr-transfer-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home, self.app, self.repo = self.tmp / "home", self.tmp / "app", self.tmp / "repo"
        self.config = ht.Config(app_home=self.app, home=self.home, repo=self.repo, state_dir=self.home / ".local/state/ai_env/herdr",
                                backups=self.tmp / "backups", stamp="T1", wait=0.5)
        for folder in ("proj/sub", "other", "empty"):
            (self.home / folder).mkdir(parents=True)
        # the repo: one mod (+ a file Herdr owns that must never travel) and our layout
        self.write(self.repo / "mods/demo/main.js", "export default class Demo {}\n")
        self.write(self.repo / "mods/demo/mod.json", '{"place": "none"}\n')
        self.write(self.repo / "mods/demo/gpui-kit.d.ts", "// written by Herdr\n")
        self.layout = {"id": "root", "type": "row", "children": [
            {"id": "panel-body", "type": "stack", "children": [{"id": "files", "type": "block"}, {"id": "changes", "type": "block"}],
             "active": "files"},
            {"id": "panel-shell-term", "type": "stack", "children": []}]}
        self.write(self.repo / "layouts/my-main.json", json.dumps(self.layout))
        # the app home, in sync with the repo (as after `herdr_sync.py deploy`)
        self.write(self.app / "mods/demo/main.js", (self.repo / "mods/demo/main.js").read_text())
        self.write(self.app / "mods/demo/mod.json", (self.repo / "mods/demo/mod.json").read_text())
        self.write(self.app / "layouts/my-main.json", json.dumps(self.layout))
        self.write(self.app / "layouts/.state/my-main.json", json.dumps({"nodes": {
            "panel-body": {"size": {"weight": 2.5}, "active": "changes"},
            "panel-shell-term": {"active": "dock-tab-d2"}, "sidebar": {"size": {"px": 260.0}}}}))
        self.write(self.app / "settings.json", json.dumps({
            "zoom": 1.25, "font_size": 15, "terminal_line_height": 1.1, "worktrees": {"directory": f"{self.home}/wt"},
            "core": "/opt/secret-path/hc", "theme": "dusk", "colors": {"accent": "#ff8800"}}))
        self.write(self.app / "keys.json", json.dumps({"global": {"alt-1": "space.select:1", "alt-x": "tab.new"}}))
        self.write(self.app / "shared.json", json.dumps({
            "space.colors": {"spA": "ansi3", "spB": "ansi5"},
            "space.groups": {"local": [{"id": "g1", "name": "work", "folded": True, "spaces": ["spA"]}]},
            "panel-shell.dock": {"must": "not travel"}}))
        # a stub `herdr-app` first on PATH: herdr_sync.py deploy runs `herdr-app check` for changed mods
        self.write(self.tmp / "bin/herdr-app", "#!/bin/sh\nexit 0\n")
        (self.tmp / "bin/herdr-app").chmod(0o755)
        self.patch_env(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", XDG_STATE_HOME=str(self.tmp / "state"))
        self.window = FakeWindow(
            self.home, groups_file=self.config.state_dir / ht.IMPORT_GROUPS,
            terminals={"t1": {"cwd": str(self.home / "proj/sub")}, "t2": {"cwd": str(self.home / "other"), "argv": ["claude", "resume"]}},
            spaces=[
                {"id": "spA", "name": "backend", "folder": str(self.home / "proj"), "machine": None, "active": True, "tabs": [
                    {"id": "tbA", "name": "1", "active": True, "settled": False,
                     "terminals": [{"terminal": "t1", "pane": "pA", "status": None, "app": None, "name": "shell", "sub_agents": []}]},
                    {"id": "tbA2", "name": "review", "active": False, "settled": False,
                     "terminals": [{"terminal": "t2", "pane": "pA2", "status": "idle", "app": "claude-code", "name": None, "sub_agents": []}]}]},
                {"id": "spB", "name": "other", "folder": str(self.home), "machine": None, "active": False, "tabs": [
                    {"id": "tbB", "name": "1", "active": True, "settled": False,
                     "terminals": [{"terminal": "t3", "pane": "pB", "status": None, "app": None, "name": None, "sub_agents": []}]}]}])
        self.window.terminals["t3"] = {"cwd": str(self.home / "other")}

    def patch_env(self, **values):
        for key, value in values.items():
            old = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(lambda k=key, o=old: os.environ.pop(k, None) if o is None else os.environ.__setitem__(k, o))

    @staticmethod
    def write(path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_main(self, *argv, window=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ht.main(list(argv), config=self.config, runner=window or self.window)
        return code, out.getvalue(), err.getvalue()

    def export(self, name="bundle.tar.gz", *flags) -> Path:
        target = self.tmp / name
        code, out, err = self.run_main("export", str(target), *flags)
        self.assertEqual(code, 0, out + err)
        return target

    @staticmethod
    def read_bundle(path: Path) -> dict:
        with tarfile.open(path) as tar:
            return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}

    def forged(self, name, workspace=None, settings=None, keys=None) -> Path:
        """A bundle built by hand (what another machine's export could look like)."""
        members = [("manifest.json", ht.json_text({"version": 1, "herdr_version": "0.1.18", "parts": ["env", "workspace"]}).encode())]
        for rel, text in (("env/mods/demo/main.js", "export default class Demo {}\n"), ("env/mods/demo/mod.json", '{"place": "none"}\n'),
                          ("env/layouts/my-main.json", json.dumps(self.layout))):
            members.append((rel, text.encode()))
        for rel, data in (("env/settings.json", settings), ("env/keys.json", keys), ("workspace.json", workspace)):
            if data is not None:
                members.append((rel, ht.json_text(data).encode()))
        target = self.tmp / name
        ht.write_bundle(target, members)
        return target

    @staticmethod
    def space(name, folder, tabs=None, **extra):
        return {"name": name, "named": True, "folder": folder, "machine": None, "color": None,
                "tabs": tabs or [{"name": "1", "named": False, "panes": [{"cwd": folder, "name": None, "program": None}]}], **extra}

    @staticmethod
    def workspace(spaces, groups=()):
        keys = list(spaces)
        return {"layout": "my-main", "spaces": spaces, "machines": [{"machine": None, "groups": list(groups), "ungrouped": keys}]}


class ExportTest(TransferCase):
    def test_contents_and_home_is_written_as_tilde(self):
        files = self.read_bundle(self.export())
        self.assertEqual(next(iter(files)), "manifest.json")
        self.assertEqual(json.loads(files["manifest.json"])["version"], 1)
        self.assertIn("env/mods/demo/main.js", files)
        self.assertNotIn("env/mods/demo/gpui-kit.d.ts", files)
        self.assertIn("env/layouts/my-main.json", files)
        self.assertEqual(json.loads(files["env/keys.json"]), json.loads((self.app / "keys.json").read_text()))
        self.assertEqual(json.loads(files["env/layout-state.json"]), {"nodes": {
            "panel-body": {"size": {"weight": 2.5}, "active": "changes"}, "sidebar": {"size": {"px": 260.0}}}})
        workspace = json.loads(files["workspace.json"])
        self.assertEqual(set(workspace["spaces"]), {"local:~/proj:backend", "local:~:other"})
        backend = workspace["spaces"]["local:~/proj:backend"]
        self.assertEqual(backend["tabs"][0]["panes"][0], {"cwd": "~/proj/sub", "name": "shell", "program": None})
        self.assertEqual(backend["tabs"][1]["panes"][0]["program"], {"app": "claude-code", "run": ["claude"]})
        self.assertEqual([t["named"] for t in backend["tabs"]], [False, True])
        self.assertTrue(backend["named"])
        self.assertFalse(workspace["spaces"]["local:~:other"]["named"])  # called after its first terminal's folder
        self.assertEqual(backend["color"], "ansi3")
        self.assertEqual(workspace["machines"], [{"machine": None, "groups": [{"name": "work", "folded": True, "spaces": ["local:~/proj:backend"]}],
                                                  "ungrouped": ["local:~:other"]}])
        for name, text in files.items():
            self.assertNotIn(str(self.home), text, name)  # no user name in the bundle
        self.assertNotIn("must", "".join(files.values()))  # other shared keys never travel

    def test_no_workspace(self):
        files = self.read_bundle(self.export("small.tar.gz", "--no-workspace"))
        self.assertNotIn("workspace.json", files)
        self.assertEqual(json.loads(files["manifest.json"])["parts"], ["env"])

    def test_settings_allowlist(self):
        settings = json.loads(self.read_bundle(self.export())["env/settings.json"])
        self.assertEqual(settings, {"zoom": 1.25, "font_size": 15, "terminal_line_height": 1.1, "worktrees": {"directory": "~/wt"}})

    def test_refuses_on_drift_unless_allowed(self):
        self.write(self.repo / "mods/demo/main.js", "// edited in the repo, not deployed\n")
        target = self.tmp / "drift.tar.gz"
        code, out, err = self.run_main("export", str(target))
        self.assertEqual(code, 1)
        self.assertIn("differ", err)
        self.assertFalse(target.exists())
        code, out, err = self.run_main("export", str(target), "--allow-drift")
        self.assertEqual(code, 0, err)
        self.assertIn("edited in the repo", self.read_bundle(target)["env/mods/demo/main.js"])

    def test_modes_are_private(self):
        code, out, err = self.run_main("export")
        self.assertEqual(code, 0, err)
        bundle = next((self.config.state_dir / ht.EXPORTS).glob("herdr-*.tar.gz"))
        self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(bundle.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(bundle.parent.parent.stat().st_mode), 0o700)

    def test_sync_takes_repo_option(self):
        sync = Path(ht.herdr_sync.__file__)
        env = {**os.environ, "HERDR_APP_HOME": str(self.app)}
        done = subprocess.run([sys.executable, str(sync), "--repo", str(self.repo), "status"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("everything in sync", done.stdout)


class ImportEnvTest(TransferCase):
    def test_merge_settings_and_keys_with_backup_and_redeploy(self):
        bundle = self.export()
        # the target machine: different values, a key of its own, a mod file gone
        before_settings = {"zoom": 1, "core": "/target/hc", "font_size": 11}
        self.write(self.app / "settings.json", json.dumps(before_settings))
        self.write(self.app / "keys.json", json.dumps({"global": {"alt-1": "space.select:9", "mine": "tab.next"}, "mod:x": {"a": "b"}}))
        (self.app / "mods/demo/main.js").unlink()
        code, out, err = self.run_main("import", str(bundle), "--apply", "--only", "env")
        self.assertEqual(code, 0, out + err)
        settings = json.loads((self.app / "settings.json").read_text())
        self.assertEqual(settings["zoom"], 1.25)
        self.assertEqual(settings["core"], "/target/hc")  # a foreign key is untouched
        self.assertEqual(settings["worktrees"], {"directory": "~/wt"})
        keys = json.loads((self.app / "keys.json").read_text())
        self.assertEqual(keys["global"], {"alt-1": "space.select:1", "mine": "tab.next", "alt-x": "tab.new"})
        self.assertEqual(keys["mod:x"], {"a": "b"})
        backups = self.config.backups / "T1"
        self.assertEqual(json.loads((backups / "settings.json").read_text()), before_settings)
        self.assertIn("mine", (backups / "keys.json").read_text())
        self.assertTrue((self.app / "mods/demo/main.js").is_file())  # deployed through herdr_sync.py
        # the layout's clicks; the `sidebar` node is not in the fake layout, so it is skipped
        self.assertEqual(self.window.do_calls(), [("layout.resize", {"id": "panel-body", "weight": 2.5}),
                                                  ("layout.select", {"stack": "panel-body", "child": "changes"})])
        self.assertIn("skipped sidebar: not in the layout", out)

    def test_import_ignores_settings_outside_the_allowlist(self):
        bundle = self.forged("forged.tar.gz", settings={"zoom": 2, "core": "/evil/hc", "theme": "x", "font_weight": 700})
        code, out, err = self.run_main("import", str(bundle), "--apply", "--only", "env")
        self.assertEqual(code, 0, out + err)
        settings = json.loads((self.app / "settings.json").read_text())
        self.assertEqual((settings["zoom"], settings["core"], settings["theme"], settings["font_weight"]), (2, "/opt/secret-path/hc", "dusk", 700))

    def test_dry_run_prints_and_runs_nothing(self):
        bundle = self.forged("dry.tar.gz", settings={"zoom": 3}, workspace=self.workspace({"local:~/proj:new": self.space("new", "~/proj")}))
        (self.app / "mods/demo/main.js").unlink()
        before = {p: p.read_bytes() for p in self.app.rglob("*") if p.is_file()}
        code, out, err = self.run_main("import", str(bundle))
        self.assertEqual(code, 0, out + err)
        self.assertIn("DRY RUN", out)
        self.assertIn("settings.json: would set zoom", out)
        self.assertIn("would run: herdr-app do space.new", out)
        self.assertEqual(self.window.do_calls(), [])  # only reads reached the stub
        self.assertEqual({p: p.read_bytes() for p in self.app.rglob("*") if p.is_file()}, before)
        self.assertFalse(self.config.backups.exists())
        self.assertFalse((self.config.state_dir / ht.IMPORT_GROUPS).exists())

    def test_bundle_with_a_path_outside_is_refused(self):
        bad = self.tmp / "bad.tar.gz"
        ht.write_bundle(bad, [("manifest.json", b"{}"), ("../escape.txt", b"x")])
        code, out, err = self.run_main("import", str(bad), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)


class ImportWorkspaceTest(TransferCase):
    def test_skips_existing_space_and_missing_folder(self):
        spaces = {"local:~/proj:backend": self.space("backend", "~/proj"),  # already open
                  "local:~/gone:lost": self.space("lost", "~/gone"),  # folder not on this machine
                  "local:~/other:fresh": self.space("fresh", "~/other"),
                  "ssh1:/srv/app:remote": self.space("remote", "/srv/app", machine="ssh1")}  # machine not connected
        code, out, err = self.run_main("import", str(self.forged("w.tar.gz", workspace=self.workspace(spaces))), "--apply", "--only", "workspace")
        self.assertEqual(code, 0, out + err)
        made = [a for c, a in self.window.do_calls() if c == "space.new"]
        self.assertEqual([a["name"] for a in made], ["fresh"])
        self.assertEqual(made[0]["folder"], str(self.home / "other"))
        self.assertEqual(made[0]["focus"], False)  # never takes focus
        self.assertIn("skip backend (~/proj): already there", out)
        self.assertIn("skip lost (~/gone): the folder does not exist here", out)
        self.assertIn("skip remote (/srv/app): machine ssh1 is not connected", out)
        # a second import finds the space it made
        self.window.calls.clear()
        self.run_main("import", str(self.forged("w.tar.gz", workspace=self.workspace(spaces))), "--apply", "--only", "workspace")
        self.assertEqual([c for c, _ in self.window.do_calls() if c == "space.new"], [])

    def test_groups_and_colours_use_the_new_space_ids(self):
        spaces = {"local:~/proj:one": self.space("one", "~/proj", color="ansi1"),
                  "local:~/other:two": self.space("two", "~/other", color="ansi2"),
                  "local:~/empty:three": self.space("three", "~/empty")}
        groups = [{"name": "work", "folded": True, "spaces": ["local:~/proj:one", "local:~/other:two"]}, {"name": "idle", "folded": False, "spaces": []}]
        code, out, err = self.run_main("import", str(self.forged("g.tar.gz", workspace=self.workspace(spaces, groups))), "--apply", "--only", "workspace")
        self.assertEqual(code, 0, out + err)
        ids = {a["name"]: self.window.spaces[-3 + i]["id"] for i, a in enumerate(a for c, a in self.window.do_calls() if c == "space.new")}
        self.assertEqual(self.window.groups_payload, {
            "groups": [{"machine": None, "name": "work", "folded": True, "spaces": [ids["one"], ids["two"]]},
                       {"machine": None, "name": "idle", "folded": False, "spaces": []}],
            "colors": {ids["one"]: "ansi1", ids["two"]: "ansi2"}})
        self.assertTrue(ids["one"].startswith("sp0"))  # an id of the running window, not an exported one
        self.assertEqual(self.window.groups_file_mode, 0o600)
        self.assertFalse((self.config.state_dir / ht.IMPORT_GROUPS).exists())
        self.assertEqual(stat.S_IMODE(self.config.state_dir.stat().st_mode), 0o700)
        self.assertIn("space-tree.import-groups", [c for c, _ in self.window.do_calls()])

    def test_tabs_and_panes_are_rebuilt_as_a_row(self):
        panes = [{"cwd": "~/other", "name": "main", "program": None}, {"cwd": "~/other", "name": None, "program": None}]
        tabs = [{"name": "1", "named": False, "panes": [{"cwd": "~/proj", "name": None, "program": None}]},
                {"name": "review", "named": True, "panes": panes}]
        spaces = {"local:~/proj:multi": self.space("multi", "~/proj", tabs=tabs)}
        code, out, err = self.run_main("import", str(self.forged("t.tar.gz", workspace=self.workspace(spaces))), "--apply", "--only", "workspace")
        self.assertEqual(code, 0, out + err)
        calls = self.window.do_calls()
        self.assertEqual([c for c, _ in calls], ["space.new", "tab.new", "pane.rename", "pane.split"])
        space = self.window.spaces[-1]
        self.assertEqual(calls[1][1], {"space": space["id"], "focus": False, "folder": str(self.home / "other"), "name": "review"})
        first_pane = space["tabs"][1]["terminals"][0]["pane"]  # tab.new returned no pane: looked up through `ls`
        self.assertEqual(calls[2][1], {"pane": first_pane, "name": "main"})
        self.assertEqual(calls[3][1], {"direction": "right", "pane": first_pane})

    def test_agents_are_reported_or_launched(self):
        program = {"app": "claude-code", "run": ["claude"]}
        tabs = [{"name": "1", "named": False, "panes": [{"cwd": "~/proj", "name": None, "program": program}]}]
        spaces = {"local:~/proj:agent": self.space("agent", "~/proj", tabs=tabs)}
        bundle = self.forged("a.tar.gz", workspace=self.workspace(spaces))
        code, out, err = self.run_main("import", str(bundle), "--apply", "--only", "workspace")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("argv", self.window.do_calls()[0][1])
        self.assertIn("agent / 1 / pane 1: claude-code (claude): not restarted", out)
        self.window.spaces.clear()
        self.window.calls.clear()
        code, out, err = self.run_main("import", str(bundle), "--apply", "--only", "workspace", "--agents", "launch")
        self.assertEqual(self.window.do_calls()[0][1]["argv"], ["claude"])
        self.assertIn("started fresh", out)


if __name__ == "__main__":
    unittest.main()
