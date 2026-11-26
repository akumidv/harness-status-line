#!/usr/bin/env python3
"""Move the Herdr setup between machines: export it to a private bundle, import it on another.

  python3 herdr_transfer.py export [FILE] [--no-workspace] [--allow-drift]
  python3 herdr_transfer.py import FILE [--apply] [--only env|workspace] [--agents report|launch]

The design, the bundle shape and what is not carried: docs/transfer.md. In short: export takes our mods and
layouts from the repo, an allowlist of settings.json, keys.json, the layout's clicks and (from the open window)
the workspace: groups, spaces, tabs, panes. Import is a dry run unless --apply; it merges settings and keys
(backup first), deploys mods and layout through herdr_sync.py, then recreates the spaces that are not there yet.
The bundle holds project names and paths: it is written 0600 in a 0700 folder and never goes into the repo.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import herdr_sync

BUNDLE_VERSION = 1
LAYOUT = "my-main"
SETTINGS_KEYS = {"worktrees", "zoom", "sounds", "minimum_contrast", "appearance"}
SETTINGS_PREFIXES = ("font_", "terminal_")
# A foreground program of an agent pane that says nothing about the agent: its command is unknown.
INTERPRETERS = {"node", "bun", "deno", "python", "python3", "ruby", "sh", "bash", "zsh", "fish", "env"}
EXPORTS = "exports"
IMPORT_GROUPS = "import-groups.json"  # read (and deleted) by the mod command space-tree.import-groups


class TransferError(RuntimeError):
    """A condition that stops export or import (the message says what to do)."""


@dataclass(frozen=True)
class Config:
    """Everything the tool reads from the environment, resolved once at the entry point."""
    app_home: Path
    home: Path
    repo: Path
    state_dir: Path  # ~/.local/state/ai_env/herdr: exports/ and import-groups.json (the mod reads it from home())
    backups: Path
    stamp: str
    wait: float = 10.0  # seconds to wait for the mod to take import-groups.json

    @classmethod
    def from_env(cls) -> "Config":
        home = Path.home()
        backups = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state") / "ai_env/backups/herdr"
        return cls(app_home=Path(os.environ.get("HERDR_APP_HOME") or home / ".config/herdr-app"), home=home,
                   repo=Path(herdr_sync.__file__).resolve().parent, state_dir=home / ".local/state/ai_env/herdr",
                   backups=backups, stamp=time.strftime("%Y%m%d-%H%M%S"))


# ---- running programs (injectable: tests give a runner that records instead of executing)

@dataclass
class Completed:
    code: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], Completed]


def run_process(argv: Sequence[str]) -> Completed:
    try:
        res = subprocess.run(list(argv), capture_output=True, text=True)
    except OSError as err:
        return Completed(127, "", str(err))
    return Completed(res.returncode, res.stdout, res.stderr)


class HerdrError(RuntimeError):
    """`herdr-app` or `hc` answered with an error."""


class Herdr:
    """The window (`herdr-app`) and its core (`hc`) behind one runner."""

    def __init__(self, runner: Runner):
        self.runner = runner

    def query(self, argv: Sequence[str]) -> dict:
        done = self.runner(argv)
        try:
            data = json.loads(done.stdout)
        except ValueError:
            data = None
        if done.code != 0 or (isinstance(data, dict) and "error" in data):
            why = data.get("error") if isinstance(data, dict) else (done.stderr or done.stdout).strip()
            raise HerdrError(f"{' '.join(argv[:3])}: {why}")
        if not isinstance(data, dict):
            raise HerdrError(f"{' '.join(argv[:3])}: no JSON answer")
        return data

    def do(self, command: str, **args) -> dict:
        return self.query(["herdr-app", "do", command, *([json.dumps(args)] if args else [])])

    def ls(self) -> dict:
        return self.query(["herdr-app", "ls"])

    def status(self) -> dict:
        return self.query(["herdr-app", "status"])

    def version(self) -> str:
        done = self.runner(["herdr-app", "--version"])
        words = done.stdout.split()
        return words[-1] if done.code == 0 and words else "unknown"

    def local_terminals(self) -> dict:
        """address -> `hc ls --json` row (cwd, agent …) of this computer's live terminals."""
        return {t["address"]: t for t in self.query(["hc", "ls", "--json"]).get("terminals", [])}

    def machines(self) -> set:
        return {m["name"] for m in self.query(["hc", "machine", "ls", "--json"]).get("machines", []) if not m.get("temporary")}

    def agent_command(self, terminal: str) -> Optional[list]:
        """The command an agent in `terminal` was started with (its name only, no arguments), or None."""
        try:
            argv = (self.query(["hc", "get", terminal, "--json"]).get("foreground") or {}).get("argv") or []
        except HerdrError:
            return None
        name = Path(argv[0]).name if argv else ""
        return [name] if name and name not in INTERPRETERS else None

    def is_dir(self, machine: Optional[str], folder: str) -> bool:
        if machine is None:
            return Path(folder).is_dir()
        return self.runner(["hc", "exec", "--on", machine, "test", "-d", folder]).code == 0


# ---- pure helpers

def tilde(path: str, home: Path) -> str:
    """`path` with the home folder written as ~ (the bundle does not carry the user name)."""
    base = str(home).rstrip("/")
    if path == base:
        return "~"
    return "~" + path[len(base):] if path.startswith(base + "/") else path


def untilde(path: str, home: Path) -> str:
    return str(home) if path == "~" else str(home / path[2:]) if path.startswith("~/") else path


def allowlisted(settings: dict) -> dict:
    """The part of settings.json that belongs to the person's taste, not to the machine."""
    return {k: v for k, v in settings.items() if k in SETTINGS_KEYS or k.startswith(SETTINGS_PREFIXES)}


def allowed_settings(settings: dict, home: Path) -> dict:
    """`allowlisted`, with the worktrees folder written with ~ for home."""
    kept = allowlisted(settings)
    worktrees = kept.get("worktrees")
    if isinstance(worktrees, dict) and isinstance(worktrees.get("directory"), str):
        kept["worktrees"] = {**worktrees, "directory": tilde(worktrees["directory"], home)}
    return kept


def merged_settings(current: dict, incoming: dict) -> dict:
    """`current` with the allowlisted keys of `incoming` set; every other key stays as it is."""
    return {**current, **allowlisted(incoming)}


def merged_keys(current: dict, incoming: dict) -> dict:
    """`current` with every binding of `incoming` set, per section; other bindings stay."""
    merged = {section: dict(bindings) if isinstance(bindings, dict) else bindings for section, bindings in current.items()}
    for section, bindings in incoming.items():
        if isinstance(bindings, dict) and isinstance(merged.get(section, {}), dict):
            merged.setdefault(section, {}).update(bindings)
    return merged


def layout_clicks(state: dict) -> dict:
    """{node id: {size, active}} of a layouts/.state file, without the nodes panel-shell rewrites."""
    nodes = {}
    for node_id, node in (state.get("nodes") or {}).items():
        click = {k: node[k] for k in ("size", "active") if node.get(k) is not None}
        if click and node_id not in herdr_sync.VOLATILE:
            nodes[node_id] = click
    return {"nodes": nodes}


def json_text(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except ValueError as err:
        raise TransferError(f"{path} is not valid JSON: {err}") from err


def auto_name(folder: str, first_cwd: Optional[str], home: Path) -> set:
    """The names Herdr gives a space nobody named: its first terminal's repo or folder ('~' at home)."""
    names = {Path(p).name for p in (folder, first_cwd) if p}
    return names | ({"~"} if folder.rstrip("/") == str(home) or (first_cwd or "").rstrip("/") == str(home) else set())


def space_identities(space: dict) -> list:
    """What an existing space must match to be this one: machine, name and either its folder or its first pane's
    (a space is made to start where its first pane was, so that is where it shows up on the next import)."""
    first = next((p.get("cwd") for t in space["tabs"] for p in t["panes"] if p.get("cwd")), None)
    return [(space["machine"], folder, space["name"]) for folder in dict.fromkeys([space["folder"], first]) if folder]


# ---- export

def drift(config: Config) -> Optional[str]:
    """None when the repo and the app home are in sync, else `herdr_sync.py status` output."""
    env = {**os.environ, "HERDR_APP_HOME": str(config.app_home)}
    done = subprocess.run([sys.executable, herdr_sync.__file__, "--repo", str(config.repo), "status"],
                          capture_output=True, text=True, env=env)
    return None if done.returncode == 0 else (done.stdout + done.stderr).strip()


def snapshot_workspace(herdr: Herdr, config: Config) -> dict:
    """The open window as workspace.json: groups, spaces, tabs, panes (folders with ~ for home)."""
    listing = herdr.ls()
    terminals = herdr.local_terminals()
    shared_file = config.app_home / "shared.json"
    shared = read_json(shared_file) if shared_file.is_file() else {}
    colors, groups = shared.get("space.colors") or {}, shared.get("space.groups") or {}
    spaces, key_of, per_machine = {}, {}, {}
    for space in listing["spaces"]:
        machine = space.get("machine")
        local = machine is None
        norm = (lambda p: tilde(p, config.home)) if local else (lambda p: p)
        panes_of = [[{"terminal": t["terminal"], "name": t.get("name"), "app": t.get("app")}
                     for t in tab.get("terminals", [])] for tab in space.get("tabs", [])]
        first_cwd = next((terminals[p["terminal"]].get("cwd") for tab in panes_of for p in tab
                          if p["terminal"] in terminals), None) if local else None
        folder = norm(space["folder"]) if space["folder"] else ("~" if local else "")  # empty: its machine's home
        tabs = []
        for index, (tab, panes) in enumerate(zip(space.get("tabs", []), panes_of), 1):
            tabs.append({"name": tab["name"], "named": tab["name"] != str(index), "panes": [{
                "cwd": norm(terminals[p["terminal"]]["cwd"]) if local and terminals.get(p["terminal"], {}).get("cwd") else None,
                "name": p["name"],
                "program": {"app": p["app"], "run": herdr.agent_command(p["terminal"]) if local else None} if p["app"] else None,
            } for p in panes]})
        key = f"{machine or 'local'}:{folder}:{space['name']}"
        for n in range(2, 1000):  # the same machine, folder and name twice: keys stay unique
            if key not in spaces:
                break
            key = f"{machine or 'local'}:{folder}:{space['name']}#{n}"
        spaces[key] = {"name": space["name"], "named": space["name"] not in auto_name(space["folder"], first_cwd, config.home),
                       "folder": folder, "machine": machine, "color": colors.get(space["id"]), "tabs": tabs}
        key_of[space["id"]] = key
        per_machine.setdefault(machine, []).append(key)
    machines = []
    for machine in sorted(per_machine.keys() | {m if m != "local" else None for m in groups}, key=lambda m: (m is not None, m or "")):
        grouped = set()
        listed = []
        for group in groups.get(machine or "local") or []:
            members = [key_of[i] for i in group.get("spaces", []) if i in key_of]
            grouped.update(members)
            listed.append({"name": group["name"], "folded": bool(group.get("folded")), "spaces": members})
        ungrouped = [k for k in per_machine.get(machine, []) if k not in grouped]
        if listed or ungrouped:
            machines.append({"machine": machine, "groups": listed, "ungrouped": ungrouped})
    return {"layout": (herdr.status().get("layout") or LAYOUT), "machines": machines, "spaces": spaces}


def export_bundle(config: Config, herdr: Herdr, target: Path, with_workspace: bool = True, allow_drift: bool = False) -> dict:
    """Writes the bundle to `target` (0600) and returns its manifest. Refuses on drift unless allowed."""
    if not allow_drift:
        problem = drift(config)
        if problem:
            raise TransferError(f"the repo and the app home differ (deploy or extract first, or use --allow-drift):\n{problem}")
    members: list = []  # (archive name, bytes | Path)
    for folder in sorted(p for p in (config.repo / "mods").iterdir() if p.is_dir()):
        members += [(f"env/mods/{folder.name}/{rel}", folder / rel) for rel in sorted(herdr_sync.mod_files(folder))]
    members += [(f"env/layouts/{p.name}", p) for p in sorted((config.repo / "layouts").glob("*.json"))]
    settings_file, keys_file = config.app_home / "settings.json", config.app_home / "keys.json"
    state_file = config.app_home / "layouts/.state" / f"{LAYOUT}.json"
    if settings_file.is_file():
        members.append(("env/settings.json", json_text(allowed_settings(read_json(settings_file), config.home)).encode()))
    if keys_file.is_file():
        members.append(("env/keys.json", json_text(read_json(keys_file)).encode()))
    if state_file.is_file():
        members.append(("env/layout-state.json", json_text(layout_clicks(read_json(state_file))).encode()))
    if with_workspace:
        members.append(("workspace.json", json_text(snapshot_workspace(herdr, config)).encode()))
    try:
        version = herdr.version()
    except HerdrError:
        version = "unknown"
    manifest = {"version": BUNDLE_VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "herdr_version": version,
                "parts": ["env"] + (["workspace"] if with_workspace else [])}
    write_bundle(target, [("manifest.json", json_text(manifest).encode())] + members)
    return manifest


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(data)


def write_bundle(target: Path, members: list) -> None:
    def anonymous(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    target.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members:
            if isinstance(content, Path):
                tar.add(content, arcname=name, recursive=False, filter=anonymous)
            else:
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(content), int(time.time()), 0o644
                tar.addfile(anonymous(info), io.BytesIO(content))
    write_private(target, buffer.getvalue())


def default_target(config: Config) -> Path:
    private_dir(config.state_dir)
    private_dir(config.state_dir / EXPORTS)
    target = config.state_dir / EXPORTS / f"herdr-{time.strftime('%Y-%m-%d')}.tar.gz"
    return target if not target.exists() else target.with_name(f"herdr-{time.strftime('%Y-%m-%d-%H%M%S')}.tar.gz")


# ---- import

def unpack(bundle: Path, into: Path) -> dict:
    """Extracts the bundle into `into` (refusing anything but plain files below it); returns its manifest."""
    try:
        tar = tarfile.open(bundle, "r:gz")
    except (OSError, tarfile.TarError) as err:
        raise TransferError(f"{bundle} is not a Herdr bundle: {err}") from err
    with tar:
        members = tar.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if not (member.isfile() or member.isdir()) or Path(member.name).is_absolute() or ".." in parts:
                raise TransferError(f"{bundle}: refusing the entry {member.name!r}")
        if not members or members[0].name != "manifest.json":
            raise TransferError(f"{bundle} has no manifest.json first: not a Herdr bundle")
        for member in members:
            member.mode, member.uid, member.gid = (0o700 if member.isdir() else 0o600), 0, 0
            tar.extract(member, into)
    manifest = json.loads((into / "manifest.json").read_text())
    if manifest.get("version") != BUNDLE_VERSION:
        raise TransferError(f"bundle version {manifest.get('version')!r}, this tool reads {BUNDLE_VERSION}")
    for name in ("mods", "layouts"):
        (into / "env" / name).mkdir(parents=True, exist_ok=True)  # herdr_sync.py walks both
    return manifest


def walk_find(node: dict, node_id: str) -> Optional[dict]:
    return next((n for n in herdr_sync.walk(node) if n.get("id") == node_id), None)


class Importer:
    """One import: prints every action, performs it only with `apply`, collects what failed."""

    def __init__(self, config: Config, herdr: Herdr, apply: bool, agents: str = "report"):
        self.config, self.herdr, self.apply, self.agents = config, herdr, apply, agents
        self.failures: list = []
        self.counts = Counter()
        self.agent_report: list = []
        self.placeholders = 0

    def say(self, text: str = "") -> None:
        print(text)

    def act(self, command: str, **args) -> dict:
        """Runs (or, in a dry run, only prints) `herdr-app do command`; raises HerdrError on an error."""
        shown = f"herdr-app do {command}" + (f" {json.dumps(args, ensure_ascii=False)}" if args else "")
        if not self.apply:
            self.say(f"  would run: {shown}")
            self.placeholders += 1
            n = self.placeholders
            return {"space": f"<space {n}>", "tab": f"<tab {n}>", "terminal": f"<terminal {n}>", "pane": f"<pane {n}>"}
        self.say(f"  run: {shown}")
        return self.herdr.do(command, **args)

    def attempt(self, command: str, **args) -> Optional[dict]:
        """`act` for a step the rest does not depend on: a failure is reported at the end."""
        try:
            return self.act(command, **args)
        except HerdrError as err:
            self.failures.append(str(err))
            self.say(f"  FAILED: {err}")
            return None

    def run(self, bundle: Path, only: Optional[str]) -> int:
        with tempfile.TemporaryDirectory(prefix="herdr-bundle-") as scratch:
            manifest = unpack(bundle, Path(scratch))
            self.say(f"{'APPLY' if self.apply else 'DRY RUN (nothing is changed; --apply does it)'}: {bundle}")
            self.preflight(manifest)
            if only in (None, "env"):
                self.env(Path(scratch) / "env")
            if only in (None, "workspace") and (Path(scratch) / "workspace.json").is_file():
                self.workspace(read_json(Path(scratch) / "workspace.json"))
            elif only in (None, "workspace"):
                self.say("workspace: the bundle has none")
        self.summary()
        return 1 if self.failures else 0

    def preflight(self, manifest: dict) -> None:
        try:
            self.herdr.status()
        except HerdrError as err:
            raise TransferError(f"the Herdr app does not answer (start it, or check HERDR_APP_HOME): {err}") from err
        have = self.herdr.version()
        if have != manifest.get("herdr_version"):
            self.say(f"warning: exported from Herdr {manifest.get('herdr_version')}, this is {have}")

    # -- env: mods, layout, settings, keys, the layout's clicks

    def env(self, env_dir: Path) -> None:
        self.say("\nenv: mods and layouts (herdr_sync.py)")
        self.deploy(env_dir)
        for name, merge in (("settings.json", merged_settings), ("keys.json", merged_keys)):
            self.merge_file(env_dir / name, merge)
        self.clicks(env_dir)

    def deploy(self, env_dir: Path) -> None:
        argv = [sys.executable, herdr_sync.__file__, "--repo", str(env_dir), "deploy"] + ([] if self.apply else ["--dry-run"])
        done = subprocess.run(argv, capture_output=True, text=True, env={**os.environ, "HERDR_APP_HOME": str(self.config.app_home)})
        self.say(done.stdout.rstrip() or "  mods and layouts already in sync")
        if done.returncode != 0:
            self.failures.append(f"herdr_sync deploy: {(done.stderr or done.stdout).strip()}")

    def merge_file(self, source: Path, merge: Callable[[dict, dict], dict]) -> None:
        if not source.is_file():
            return
        target = self.config.app_home / source.name
        current = read_json(target) if target.is_file() else {}
        merged = merge(current, read_json(source))
        changed = sorted(k for k in merged if merged[k] != current.get(k))
        if not changed:
            self.say(f"  {source.name}: unchanged")
            return
        self.say(f"  {source.name}: {'sets' if self.apply else 'would set'} {', '.join(changed)}")
        if not self.apply:
            return
        if target.is_file():
            backup = self.config.backups / self.config.stamp / target.relative_to(self.config.app_home)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(target.read_bytes())
            self.say(f"  {source.name}: backup in {backup}")
        staged = target.with_name(target.name + ".transfer-tmp")
        staged.write_text(json_text(merged))
        if target.is_file():
            staged.chmod(target.stat().st_mode & 0o777)
        os.replace(staged, target)

    def clicks(self, env_dir: Path) -> None:
        layout_file, state_file = env_dir / "layouts" / f"{LAYOUT}.json", env_dir / "layout-state.json"
        if not layout_file.is_file():
            return
        self.say(f"\nlayout {LAYOUT} and its clicks")
        if self.herdr.status().get("layout") == LAYOUT:
            self.say(f"  {LAYOUT} is already shown")
        else:
            self.attempt("layout.switch", name=LAYOUT)
        tree = read_json(layout_file)
        for node_id, click in (layout_clicks(read_json(state_file)) if state_file.is_file() else {"nodes": {}})["nodes"].items():
            node = walk_find(tree, node_id)
            if node is None:
                self.say(f"  skipped {node_id}: not in the layout")
                continue
            if click.get("size"):
                self.attempt("layout.resize", id=node_id, **click["size"])
            if click.get("active") and walk_find(node, click["active"]):
                self.attempt("layout.select", stack=node_id, child=click["active"])

    # -- workspace: spaces first, then groups and colours

    def workspace(self, bundle: dict) -> None:
        self.say("\nworkspace: spaces")
        home = self.config.home
        existing = Counter((s.get("machine"), (tilde(s["folder"], home) or "~") if s.get("machine") is None else s["folder"], s["name"])
                           for s in self.herdr.ls()["spaces"])
        connected = self.herdr.machines()
        created: dict = {}  # bundle key -> the new space's id
        for key, space in bundle["spaces"].items():
            machine, label = space["machine"], f"{space['name']} ({space['folder']})"
            match = next((i for i in space_identities(space) if existing[i] > 0), None)
            if match:
                existing[match] -= 1
                self.counts["skipped: exists"] += 1
                self.say(f"  skip {label}: already there")
            elif machine is not None and machine not in connected:
                self.counts["skipped: machine"] += 1
                self.say(f"  skip {label}: machine {machine} is not connected")
            elif space["folder"] and not self.herdr.is_dir(machine, untilde(space["folder"], home)):
                self.counts["skipped: no folder"] += 1
                self.say(f"  skip {label}: the folder does not exist here (clone the repo, then import again)")
            else:
                try:
                    created[key] = self.make_space(space)
                    self.counts["created"] += 1
                except HerdrError as err:
                    self.counts["failed"] += 1
                    self.failures.append(f"{label}: {err}")
                    self.say(f"  FAILED {label}: {err}")
        self.groups(bundle, created, connected)

    def program_args(self, pane: dict) -> dict:
        """`argv` for a pane whose program was an agent, with --agents=launch; else it is only reported."""
        program = pane.get("program")
        if not program:
            return {}
        if self.agents == "launch" and program.get("run"):
            return {"argv": program["run"]}
        return {}

    def pane_of(self, made: dict) -> str:
        if made.get("pane") or not self.apply:
            return made.get("pane") or "<pane>"
        for space in self.herdr.ls()["spaces"]:
            for tab in space.get("tabs", []):
                for terminal in tab.get("terminals", []):
                    if terminal.get("terminal") == made.get("terminal"):
                        return terminal["pane"]
        raise HerdrError(f"no pane shows the terminal {made.get('terminal')}")

    def pane_start(self, machine: Optional[str], pane: dict, fallback: str) -> str:
        """Where a new space or tab starts so that its first pane is where the exported one was (else `fallback`)."""
        cwd = untilde(pane["cwd"], self.config.home) if pane.get("cwd") else ""
        return cwd if cwd and self.herdr.is_dir(machine, cwd) else fallback

    def make_space(self, space: dict) -> str:
        home = self.config.home
        folder = untilde(space["folder"], home)  # "": the machine's home
        machine = space["machine"]
        self.say(f"  create {space['name']} ({space['folder']}): {len(space['tabs'])} tab(s)")
        tabs = space["tabs"]
        first = tabs[0] if tabs else {"name": "1", "named": False, "panes": [{}]}
        start = self.pane_start(machine, (first["panes"] or [{}])[0], folder)
        args = {"focus": False, **self.program_args((first["panes"] or [{}])[0])}
        if start:
            args["folder"] = start
        if space["named"]:
            args["name"] = space["name"]
        if first["named"]:
            args["tab"] = first["name"]
        if machine is not None:
            args["machine"] = machine
        made = self.act("space.new", **args)
        space_id = made["space"]
        self.fill_tab(space, first, made)
        for tab in tabs[1:]:
            cwd = self.pane_start(machine, (tab["panes"] or [{}])[0], folder)
            args = {"space": space_id, "focus": False, **self.program_args((tab["panes"] or [{}])[0])}
            if cwd:
                args["folder"] = cwd
            if tab["named"]:
                args["name"] = tab["name"]
            self.fill_tab(space, tab, self.act("tab.new", **args))
        return space_id

    def fill_tab(self, space: dict, tab: dict, made: dict) -> None:
        """Names the first pane, then the other panes as a row of right splits (the geometry is not carried)."""
        panes = tab["panes"] or [{}]
        previous = self.pane_of(made)
        for number, pane in enumerate(panes):
            if number:
                previous = self.pane_of(self.act("pane.split", direction="right", pane=previous, **self.program_args(pane)))
            if pane.get("name"):
                self.attempt("pane.rename", pane=previous, name=pane["name"])
            program = pane.get("program")
            if program:
                note = "started fresh" if self.program_args(pane) else "not restarted (a shell stands there)"
                self.agent_report.append(f"{space['name']} / {tab['name']} / pane {number + 1}: {program['app']}"
                                         f"{' (' + ' '.join(program['run']) + ')' if program.get('run') else ''}: {note}")

    def groups(self, bundle: dict, created: dict, connected: set) -> None:
        self.say("\nworkspace: groups and colours")
        if not created:  # groups and colours follow new spaces only: a second import must not bring back a deleted group
            self.say("  nothing to apply (no new spaces)")
            return
        groups = []
        for entry in bundle["machines"]:
            if entry["machine"] is not None and entry["machine"] not in connected:
                continue
            for group in entry["groups"]:
                members = [created[k] for k in group["spaces"] if k in created]
                groups.append({"machine": entry["machine"], "name": group["name"], "folded": group["folded"], "spaces": members})
                self.say(f"  group {group['name']}: {len(members)} new space(s)")
        colors = {created[k]: s["color"] for k, s in bundle["spaces"].items() if k in created and s.get("color")}
        if not groups and not colors:
            self.say("  nothing to apply")
            return
        self.say(f"  {len(colors)} colour(s) for new spaces")
        path = self.config.state_dir / IMPORT_GROUPS
        if not self.apply:
            self.say(f"  would write {path} and run: herdr-app do space-tree.import-groups")
            return
        private_dir(self.config.state_dir)
        write_private(path, json_text({"groups": groups, "colors": colors}).encode())
        if self.attempt("space-tree.import-groups") is None:
            self.say(f"  the mod did not take the command (is space-tree shown, layout {LAYOUT}?); {path} stays, run the command again")
            return
        deadline = time.monotonic() + self.config.wait
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        if path.exists():
            self.failures.append(f"space-tree.import-groups did not consume {path}")
        else:
            self.say("  space-tree applied the groups and colours")

    def summary(self) -> None:
        self.say("\nsummary")
        for what, n in sorted(self.counts.items()):
            self.say(f"  spaces {what}: {n}")
        if self.agent_report:
            self.say("  agents that stood in the new panes (no history is carried; --agents=launch starts them fresh):")
            for line in self.agent_report:
                self.say(f"    {line}")
        for failure in self.failures:
            self.say(f"  FAILED: {failure}")
        if not self.apply:
            self.say("  dry run: nothing was changed")


# ---- command line

def main(argv: Optional[Sequence[str]] = None, config: Optional[Config] = None, runner: Runner = run_process) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    out = sub.add_parser("export", help="write a bundle of the setup")
    out.add_argument("file", nargs="?", type=Path, help="default: ~/.local/state/ai_env/herdr/exports/herdr-<date>.tar.gz")
    out.add_argument("--no-workspace", action="store_true", help="leave out groups, spaces, tabs and panes")
    out.add_argument("--allow-drift", action="store_true", help="export although the repo and the app home differ")
    inn = sub.add_parser("import", help="apply a bundle (a dry run unless --apply)")
    inn.add_argument("file", type=Path)
    inn.add_argument("--apply", action="store_true")
    inn.add_argument("--only", choices=["env", "workspace"])
    inn.add_argument("--agents", choices=["report", "launch"], default="report")
    args = ap.parse_args(argv)
    config = config or Config.from_env()
    try:
        if not config.app_home.is_dir():
            raise TransferError(f"no Herdr app home at {config.app_home} (install Herdr first, or set HERDR_APP_HOME)")
        herdr = Herdr(runner)
        if args.command == "export":
            target = args.file or default_target(config)
            manifest = export_bundle(config, herdr, target, not args.no_workspace, args.allow_drift)
            print(f"exported {', '.join(manifest['parts'])} to {target} (private: 0600; it holds project names and paths, keep it out of the repo)")
            return 0
        return Importer(config, herdr, args.apply, args.agents).run(args.file, args.only)
    except (TransferError, HerdrError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
