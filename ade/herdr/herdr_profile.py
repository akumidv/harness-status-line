#!/usr/bin/env python3
"""Open a Herdr window for a profile: its own app home, theme and keys, same layout and mods everywhere.

  python3 herdr_profile.py list                                 profiles, their homes, whether a window answers
  python3 herdr_profile.py status [PROFILE]                     what is set up for a profile (all when omitted)
  python3 herdr_profile.py launch PROFILE [--dry-run] [-- ARGS] seed the home, then start the window (ARGS go to the app)

Profiles (names, colours, icon labels, themes) are not in the repo: they are read from a local JSON file,
$HERDR_PROFILES_CONFIG or `~/.config/ai_env/herdr-profiles.json`; copy `profiles.example.json` there and edit it.

Design: docs/profiles.md. `launch` is idempotent; in order:
  1. a non-main profile: create its home, `herdr_sync.py deploy` into it, `shared.json` {"layout": "my-main"} when missing;
     on the first start only (marker `.seeded`) keys.json and settings.json (+ the profile's theme) are taken from the main home;
  2. `<home>/profile.name` = the profile name and `<home>/profile.color` = its colour (the mods read both);
  3. `~/.local/state/ai_env/herdr/profiles.json` is created when missing and never changed afterwards;
  4. the profile's keys are merged into `<home>/keys.json` (section `layout:my-main`, backup first; a missing file is left alone);
  5. the window is started detached unless one already answers for this home.

Start command: $HERDR_LAUNCHER (shell-split; ARGS are appended) if set, else $HERDR_APP_BIN, else `herdr-app` from PATH.
The child gets HERDR_APP_HOME=<home> and HERDR_PROFILE=<profile>. $HERDR_PROFILE_THEME overrides the profile's theme (first start).
`--dry-run` prints every step and the command and changes nothing on disk.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent
LAYOUT = "my-main"
MAIN_HOME_NAME = "herdr-app"  # Herdr's own app home folder; the main profile always uses it
HOME_NAME_PREFIX = "herdr-app-"
PROFILES_CONFIG_NAME = "herdr-profiles.json"
PROFILES_EXAMPLE = REPO / "profiles.example.json"
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")
KEYS_SECTION = f"layout:{LAYOUT}"
SEEDED = ".seeded"
PROFILE_NAME = "profile.name"
PROFILE_COLOR = "profile.color"
PROFILE_STOP_PROMPT = "profile.stop_prompt"
STATUS_TIMEOUT = 15  # seconds `herdr-app status` may take before the window counts as not running


@dataclass(frozen=True)
class Profile:
    name: str
    home_name: str  # folder under ~/.config
    theme: Optional[str]
    app_id: str  # the window's application id (HERDR_APP_ID): a taskbar or dock groups windows by it
    label: str  # the icon text
    color: str  # "#rrggbb": the icon tile and the window tint
    is_main: bool  # the main profile keeps Herdr's own home and id, so an existing `herdr-app.desktop` still matches it
    stop_prompt: Optional[str] = None  # the checkpoint prompt the "Prepare to stop" button sends (None: the mod's default)


# Herdr's default bindings that count every space; a profiled window rebinds them to the space-tree commands.
KEY_DEFAULTS = {
    **{f"alt-{n}": (f"space.select:{n}", f"space-tree.select-{n}") for n in range(1, 10)},
    "ctrl-shift-pagedown": ("space.next", "space-tree.next"),
    "ctrl-shift-pageup": ("space.prev", "space-tree.prev"),
}


class ProfileError(RuntimeError):
    """A condition that stops the tool (the message says what to do)."""


class UnknownProfile(ProfileError):
    """The requested profile is not in the table (exit status 2)."""


@dataclass(frozen=True)
class Config:
    """Everything the tool reads from the environment, resolved once at the entry point."""
    home: Path        # the user's home; every profile home and state path derives from it
    state_dir: Path   # profiles.json, the launch logs and the generated icons
    profiles_config: Path  # the local profile table ($HERDR_PROFILES_CONFIG or ~/.config/ai_env/herdr-profiles.json)
    profiles: tuple   # the Profile entries read from `profiles_config`, in file order
    backups: Path
    stamp: str
    theme_override: Optional[str]
    launcher: Optional[str]
    app_bin: Optional[str]  # explicit herdr-app path ($HERDR_APP_BIN)

    @classmethod
    def from_env(cls) -> "Config":
        """Reads the environment and the profile table; raises ProfileError when the table is missing or invalid."""
        home = Path.home()
        state_root = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state")
        profiles_config = Path(os.environ.get("HERDR_PROFILES_CONFIG") or home / ".config/ai_env" / PROFILES_CONFIG_NAME)
        return cls(home=home,
                   state_dir=Path(os.environ.get("HERDR_PROFILE_STATE_DIR") or home / ".local/state/ai_env/herdr"),
                   profiles_config=profiles_config, profiles=load_profiles(profiles_config),
                   backups=state_root / "ai_env/backups/herdr", stamp=time.strftime("%Y%m%d-%H%M%S"),
                   theme_override=os.environ.get("HERDR_PROFILE_THEME") or None,
                   launcher=os.environ.get("HERDR_LAUNCHER") or None, app_bin=os.environ.get("HERDR_APP_BIN") or None)

    def app_home(self, profile: Profile) -> Path:
        return self.home / ".config" / profile.home_name

    def theme(self, profile: Profile) -> Optional[str]:
        return self.theme_override or profile.theme

    @property
    def icons_dir(self) -> Path:
        return self.state_dir / "icons"

    @property
    def main(self) -> Profile:
        return next(profile for profile in self.profiles if profile.is_main)

    def find(self, name: str) -> Profile:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise UnknownProfile(f"unknown profile {name!r}; known: {', '.join(p.name for p in self.profiles)}")


# ---- the local profile table

def require_text(entry: dict, key: str, where: str, optional: bool = False) -> Optional[str]:
    value = entry.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{where}: {key!r} must be a non-empty string, got {value!r}")
    return value.strip()


def require_folder_name(value: str, where: str, key: str) -> str:
    if "/" in value or "\\" in value or value in (".", ".."):
        raise ProfileError(f"{where}: {key!r} must be a plain folder or id name without path separators, got {value!r}")
    return value


def parse_profile(entry: object, index: int, is_main: bool) -> Profile:
    where = f"profile #{index + 1}"
    if not isinstance(entry, dict):
        raise ProfileError(f"{where} must be a JSON object, got {entry!r}")
    name = require_text(entry, "name", where)
    where = f"profile {name!r}"
    color = require_text(entry, "color", where)
    if not COLOR_PATTERN.fullmatch(color):
        raise ProfileError(f"{where}: 'color' must look like #rrggbb, got {color!r}")
    label = require_text(entry, "label", where, optional=True) or name[:2].upper()
    home_name = MAIN_HOME_NAME if is_main else (require_text(entry, "home", where, optional=True) or HOME_NAME_PREFIX + name)
    app_id = MAIN_HOME_NAME if is_main else (require_text(entry, "app_id", where, optional=True) or home_name)
    return Profile(name=name, home_name=require_folder_name(home_name, where, "home"), theme=require_text(entry, "theme", where, optional=True),
                   app_id=require_folder_name(app_id, where, "app_id"), label=label, color=color.lower(), is_main=is_main,
                   stop_prompt=require_text(entry, "stop_prompt", where, optional=True))


def check_unique(profiles: Sequence[Profile], field: str) -> None:
    seen: dict = {}
    for profile in profiles:
        value = getattr(profile, field)
        if value in seen:
            raise ProfileError(f"profiles {seen[value]!r} and {profile.name!r} share the same {field} {value!r}; each must be unique")
        seen[value] = profile.name


def parse_profiles(data: dict, source: Path) -> tuple:
    """The Profile entries of a parsed table (see profiles.example.json); raises ProfileError naming the bad value."""
    entries = data.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise ProfileError(f"{source}: 'profiles' must be a non-empty list, got {entries!r}")
    names = [entry.get("name") if isinstance(entry, dict) else None for entry in entries]
    main_name = data.get("main", names[0])
    if main_name not in names:
        raise ProfileError(f"{source}: 'main' is {main_name!r}, not the name of any profile ({', '.join(map(repr, names))})")
    profiles = tuple(parse_profile(entry, index, names[index] == main_name) for index, entry in enumerate(entries))
    for field in ("name", "home_name", "app_id"):
        check_unique(profiles, field)
    return profiles


def load_profiles(path: Path) -> tuple:
    """The profile table in `path`; a missing file tells the owner to copy the example."""
    if not path.is_file():
        raise ProfileError(f"no profile table at {path}: copy {PROFILES_EXAMPLE} there and edit the names, colours and labels "
                           f"(or point HERDR_PROFILES_CONFIG at your own file)")
    return parse_profiles(read_json_object(path), path)


# ---- the app binary and the start command

def app_binary(config: Config) -> Optional[str]:
    return config.app_bin or shutil.which("herdr-app")


def start_command(config: Config, extra: Sequence[str]) -> list:
    """The argv that opens the window: the launcher if set, else the app binary; `extra` is appended."""
    if config.launcher:
        return shlex.split(config.launcher) + list(extra)
    binary = app_binary(config)
    if not binary:
        raise ProfileError("no way to start Herdr: set HERDR_LAUNCHER or HERDR_APP_BIN, or put `herdr-app` on PATH")
    return [binary] + list(extra)


def child_env(config: Config, profile: Profile) -> dict:
    return {**os.environ, "HERDR_APP_HOME": str(config.app_home(profile)), "HERDR_PROFILE": profile.name, "HERDR_APP_ID": profile.app_id}


def window_running(config: Config, profile: Profile) -> bool:
    """True when `herdr-app status` answers (exit 0) for this profile's home."""
    binary = app_binary(config)
    if not binary:
        return False
    try:
        res = subprocess.run([binary, "status"], env=child_env(config, profile), capture_output=True, timeout=STATUS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


# ---- small file helpers

def read_json_object(path: Path) -> dict:
    """The JSON object in `path`; raises ProfileError when it is not valid JSON or not an object."""
    try:
        data = json.loads(path.read_text())
    except ValueError as err:
        raise ProfileError(f"{path} is not valid JSON ({err}); fix or remove it") from err
    if not isinstance(data, dict):
        raise ProfileError(f"{path} holds {type(data).__name__}, not a JSON object")
    return data


def json_text(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def say(dry_run: bool, text: str) -> None:
    print(f"{'would: ' if dry_run else ''}{text}")


# ---- the launch steps

def seed_settings(config: Config, profile: Profile) -> dict:
    """The first-start settings: the main home's settings.json (or {}) plus the profile's theme."""
    main_settings = config.app_home(config.main) / "settings.json"
    try:
        settings = read_json_object(main_settings) if main_settings.is_file() else {}
    except ProfileError:
        settings = {}
    if config.theme(profile):
        settings["theme"] = config.theme(profile)
    return settings


def seed_home(config: Config, profile: Profile, dry_run: bool) -> None:
    """Step 1: make a non-main home ready (mods, layout, shared.json, first-start copies)."""
    home = config.app_home(profile)
    say(dry_run, f"mkdir -p {home}")
    say(dry_run, f"deploy mods and layout into {home} (herdr_sync.py deploy)")
    if not dry_run:
        home.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([sys.executable, str(REPO / "herdr_sync.py"), "deploy"], capture_output=True, text=True,
                             env={**os.environ, "HERDR_APP_HOME": str(home)})
        if res.returncode != 0:
            raise ProfileError(f"herdr_sync.py deploy failed for {home} (exit {res.returncode}):\n{res.stdout}{res.stderr}")
    shared = home / "shared.json"
    if not shared.exists():
        say(dry_run, f"write {shared} with layout {LAYOUT}")
        if not dry_run:
            shared.write_text(json_text({"layout": LAYOUT}))
    if (home / SEEDED).exists():
        return
    main_keys = config.app_home(config.main) / "keys.json"
    if main_keys.is_file():
        say(dry_run, f"first start: copy {main_keys} -> {home / 'keys.json'}")
        if not dry_run:
            shutil.copy2(main_keys, home / "keys.json")
    say(dry_run, f"first start: write {home / 'settings.json'} (main settings, theme {config.theme(profile) or 'unchanged'})")
    if not dry_run:
        (home / "settings.json").write_text(json_text(seed_settings(config, profile)))
        (home / SEEDED).touch()


def write_profile_marks(config: Config, profile: Profile, dry_run: bool) -> None:
    """Step 2: `profile.name` and `profile.color`, one line each, and `profile.stop_prompt` (the whole prompt; the file
    is removed when the profile has none); the mods read them."""
    marks = [(PROFILE_NAME, profile.name), (PROFILE_COLOR, profile.color), (PROFILE_STOP_PROMPT, profile.stop_prompt)]
    for filename, value in marks:
        target = config.app_home(profile) / filename
        if value is None:
            say(dry_run, f"remove {target} (none set)")
            if not dry_run:
                target.unlink(missing_ok=True)
            continue
        say(dry_run, f"write {target}: {value if len(value) < 60 else value[:57] + '...'}")
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value + "\n")


def ensure_profiles_file(config: Config, dry_run: bool) -> None:
    """Step 3: create profiles.json (dir 0700, file 0600) when missing; an existing file is never touched."""
    target = config.state_dir / "profiles.json"
    if target.exists():
        print(f"{target} exists, left as it is")
        return
    say(dry_run, f"create {target}")
    if dry_run:
        return
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(json_text({"default": config.main.name, "spaces": {}}))


def keys_additions(keys: dict) -> dict:
    """Bindings to add to the layout section: those whose `global` value is still Herdr's default and that the section lacks."""
    global_keys = keys.get("global") if isinstance(keys.get("global"), dict) else {}
    section = keys.get(KEYS_SECTION) if isinstance(keys.get(KEYS_SECTION), dict) else {}
    return {key: new for key, (default, new) in KEY_DEFAULTS.items() if global_keys.get(key) == default and key not in section}


def merge_keys(config: Config, profile: Profile, dry_run: bool) -> None:
    """Step 4: add the space-tree bindings to <home>/keys.json (backup first); nothing to do when absent or up to date."""
    keys_file = config.app_home(profile) / "keys.json"
    if not keys_file.is_file():
        print(f"{keys_file} is missing, keys left alone")
        return
    keys = read_json_object(keys_file)
    additions = keys_additions(keys)
    if not additions:
        print(f"{keys_file}: section {KEYS_SECTION} already up to date")
        return
    say(dry_run, f"add {len(additions)} binding(s) to section {KEYS_SECTION} of {keys_file}: {', '.join(sorted(additions))}")
    if dry_run:
        return
    backup = config.backups / config.stamp / f"keys-{profile.name}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(keys_file, backup)
    section = keys.get(KEYS_SECTION) if isinstance(keys.get(KEYS_SECTION), dict) else {}
    keys[KEYS_SECTION] = {**section, **additions}
    keys_file.write_text(json_text(keys))
    print(f"keys.json backed up to {backup}")


def start_window(config: Config, profile: Profile, extra: Sequence[str], dry_run: bool) -> None:
    """Step 5: start the window detached unless one already answers for this home."""
    if window_running(config, profile):
        print(f"already running ({config.app_home(profile)})")
        return
    argv = start_command(config, extra)
    log = config.state_dir / f"launch-{profile.name}.log"
    say(dry_run, f"start (HERDR_APP_HOME={config.app_home(profile)} HERDR_PROFILE={profile.name}): {shlex.join(argv)} > {log}")
    if dry_run:
        return
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(log, "ab") as out:
        process = subprocess.Popen(argv, env=child_env(config, profile), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                   start_new_session=True)
    print(f"started pid {process.pid}, log {log}")


def launch(config: Config, profile: Profile, extra: Sequence[str] = (), dry_run: bool = False) -> int:
    if not profile.is_main:
        seed_home(config, profile, dry_run)
    write_profile_marks(config, profile, dry_run)
    ensure_profiles_file(config, dry_run)
    merge_keys(config, profile, dry_run)
    start_window(config, profile, extra, dry_run)
    return 0


# ---- deploy

def deploy_all(config: Config, dry_run: bool = False) -> int:
    """Run herdr_sync.py deploy for every profile home that exists (each home keeps its own copy of the mods)."""
    failed = 0
    for profile in config.profiles:
        home = config.app_home(profile)
        if not home.is_dir():
            print(f"{profile.name}: no home yet ({home}), skipped")
            continue
        args = [sys.executable, str(REPO / "herdr_sync.py"), "deploy", *(["--dry-run"] if dry_run else [])]
        res = subprocess.run(args, capture_output=True, text=True, env={**os.environ, "HERDR_APP_HOME": str(home)})
        print(f"{profile.name}: {'deployed' if res.returncode == 0 else 'FAILED'} ({home})")
        if res.returncode != 0:
            failed += 1
            print(res.stdout + res.stderr, file=sys.stderr)
    return 1 if failed else 0


# ---- desktop entries (taskbar icon and name per profile)

ICON_SIZES = (48, 256)
MAKE_ICONS = REPO / "icons" / "make_icons.py"


def icon_path(config: Config, profile: Profile, size: int) -> Path:
    return config.icons_dir / f"{profile.name}-{size}.png"


def ensure_icons(config: Config) -> None:
    """Draw the icons into the state dir (never the repo) when one is missing or older than the profile table."""
    table_time = config.profiles_config.stat().st_mtime
    current = all(icon_path(config, p, size).is_file() and icon_path(config, p, size).stat().st_mtime >= table_time
                  for p in config.profiles for size in ICON_SIZES)
    if current:
        return
    res = subprocess.run([sys.executable, str(MAKE_ICONS)], capture_output=True, text=True,
                         env={**os.environ, "HERDR_PROFILES_CONFIG": str(config.profiles_config),
                              "HERDR_PROFILE_STATE_DIR": str(config.state_dir)})
    if res.returncode != 0:
        raise ProfileError(f"drawing the icons failed (exit {res.returncode}; icons/make_icons.py needs Pillow):\n{res.stdout}{res.stderr}")


def desktop_text(profile: Profile, exec_line: str) -> str:
    return (
        "[Desktop Entry]\nType=Application\n"
        f"Name=Herdr · {profile.name}\nComment=Herdr window of the {profile.name} profile\n"
        f"Exec={exec_line}\nIcon={profile.app_id}\nTerminal=false\nCategories=Development;\n"
        f"StartupWMClass={profile.app_id}\n"
    )


def write_desktop(config: Config, out: Path, exec_template: Optional[str]) -> int:
    """Write `<out>/share/applications/<app id>.desktop` and the icons under `<out>/share/icons/hicolor/` for every
    profile (drawing the icons first when needed); installing them is left to the caller (a system folder needs root)."""
    ensure_icons(config)
    for profile in config.profiles:
        exec_line = (exec_template.format(profile=profile.name) if exec_template
                     else f"{shlex.quote(sys.executable)} {shlex.quote(str(REPO / 'herdr_profile.py'))} launch {profile.name}")
        entry = out / "share" / "applications" / f"{profile.app_id}.desktop"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(desktop_text(profile, exec_line))
        for size in ICON_SIZES:
            target = out / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{profile.app_id}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon_path(config, profile, size), target)
    print(f"written to {out}/share (a .desktop and icons per profile). Install:")
    print(f"  Linux, own user (no root):   cp -r {out}/share/. ~/.local/share/")
    print(f"  system-wide (when the desktop reads icons only there; needs root rights):  cp -r {out}/share/. /usr/share/")
    return 0


# ---- list / status

def yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def list_profiles(config: Config) -> int:
    print(f"{'profile':<18}{'home':<52}{'exists':<8}running")
    for profile in config.profiles:
        home = config.app_home(profile)
        print(f"{profile.name:<18}{str(home):<52}{yes_no(home.is_dir()):<8}{yes_no(home.is_dir() and window_running(config, profile))}")
    return 0


def show_status(config: Config, names: Sequence[str]) -> int:
    for profile in [config.find(n) for n in names] if names else config.profiles:
        home = config.app_home(profile)
        written = (home / PROFILE_NAME).read_text().strip() if (home / PROFILE_NAME).is_file() else "(missing)"
        written_color = (home / PROFILE_COLOR).read_text().strip() if (home / PROFILE_COLOR).is_file() else "(missing)"
        keys_file = home / "keys.json"
        keys = ("no keys.json" if not keys_file.is_file()
                else "up to date" if not keys_additions(read_json_object(keys_file)) else "bindings to add")
        print(f"{profile.name}{' (main)' if profile.is_main else ''}\n  home: {home} (exists: {yes_no(home.is_dir())})\n"
              f"  theme: {config.theme(profile) or 'unchanged'}   seeded: {yes_no((home / SEEDED).exists())}   "
              f"profile.name: {written}\n  color: {profile.color} (label {profile.label}, profile.color: {written_color})\n  keys section {KEYS_SECTION}: {keys}\n"
              f"  window running: {yes_no(home.is_dir() and window_running(config, profile))}")
    return 0


def split_extra(argv: Sequence[str]) -> tuple:
    """(arguments for this tool, arguments after `--` for the app)."""
    argv = list(argv)
    return (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])


def main(argv: Optional[Sequence[str]] = None) -> int:
    own, extra = split_extra(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = ap.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("profile", nargs="*")
    launch_parser = commands.add_parser("launch")
    launch_parser.add_argument("profile")
    launch_parser.add_argument("--dry-run", action="store_true")
    desktop_parser = commands.add_parser("desktop")
    desktop_parser.add_argument("--out", default=None, help="staging folder (default: <state dir>/desktop)")
    desktop_parser.add_argument("--exec", dest="exec_template", default=None,
                                help="Exec line template with {profile}, e.g. '/path/herdr-launch-{profile}.sh'")
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(own)
    if extra and args.command != "launch":
        ap.error("arguments after `--` are only for launch")
    try:
        config = Config.from_env()
        if args.command == "list":
            return list_profiles(config)
        if args.command == "status":
            return show_status(config, args.profile)
        if args.command == "deploy":
            return deploy_all(config, args.dry_run)
        if args.command == "desktop":
            return write_desktop(config, Path(args.out) if args.out else config.state_dir / "desktop", args.exec_template)
        return launch(config, config.find(args.profile), extra, args.dry_run)
    except ProfileError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2 if isinstance(err, UnknownProfile) else 1


if __name__ == "__main__":
    sys.exit(main())
