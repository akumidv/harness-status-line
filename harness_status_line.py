#!/usr/bin/env python3
"""harness-status-line: extract the *status-line parts* of coding-agent harnesses into this repo,
and deploy them back onto a machine. Never copies whole configs - only the status-line bits.

  python3 harness_status_line.py status                 # live vs repo, per harness
  python3 harness_status_line.py extract [--dry-run]    # live config  -> repo   (the collect step)
  python3 harness_status_line.py deploy  [--dry-run]    # repo         -> live config (backs up what it changes)
  options: --only claude qwen codex mcode (or a profile name such as codex-personal)

Harnesses and what is extracted (see README.md):
  claude  ~/.claude   statusline-command.sh, statusline-analyze.py, settings.json -> "statusLine"
  qwen    ~/.qwen     statusline-command.sh, settings.json -> "ui.statusLine"
  codex   ~/.codex, ~/.codex-personal   config.toml -> [tui] status_line* keys
  mcode   ~/.minimax  config.yaml -> tui.statusLine block
Set HSL_HOME to run against another home directory (testing). Python 3.11+, standard library only.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOME = Path(os.environ.get('HSL_HOME') or Path.home())
STAMP = time.strftime('%Y%m%d-%H%M%S')

# name = target (profile) name; harness = repo sub-folder. Repo dir: <harness>/ or <harness>/<name>/ when they differ.
TARGETS = [
    dict(name='claude', harness='claude', home='.claude',
         files=['statusline-command.sh', 'statusline-analyze.py'],
         json=dict(file='settings.json', path=['statusLine'], fragment='settings.fragment.json')),
    dict(name='qwen', harness='qwen', home='.qwen',
         files=['statusline-command.sh'],
         json=dict(file='settings.json', path=['ui', 'statusLine'], fragment='settings.fragment.json')),
    dict(name='codex', harness='codex', home='.codex',
         toml=dict(file='config.toml', table='tui', prefix='status_line', fragment='config.fragment.toml')),
    dict(name='codex-personal', harness='codex', home='.codex-personal',
         toml=dict(file='config.toml', table='tui', prefix='status_line', fragment='config.fragment.toml')),
    dict(name='mcode', harness='mcode', home='.minimax',
         yaml=dict(file='config.yaml', path=['tui', 'statusLine'], fragment='config.fragment.yaml')),
]

SECRET_KEY = re.compile(r'(api[_-]?key|token|secret|passw|credential|auth)', re.I)
SECRET_ASSIGN = re.compile(r'(api[_-]?key|token|secret|passw(or)?d)\s*[=:]\s*[\'"]?[A-Za-z0-9_\-./+]{16,}', re.I)


class Skip(Exception):
    pass


def repo_dir(t):
    return REPO / t['harness'] if t['name'] == t['harness'] else REPO / t['harness'] / t['name']


def live_dir(t):
    return HOME / t['home']


def guard(name, text):
    """Refuse to put anything that looks like a credential into the repo."""
    if SECRET_ASSIGN.search(text):
        raise SystemExit(f'refusing {name}: looks like it contains a credential')


# ----------------------------------------------------------------------------- JSON (nested key path)
def json_get(obj, path):
    for k in path:
        if not isinstance(obj, dict) or k not in obj:
            raise Skip(f'no "{".".join(path)}" in live config')
        obj = obj[k]
    return obj


def json_nest(path, value):
    for k in reversed(path):
        value = {k: value}
    return value


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def check_keys(node, where):
    if isinstance(node, dict):
        for k, v in node.items():
            if SECRET_KEY.search(k) and not isinstance(v, (dict, list, bool)):
                raise SystemExit(f'refusing {where}: key "{k}" looks like a credential')
            check_keys(v, where)
    elif isinstance(node, list):
        for v in node:
            check_keys(v, where)


# ----------------------------------------------------------------------------- TOML ([table] keys with a prefix)
def toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return '[' + ', '.join(toml_value(x) for x in v) + ']'
    raise TypeError(f'unsupported TOML value {v!r}')


def toml_fragment(text, spec):
    data = tomllib.loads(text).get(spec['table'], {})
    keys = {k: v for k, v in data.items() if k.startswith(spec['prefix'])}
    if not keys:
        raise Skip(f'no [{spec["table"]}] {spec["prefix"]}* keys in live config')
    check_keys(keys, 'toml fragment')
    return f'[{spec["table"]}]\n' + ''.join(f'{k} = {toml_value(v)}\n' for k, v in keys.items())


def toml_apply(text, spec, fragment):
    want = tomllib.loads(fragment)[spec['table']]
    lines = text.split('\n')
    head = next((i for i, l in enumerate(lines) if l.strip() == f'[{spec["table"]}]'), None)
    if head is None:
        body = f'[{spec["table"]}]\n' + ''.join(f'{k} = {toml_value(v)}\n' for k, v in want.items())
        out = text.rstrip('\n') + '\n\n' + body
    else:
        end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
        for k, v in want.items():
            new = f'{k} = {toml_value(v)}'
            idx = next((i for i in range(head + 1, end) if re.match(rf'\s*{re.escape(k)}\s*=', lines[i])), None)
            if idx is not None:
                lines[idx] = new                      # existing key: replace in place
            else:                                     # new key: append after the table's last non-blank line, in fragment order
                last = max((i for i in range(head, end) if lines[i].strip()), default=head)
                lines.insert(last + 1, new)
                end += 1
        out = '\n'.join(lines)
    tomllib.loads(out)            # must stay valid TOML
    return out


# ----------------------------------------------------------------------------- YAML (textual block under a key path)
def yaml_find(lines, path):
    """Return (start, end) line range of the block at key `path` (inclusive start line, exclusive end)."""
    lo, hi, indent = 0, len(lines), -1
    rng = None
    for depth, key in enumerate(path):
        rng = None
        i = lo
        while i < hi:
            m = re.match(rf'^(\s*){re.escape(key)}:\s*(#.*)?$', lines[i])
            if m and len(m.group(1)) > indent and (depth > 0 or len(m.group(1)) == 0):
                ind = len(m.group(1))
                j = i + 1
                while j < hi and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > ind):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():      # trailing blank lines belong to the next block
                    j -= 1
                rng = (i, j)
                indent = ind
                break
            i += 1
        if rng is None:
            return None
        lo, hi = rng[0] + 1, rng[1]
    return rng


def yaml_fragment(text, spec):
    lines = text.split('\n')
    rng = yaml_find(lines, spec['path'])
    if rng is None:
        raise Skip(f'no "{".".join(spec["path"])}" block in live config')
    block = [l for l in lines[rng[0]:rng[1]]]
    while block and not block[-1].strip():
        block.pop()
    # re-emit as a standalone fragment: parent keys first, original indentation kept for the block
    out = []
    for depth, key in enumerate(spec['path'][:-1]):
        out.append('  ' * depth + f'{key}:')
    base = len(re.match(r'\s*', block[0]).group(0))
    out += ['  ' * (len(spec['path']) - 1) + l[base:] for l in block]
    frag = '\n'.join(out) + '\n'
    for l in frag.split('\n'):
        m = re.match(r'\s*([^:#]+):', l)
        if m and SECRET_KEY.search(m.group(1)) and l.split(':', 1)[1].strip():
            raise SystemExit(f'refusing yaml fragment: key "{m.group(1)}" looks like a credential')
    return frag


def yaml_apply(text, spec, fragment):
    path = spec['path']
    new_block = fragment.rstrip('\n').split('\n')[len(path) - 1:]         # the innermost block, with its indentation
    lines = text.split('\n')
    rng = yaml_find(lines, path)
    if rng is not None:
        ind = len(re.match(r'\s*', lines[rng[0]]).group(0))
        base = len(re.match(r'\s*', new_block[0]).group(0))
        lines[rng[0]:rng[1]] = [' ' * ind + l[base:] for l in new_block]
        return '\n'.join(lines)
    parent = yaml_find(lines, path[:-1]) if len(path) > 1 else None
    if parent is not None:
        ind = len(re.match(r'\s*', lines[parent[0]]).group(0)) + 2
        base = len(re.match(r'\s*', new_block[0]).group(0))
        lines[parent[1]:parent[1]] = [' ' * ind + l[base:] for l in new_block]
        return '\n'.join(lines)
    return text.rstrip('\n') + '\n' + fragment


# ----------------------------------------------------------------------------- collect (live -> {repo filename: bytes})
def collect(t):
    out, notes = {}, []
    live = live_dir(t)
    for name in t.get('files', []):
        p = live / name
        if p.is_file():
            data = p.read_bytes()
            guard(str(p), data.decode('utf-8', 'replace'))
            out[name] = data
        else:
            notes.append(f'{name}: missing in live')
    for kind in ('json', 'toml', 'yaml'):
        spec = t.get(kind)
        if not spec:
            continue
        p = live / spec['file']
        try:
            if not p.is_file():
                raise Skip(f'{spec["file"]} missing in live')
            text = p.read_text()
            if kind == 'json':
                frag = json.dumps(json_nest(spec['path'], json_get(json.loads(text), spec['path'])), indent=2, ensure_ascii=False) + '\n'
                check_keys(json.loads(frag), spec['fragment'])
            elif kind == 'toml':
                frag = toml_fragment(text, spec)
            else:
                frag = yaml_fragment(text, spec)
            out[spec['fragment']] = frag.encode()
        except Skip as e:
            notes.append(f'{spec["fragment"]}: {e}')
    return out, notes


# ----------------------------------------------------------------------------- commands
def selected(only):
    ts = [t for t in TARGETS if not only or t['name'] in only or t['harness'] in only]
    if not ts:
        raise SystemExit('nothing matches --only ' + ' '.join(only))
    return ts


def cmd_extract(args):
    for t in selected(args.only):
        data, notes = collect(t)
        d = repo_dir(t)
        print(f'[{t["name"]}]  {live_dir(t)} -> {d.relative_to(REPO)}/')
        for name, content in data.items():
            dst = d / name
            old = dst.read_bytes() if dst.exists() else None
            state = 'unchanged' if old == content else ('new' if old is None else 'updated')
            print(f'  {state:9} {name}')
            if state != 'unchanged' and not args.dry_run:
                d.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
                if name.endswith(('.sh', '.py')):
                    dst.chmod(0o755)
        for n in notes:
            print(f'  skipped   {n}')


def backup(p):
    if p.exists():
        b = p.with_name(p.name + f'.bak-hsl-{STAMP}')
        shutil.copy2(p, b)
        return b.name
    return None


def write_live(p, new_text, old_text, dry, label):
    if old_text == new_text:
        print(f'  unchanged {label}')
        return
    if dry:
        print(f'  would change {label}')
        sys.stdout.writelines('    ' + l for l in difflib.unified_diff(
            (old_text or '').splitlines(True), new_text.splitlines(True), 'live', 'repo', n=1))
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    b = backup(p)
    p.write_text(new_text)
    print(f'  changed   {label}' + (f'   (backup: {b})' if b else '   (created)'))


def cmd_deploy(args):
    for t in selected(args.only):
        d, live = repo_dir(t), live_dir(t)
        if not d.is_dir():
            print(f'[{t["name"]}] nothing in repo ({d.relative_to(REPO)}/) - skipped')
            continue
        print(f'[{t["name"]}]  {d.relative_to(REPO)}/ -> {live}')
        for name in t.get('files', []):
            src = d / name
            if not src.is_file():
                continue
            dst = live / name
            new = src.read_bytes().decode()
            old = dst.read_text() if dst.exists() else None
            write_live(dst, new, old, args.dry_run, name)
            if not args.dry_run and dst.exists() and name.endswith(('.sh', '.py')):
                dst.chmod(0o755)
        for kind in ('json', 'toml', 'yaml'):
            spec = t.get(kind)
            if not spec or not (d / spec['fragment']).is_file():
                continue
            frag = (d / spec['fragment']).read_text()
            dst = live / spec['file']
            old = dst.read_text() if dst.exists() else None
            if kind == 'json':
                check_keys(json.loads(frag), spec['fragment'])
                cur = json.loads(old) if old else {}
                new = json.dumps(deep_merge(cur, json.loads(frag)), indent=2, ensure_ascii=False) + '\n'
            elif kind == 'toml':
                new = toml_apply(old or '', spec, frag)
            else:
                new = yaml_apply(old or '', spec, frag)
            write_live(dst, new, old, args.dry_run, f'{spec["file"]}  <- {spec["fragment"]}')


def same(name, a, b):
    """Fragments compare by parsed value (key order / formatting do not matter); scripts byte for byte."""
    try:
        if name.endswith('.json'):
            return json.loads(a) == json.loads(b)
        if name.endswith('.toml'):
            return tomllib.loads(a.decode()) == tomllib.loads(b.decode())
    except ValueError:
        pass
    return a == b


def cmd_status(args):
    for t in selected(args.only):
        d = repo_dir(t)
        data, notes = collect(t)
        print(f'[{t["name"]}]')
        names = set(data) | {p.name for p in d.glob('*') if p.is_file()} if d.is_dir() else set(data)
        for name in sorted(names):
            in_repo = (d / name).read_bytes() if (d / name).is_file() else None
            live = data.get(name)
            if live is None:
                state = 'only in repo (not in live)'
            elif in_repo is None:
                state = 'only in live (run extract)'
            else:
                state = 'in sync' if same(name, live, in_repo) else 'DIFFERS (extract to take live, deploy to push repo)'
            print(f'  {name:28} {state}')
        for n in notes:
            print(f'  -        {n}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, fn in (('extract', cmd_extract), ('deploy', cmd_deploy), ('status', cmd_status)):
        p = sub.add_parser(name)
        p.add_argument('--only', nargs='+', metavar='NAME', help='harness or profile: claude qwen codex codex-personal mcode')
        if name != 'status':
            p.add_argument('--dry-run', action='store_true')
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
