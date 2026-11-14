#!/usr/bin/env python3
"""Status-line analyzer shared by all harnesses (Claude Code, Qwen Code, mcode, OpenCode, pi).

Two kinds of connection (the API endpoint a harness talks to, keyed by host):
  * limited  - a subscription with 5h/weekly limits (Claude Code passes rate_limits): load matrix + burn speed,
               calibrated to the weekly limit % (the original Claude analysis below, files in the connection's folder ~/.local/state/harness-status-line/api.anthropic.com/);
  * free / metered - no limits (own gateway, pay-per-token): load matrix in tokens (free) or USD (metered) and
               a SPEED matrix - how fast the models answer in each hour of the week vs their own median, pooled
               over models = the load of the endpoint. Files in ~/.local/state/harness-status-line/<host>/conn.
Every connection (provider) has its own folder there; harnesses that share an endpoint share its folder.
Every connection also gets the speed matrix and per-model median speeds (Claude: the colour of the speed).

  statusline-analyze.py              daily profile (background, nice 19)
  statusline-analyze.py --collect    fast collector (every ~5 min): activity hours of ALL sessions (VS Code
                                     extension, OpenCode, pi, ...), current pace and speed per connection -> tmpfs

Claude part: weekly token-COST profile in 1-hour slots, calibrated to the weekly limit %.

Reads Claude Code transcripts (read-only, incrementally: only bytes added since the last run),
weights every assistant message by price (per model and token type) and buckets it into the
168 hourly slots of a week. The weekly limit % is account-wide, so the absolute scale
("% of the weekly limit per USD") is calibrated from this week's cost vs the current wk%.

Writes ~/.local/state/harness-status-line/api.anthropic.com/profile (shell-readable) and profile.json (inspection).
Run in the background once a day by statusline-command.sh. stdlib only.
usage: statusline-analyze.py [--wk-x100 N --reset EPOCH]   (override calibration, for tests; not persisted)
       statusline-analyze.py --burn   (read-only report: does the burn rate differ by hour / mode / weekday / day?)
"""
import bisect, collections, datetime, fcntl, glob, itertools, json, os, random, re, sys, time, urllib.error, urllib.request

CLAUDE = os.path.expanduser('~/.claude')                         # the harness's own dir: transcripts + credentials only, nothing of ours is kept there
PROJECTS = os.path.join(CLAUDE, 'projects')
HOME = os.path.expanduser('~')
HSL_STATE = os.path.join(HOME, '.local/state/harness-status-line')        # durable data; one folder per CONNECTION (provider = endpoint host)
ANTHROPIC = 'api.anthropic.com'
ANTH_DIR = os.path.join(HSL_STATE, ANTHROPIC)                    # the Claude account (limited connection): limit axis, token axis, cost profile, usage poll
STATE = os.path.join(ANTH_DIR, 'analyze-state.json')
OUT = os.path.join(ANTH_DIR, 'profile')
MEASURES = os.path.join(ANTH_DIR, 'measures.jsonl')              # one record per completed UTC day (long-term log)
STATE_VERSION = 7                # 5: per-message start/end (speed); 6: + cache write 1h (5m = cw - cw1h); 7: + effort, speed, thinking
HISTORY = os.path.join(ANTH_DIR, 'history')            # LIMIT axis: "epoch wk_x100 5h_x100 wk_reset 5h_reset"
RESET_DROP = 300                 # x100: a wk% drop this big within one weekly window = manual reset (history has 1-point backward steps = noise)
TOKENS = os.path.join(ANTH_DIR, 'tokens')              # TOKEN axis: raw tokens per 5-min bucket, model, effort, speed (never trimmed)
TOK_FIELDS = ('input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h')
TOK_BUCKET = 300
# OUTSIDE USE: the limits are account-wide, the transcripts are this machine's (+ imports). Copies of another machine's
# ~/.claude/projects go to IMPORTS/<host>/ (any tree of *.jsonl): their messages count as mine (cost, matrices,
# calibration), their tokens go to their own axis TOKENS@<host>. Use nobody imported shows as an excess of the 5h%
# step over the local cost: hours far above the median 5h%-per-$ are flagged and kept out of calibration and the fit.
IMPORTS = os.path.join(HSL_STATE, 'imports')
OUT_MIN_USD, OUT_RATIO, OUT_MIN_PTS = 1.0, 2.0, 3.0   # median over hours with >= $1; flag: 5h step > 2x expected and >= 3 points over it
OUT_MIN_HOURS = 24              # no flags until the median rests on this many hours (outside use must stay the minority)
SLOT, SLOTS = 3600, 168          # 1-hour grid; 168 slots = one week. slot index = (epoch // 3600) % 168
KEEP_DAYS, PROFILE_DAYS, FROZEN_DAYS = 15, 14, 120   # frozen = per-slot cost kept after per-message detail expires
HSL_CFG = os.path.join(HSL_STATE, 'config.json')                  # optional overrides (billing, prices); the old ~/.config/harness-status-line/connections.json is read when this one is missing
HSL_CFG_OLD = os.path.join(HOME, '.config/harness-status-line/connections.json')
# LIMIT POLL: an idle CLI session keeps the limits of its last response and the VS Code extension has no status line, so
# the limit axis had gaps. --collect asks the account's usage endpoint (the one behind /usage; undocumented) with the
# OAuth access token of ~/.claude/.credentials.json: read in memory only, never printed, stored or refreshed (Claude
# Code refreshes it; an expired token = no poll). Rows go to HISTORY in the status line's format. Failures back off.
USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CREDENTIALS = os.path.join(CLAUDE, '.credentials.json')
POLL_MIN, POLL_MAX_BACKOFF, POLL_HEARTBEAT = 240, 3600, 1800   # seconds between polls; backoff cap; a row at least this often
POLL_REFUSED = (401, 403, 429)                                   # HTTP refusals: 3 tries with a growing wait, then hourly; they never write the stop file
POLL_STOP_AFTER, COLLECT_STOP_AFTER = 3, 3                      # consecutive other HTTP errors / collector crashes before it stops for good
SPEED_DAYS = 42                  # speed matrix window (needs more weeks than the load: one hour of the week = 1 sample/week)
SPEED_K = 4.0                    # pseudo-requests at index 1 per hour of the week (shrinkage of the speed matrix)
MIN_OUT, MIN_DUR = 50, 1.0       # a response counts for speed only from 50 output tokens and 1 s
BUSY, LIGHT = 0.92, 1.08         # speed index thresholds of the load modes (busy = slower than usual)

# USD per 1M tokens: input, output, cache read, cache write 5m, cache write 1h. First match wins.
# Source: Anthropic price table. ASSUMED (not in the table): cache write 1.25x / 2x of input,
# Haiku 5.5 cache read 0.1x. Only ratios matter - the absolute scale is calibrated against wk%.
PRICES = [
    ('opus-5-5',  (4.00, 20.00, 0.20, 5.00, 8.00)),
    ('opus-5',    (5.00, 25.00, 0.50, 6.25, 10.00)),
    ('opus-4',    (5.00, 25.00, 0.50, 6.25, 10.00)),
    ('sonnet-5',  (2.00, 10.00, 0.20, 2.50, 4.00)),     # sonnet-5 and sonnet-5-5
    ('sonnet-4',  (3.00, 15.00, 0.30, 3.75, 6.00)),
    ('haiku-5',   (0.10, 0.50, 0.01, 0.125, 0.20)),
    ('haiku-4',   (1.00, 5.00, 0.10, 1.25, 2.00)),
]
DEFAULT_PRICE = (2.00, 10.00, 0.20, 2.50, 4.00)


def price_for(model):
    for key, p in PRICES:
        if key in (model or ''):
            return p
    return DEFAULT_PRICE


def cost(u, p):
    cc = u.get('cache_creation') or {}
    w5, w1 = cc.get('ephemeral_5m_input_tokens'), cc.get('ephemeral_1h_input_tokens')
    if w5 is None and w1 is None:
        w5, w1 = u.get('cache_creation_input_tokens') or 0, 0
    return ((u.get('input_tokens') or 0) * p[0] + (u.get('output_tokens') or 0) * p[1]
            + (u.get('cache_read_input_tokens') or 0) * p[2] + (w5 or 0) * p[3] + (w1 or 0) * p[4]) / 1e6


DEFAULT_SCHED = (13, 19, 8)     # UTC hours: peak start, peak end, eco end (weekdays); weekends = eco
SCHED = list(DEFAULT_SCHED)     # may be shifted by analyze_factors() when recent data clearly supports it
RECENT_DAYS = 42                # factors and boundaries are fitted on the most recent weeks only
SHIFT = 2                       # boundaries are searched within +-SHIFT hours of the default
PRELIM_PAIRS, PRELIM_LAMBDA = 8, 8.0   # preliminary mode factors: min wk pairs, shrinkage toward 1
SHIFT_MARGIN = 0.93             # a shifted schedule must cut the CV error by >7% vs the default one


def mode_of(t):   # same schedule as the status line (UTC): 2 peak, 0 eco, 1 normal
    d, h = int(t) // 86400, int(t) % 86400 // 3600
    dow = (d + 3) % 7 + 1
    if dow <= 5 and SCHED[0] <= h < SCHED[1]:
        return 2
    return 0 if (dow > 5 or h < SCHED[2]) else 1


def model_key(m):
    return re.sub(r'-\d{8}$', '', (m or 'unknown').replace('claude-', ''))


def read_samples():
    """History lines 'epoch wk_x100 five_x100 [reset]' -> [(t, wk, five or None)]."""
    out = []
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
                out.append((int(p[0]), int(p[1]), int(p[2]) if len(p) > 2 and p[2].isdigit() else None))
    except OSError:
        pass
    return out


def solve(A, b):
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-12:
            return None
        M[i], M[piv] = M[piv], M[i]
        d = M[i][i]
        for r in range(n):
            if r != i and M[r][i]:
                f = M[r][i] / d
                Mr, Mi = M[r], M[i]
                for c in range(i, n + 1):
                    Mr[c] -= f * Mi[c]
    return [M[i][n] / M[i][i] for i in range(n)]


# ---- burn factor grids: how fast the limit burns per unit of token cost, by time of week ----
def _dh(sw):
    t = sw * SLOT
    return (t % 86400) // 3600, (t % 86400) // SLOT, ((t // 86400 + 3) % 7 + 1) > 5      # hour, half-hour of day, weekend?


GRIDS = {                                      # name -> (bins, week-slot -> bin); ordered simple -> fine
    'flat':      (1,  lambda sw: 0),
    'schedule3': (3,  lambda sw: mode_of(sw * SLOT)),
}
GRID_MARGIN = 1.05                              # prefer the simpler grid unless a finer one wins by more than 5% (CV error)
LAMBDAS = (0.3, 3.0, 30.0)                      # ridge strength relative to mean(diag A); pulls bins toward "no effect"


def build_pairs(samples, buckets, skip=()):
    """One row per interval between consecutive history samples and per limit (wk, 5h):
    observed % step y and the token cost spent in the interval per week-slot."""
    pairs = []
    for (t1, w1, f1), (t2, w2, f2) in zip(samples, samples[1:]):
        dt = t2 - t1
        if dt <= 0:
            continue
        cs = {}
        for sl in range(t1 // SLOT, (t2 - 1) // SLOT + 1):
            ov = min(t2, (sl + 1) * SLOT) - max(t1, sl * SLOT)
            if ov > 0 and sl in buckets:
                cs[sl % SLOTS] = cs.get(sl % SLOTS, 0.0) + buckets[sl] * ov / SLOT
        if not cs or any(sl in skip for sl in range(t1 // SLOT, (t2 - 1) // SLOT + 1)):   # outside use: not my cost
            continue
        if dt <= 10800 and w2 >= w1:
            pairs.append({'lim': 'wk', 'y': (w2 - w1) / 100.0, 'cs': cs, 'day': t1 // 86400})
        if f1 is not None and f2 is not None and dt <= 18000 and f2 >= f1:      # a drop = new 5h window: skip
            pairs.append({'lim': '5h', 'y': (f2 - f1) / 100.0, 'cs': cs, 'day': t1 // 86400})
    for lim in ('wk', '5h'):        # each limit in its own units -> divide by its mean burn per $, so they can be pooled
        P = [p for p in pairs if p['lim'] == lim]
        sy, sc = sum(p['y'] for p in P), sum(sum(p['cs'].values()) for p in P)
        k = sy / sc if sy > 0 and sc > 0 else None
        for p in P:
            p['yn'] = p['y'] / k if k else None
    return [p for p in pairs if p.get('yn') is not None]


def fit_grid(pairs, binmap, nb, lam_rel):
    A, b = {}, [0.0] * nb
    diag = [0.0] * nb
    for p in pairs:
        c = {}
        for sw, v in p['cs'].items():
            c[binmap[sw]] = c.get(binmap[sw], 0.0) + v
        for i, ci in c.items():
            b[i] += ci * p['yn']
            for j, cj in c.items():
                A[(i, j)] = A.get((i, j), 0.0) + ci * cj
            diag[i] += ci * ci
    lam = lam_rel * (sum(diag) / nb or 1.0)
    M = [[A.get((i, j), 0.0) + (lam if i == j else 0.0) for j in range(nb)] for i in range(nb)]
    th = solve(M, [b[i] + lam for i in range(nb)])
    return [max(t, 0.0) for t in th] if th else [1.0] * nb


def sse(pairs, binmap, th):
    tot = 0.0
    for p in pairs:
        pred = sum(th[binmap[sw]] * v for sw, v in p['cs'].items())
        tot += (p['yn'] - pred) ** 2
    return tot


def folds_by_day(pairs, k):
    days = sorted({p['day'] for p in pairs})
    size = max(1, -(-len(days) // k))
    idx = {d: i // size for i, d in enumerate(days)}
    return [[p for p in pairs if idx[p['day']] == f] for f in range(max(idx.values()) + 1)]


def cv_error(pairs, name, lam_rel, k=5, binmap=None):
    nb, fn = GRIDS[name]
    binmap = binmap or [fn(sw) for sw in range(SLOTS)]
    fo = folds_by_day(pairs, k)
    tot = 0.0
    for i, test in enumerate(fo):
        train = [p for j, f in enumerate(fo) if j != i for p in f]
        th = [1.0] * nb if name == 'flat' else fit_grid(train, binmap, nb, lam_rel)
        tot += sse(test, binmap, th)
    return tot


def analyze_factors(samples, buckets, mean, min_pairs=150, min_cost=100.0, min_days=14, n_perm=20, skip=()):
    """Pick the time grid the data supports (cross-validated by day-blocks), fit it, report factors per week slot."""
    res = {'status': 'insufficient data', 'grid': None, 'factors_slot': None, 'mode_factors': [None] * 3}
    pairs = build_pairs(samples, buckets, skip)
    if pairs:
        last = max(p['day'] for p in pairs)
        pairs = [p for p in pairs if p['day'] > last - RECENT_DAYS]
    SCHED[:] = DEFAULT_SCHED
    res['schedule_utc'] = {'peak_start': SCHED[0], 'peak_end': SCHED[1], 'eco_end': SCHED[2], 'shifted': False}
    days = len({p['day'] for p in pairs})
    cost = sum(sum(p['cs'].values()) for p in pairs if p['lim'] == 'wk')
    res.update(pairs=len(pairs), days=days, cost_usd_in_wk_pairs=round(cost, 1),
               need='>=%d pairs, >=$%d, >=%d days' % (min_pairs, min_cost, min_days))
    if len(pairs) < min_pairs or cost < min_cost or days < min_days:
        # PRELIMINARY factors from what there is: a heavily shrunk fit (prior = 1), clipped to a sane range
        wk = [p for p in pairs if p['lim'] == 'wk']
        if len(wk) >= PRELIM_PAIRS:
            bm = [mode_of(sw * SLOT) for sw in range(SLOTS)]
            th = fit_grid(pairs, bm, 3, PRELIM_LAMBDA)
            ref = th[1] or 1.0
            f = [min(max(t / ref, 0.6), 1.6) for t in th]
            res['mode_factors'] = [round(min(f[0], 1.0), 2), 1.0, round(max(f[2], 1.0), 2)]   # prelim only: eco is never dearer, peak never cheaper than normal
            res['status'] = 'preliminary (%d pairs, shrunk toward 1)' % len(wk)
            res['prelim'] = True
        return res
    # ---- have the mode boundaries moved? try +-SHIFT hours around the default; keep a shift only if it is clearly better ----
    def sched_err():
        return cv_error(pairs, 'schedule3', 3.0)
    base_err = sched_err()
    best_err, best_s = base_err, tuple(DEFAULT_SCHED)
    for ps in range(DEFAULT_SCHED[0] - SHIFT, DEFAULT_SCHED[0] + SHIFT + 1):
        for pe in range(DEFAULT_SCHED[1] - SHIFT, DEFAULT_SCHED[1] + SHIFT + 1):
            for ee in range(DEFAULT_SCHED[2] - SHIFT, DEFAULT_SCHED[2] + SHIFT + 1):
                if not (ee < ps < pe) or (ps, pe, ee) == tuple(DEFAULT_SCHED):
                    continue
                SCHED[:] = (ps, pe, ee)
                e = sched_err()
                if e < best_err:
                    best_err, best_s = e, (ps, pe, ee)
    if best_s != tuple(DEFAULT_SCHED) and best_err < base_err * SHIFT_MARGIN:
        SCHED[:] = best_s
        res['schedule_utc'] = {'peak_start': best_s[0], 'peak_end': best_s[1], 'eco_end': best_s[2], 'shifted': True,
                               'cv_error_vs_default': round(best_err / (base_err or 1.0), 3)}
    else:
        SCHED[:] = DEFAULT_SCHED
    cv, best_lam = {}, {}
    for name in GRIDS:
        if name == 'flat':
            cv[name], best_lam[name] = cv_error(pairs, 'flat', 0), 0
            continue
        errs = {lam: cv_error(pairs, name, lam) for lam in LAMBDAS}
        best_lam[name] = min(errs, key=errs.get)
        cv[name] = errs[best_lam[name]]
    base = cv['flat'] or 1.0
    res['cv_error_vs_flat'] = {n: round(v / base, 3) for n, v in cv.items()}
    # Event-driven samples end an interval exactly when the % steps, so "finer grid beats flat" is biased even with
    # NO time effect. A grid is accepted only if it beats flat by >5% AND beats the same grid with the slot->bin
    # labels shuffled (null), n_perm times. Everything else falls back to the simpler grid / flat.
    rng, ok = random.Random(7), {}
    nulls = {}
    for name in GRIDS:
        if name == 'flat':
            continue
        r = cv[name] / base
        if r >= 0.95:
            continue
        nb, fn = GRIDS[name]
        bm0 = [fn(sw) for sw in range(SLOTS)]
        nl = []
        for _ in range(n_perm):
            bm = bm0[:]
            rng.shuffle(bm)
            nl.append(cv_error(pairs, name, best_lam[name], binmap=bm) / base)
        nulls[name] = round(min(nl), 3)
        if r < min(nl):
            ok[name] = r
    res['null_best_ratio'] = nulls
    if ok:
        best_r = min(ok.values())
        chosen = next(n for n in GRIDS if n in ok and ok[n] <= best_r * GRID_MARGIN)
    else:
        chosen = 'flat'
    res['grid'] = chosen
    res['status'] = 'ok' if chosen != 'flat' else 'no time-of-day effect detected'

    def slot_factors(name):
        nb, fn = GRIDS[name]
        bm = [fn(sw) for sw in range(SLOTS)]
        th = fit_grid(pairs, bm, nb, best_lam[name]) if name != 'flat' else [1.0]
        ts = [th[bm[sw]] for sw in range(SLOTS)]
        w = [mean[sw] if mode_of(sw * SLOT) == 1 else 0.0 for sw in range(SLOTS)]
        ref = sum(t * x for t, x in zip(ts, w)) / (sum(w) or 1.0) or 1.0            # normal-schedule slots = 1
        return [round(min(max(t / ref, 0.05), 20.0), 3) for t in ts]
    fs = slot_factors(chosen)
    res['factors_slot'] = fs
    res['factors_by_hour_utc'] = {
        'weekday': [round(sum(fs[sw] for sw in range(SLOTS) if _dh(sw)[0] == h and not _dh(sw)[2]) / max(1, sum(1 for sw in range(SLOTS) if _dh(sw)[0] == h and not _dh(sw)[2])), 2) for h in range(24)],
        'weekend': [round(sum(fs[sw] for sw in range(SLOTS) if _dh(sw)[0] == h and _dh(sw)[2]) / max(1, sum(1 for sw in range(SLOTS) if _dh(sw)[0] == h and _dh(sw)[2])), 2) for h in range(24)],
    }
    if 'schedule3' in best_lam:                                       # the 3 modes of the assumed schedule, for the fallback display
        f3 = slot_factors('schedule3')
        res['mode_factors'] = [round(f3[next(sw for sw in range(SLOTS) if mode_of(sw * SLOT) == m)], 2) for m in range(3)]
        if chosen == 'flat':
            res['mode_factors'] = [1.0, 1.0, 1.0]                      # no time-of-day effect detected
    # ---- did the regime change? needs >= 28 days: older-half model vs newer data ----
    span = max(p['day'] for p in pairs) - min(p['day'] for p in pairs)
    res['span_days'] = span
    if span >= 28 and chosen != 'flat':
        mid = sorted(p['day'] for p in pairs)[len(pairs) // 2]
        old, new = [p for p in pairs if p['day'] < mid], [p for p in pairs if p['day'] >= mid]
        nb, fn = GRIDS[chosen]
        bm = [fn(sw) for sw in range(SLOTS)]
        th_old = fit_grid(old, bm, nb, best_lam[chosen])
        a_err = sse(new, bm, th_old)
        b_err = sum(sse(t, bm, fit_grid([p for j, f in enumerate(folds_by_day(new, 3)) if j != i for p in f], bm, nb, best_lam[chosen]))
                    for i, t in enumerate(folds_by_day(new, 3)))
        # EXPLORATORY: calibrated on synthetic data this ratio does not separate a shifted schedule from a stable one
        # (stable 0.8-1.2, shifted 1.0-1.35), so no verdict is given - only the number, for eyeballing over many weeks.
        res['regime_change'] = {'older_model_error_vs_newer_cv': round(a_err / (b_err or 1.0), 2), 'verdict': 'not reliable - indicator only'}
    else:
        res['regime_change'] = 'needs >= 28 days of history (have %d)' % span
    return res


def iso_ts(ts):
    return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()


def scan(path, st, host=None):
    """Incremental read of one Claude transcript. msgs[id] = [slot, cost, model, in, out, cache_read, cache_write,
    start, end, cache_write_1h, effort, speed, thinking]: start = the previous entry (the request), end = the last
    streamed copy -> response speed; cache_write = 5m + 1h; effort = reasoning effort of the request (low..max, '-' if
    absent), speed = 'standard' or 'fast' (fast mode), thinking = thinking tokens (part of output); + host for
    imported transcripts (local records have 13 fields)."""
    size = os.path.getsize(path)
    rec = st['files'].get(path)
    off = rec[1] if rec and rec[0] <= size else 0
    prev_t = rec[2] if rec and rec[0] <= size and len(rec) > 2 else None
    if off >= size:
        return
    with open(path, 'rb') as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b'\n')
    if end < 0:
        return
    msgs = st['msgs']
    for line in data[:end + 1].split(b'\n'):
        if b'"timestamp"' not in line:
            continue
        has_u = b'"usage"' in line
        if not has_u and b'"type":"user"' not in line:
            continue
        try:
            d = json.loads(line)
            if d.get('type') not in ('user', 'assistant'):
                continue
            t = iso_ts(d['timestamp'])
        except Exception:
            continue
        m = d.get('message') or {}
        u, mid = m.get('usage'), m.get('id')
        if d.get('type') != 'assistant' or not u or not mid or m.get('model') == '<synthetic>':
            prev_t = t
            continue
        c = cost(u, price_for(m.get('model')))
        prev = msgs.get(mid)
        start = prev[7] if prev is not None and len(prev) > 8 else (prev_t or t)
        if prev is None or c >= prev[1]:          # streamed copies of one message: keep the fullest
            cc = u.get('cache_creation') or {}
            w1 = cc.get('ephemeral_1h_input_tokens') or 0
            cw = ((cc.get('ephemeral_5m_input_tokens') or 0) + w1) or (u.get('cache_creation_input_tokens') or 0)
            msgs[mid] = [int(t) // SLOT, c, model_key(m.get('model')), u.get('input_tokens') or 0,
                         u.get('output_tokens') or 0, u.get('cache_read_input_tokens') or 0, cw, start, t, w1,
                         str(d.get('effort') or '-'), str(u.get('speed') or '-'),
                         (u.get('output_tokens_details') or {}).get('thinking_tokens') or 0] + ([host] if host else [])
        elif len(prev) > 8:
            prev[8] = max(prev[8], t)
        prev_t = t
    st['files'][path] = [size, off + end + 1, prev_t]


def last_limits():
    """(wk_x100, reset_epoch) from the newest history line that carries the reset time."""
    res = None
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 4 and p[1].isdigit() and p[3].isdigit():
                res = (int(p[1]), int(p[3]))
    except OSError:
        pass
    return res


def wk_credit_of(rw):
    """Points x100 of the weekly limit handed back by manual resets inside the weekly window that ends at rw: the sum of the
    wk% drops >= RESET_DROP between consecutive history rows with the SAME reset time (a real weekly reset changes it). A reset
    may be partial (one model, e.g. Fable: its use is taken off the weekly limit). Calibration counts wk% + credit as the
    use of the week, so it stays comparable with the cost of the whole week."""
    credit, prev = 0, None
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 4 and p[0].isdigit() and p[1].isdigit() and p[3].isdigit():
                cur = (int(p[1]), int(p[3]))
                if prev and cur[1] == rw and prev[1] == rw and prev[0] - cur[0] >= RESET_DROP:
                    credit += prev[0] - cur[0]
                prev = cur
    except OSError:
        pass
    return credit


def parse_tok_line(line):
    """One line of the token axis -> (bucket, model, effort, speed, msgs, [input, output, cache_read, cache_write_5m,
    cache_write_1h], thinking) or None. Current format = JSONL (see append_tokens); the legacy whitespace format
    ('epoch model effort speed msgs input output cache_read cache_write_5m cache_write_1h thinking', '#' header) is still read."""
    try:
        if line.startswith('{'):
            d = json.loads(line)
            return (int(d['t']), d['model'], d['effort'], d['speed'], int(d['msgs']), [int(d[k]) for k in TOK_FIELDS], int(d.get('thinking', 0)))
        p = line.split()
        if len(p) >= 11 and p[0].isdigit():
            return (int(p[0]), p[1], p[2], p[3], int(p[4]), [int(x) for x in p[5:10]], int(p[10]))
    except (ValueError, KeyError, TypeError):
        pass
    return None


def read_tokens(path=TOKENS):
    """Rows of the token axis (either format), in file order; a missing file = no rows."""
    try:
        with open(path) as f:
            for line in f:
                r = parse_tok_line(line)
                if r:
                    yield r
    except OSError:
        return


def tok_json(r):
    """A row as one JSONL line, readable by hand: ts = the bucket start in UTC, t = the same as epoch (the join key)."""
    d = {'t': r[0], 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r[0])), 'model': r[1], 'effort': r[2], 'speed': r[3],
         'msgs': r[4]}
    d.update(zip(TOK_FIELDS, r[5]))
    d['thinking'] = r[6]
    return json.dumps(d) + '\n'


def append_tokens(msgs, now, path=TOKENS, host=None):
    """TOKEN axis (raw, no prices): append completed 5-min buckets newer than the file's last one, one JSON object per
    line (JSONL), so the file can be read by hand: t/ts = bucket start (epoch / UTC), model, effort, speed, msgs, input,
    output, cache_read, cache_write_5m, cache_write_1h, thinking (part of output). Key = bucket x model x effort x speed.
    Kept forever (~150 lines a day); the LIMIT axis is HISTORY - join them by epoch for any analysis. A file in the legacy
    whitespace format is converted losslessly; the original is rotated, not deleted: '<file>.<epoch>'.
    host=None: this machine's messages; host='x': the messages imported from x (file TOKENS@x)."""
    try:
        with open(path) as f:
            head = f.readline()
        if head and not head.startswith('{'):
            rows = list(read_tokens(path))
            tmp = path + '.new'
            with open(tmp, 'w') as f:
                f.writelines(tok_json(r) for r in rows)
            os.rename(path, '%s.%d' % (path, int(now)))
            os.replace(tmp, path)
    except OSError:
        pass
    last = 0
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            for line in f.read().decode('utf-8', 'replace').split('\n'):
                r = parse_tok_line(line)
                if r:
                    last = r[0]
    except OSError:
        pass
    upto = (int(now) - TOK_BUCKET) // TOK_BUCKET * TOK_BUCKET          # buckets ending >= 5 min ago are complete
    agg = {}
    for v in msgs.values():
        if len(v) < 13 or (v[13] if len(v) > 13 else None) != host:
            continue
        b = int(v[8]) // TOK_BUCKET * TOK_BUCKET
        if not (last < b < upto):
            continue
        a = agg.setdefault((b, v[2], v[10], v[11]), [0] * 7)
        a[0] += 1; a[1] += v[3]; a[2] += v[4]; a[3] += v[5]; a[4] += v[6] - v[9]; a[5] += v[9]; a[6] += v[12]
    if not agg:
        return 0
    with open(path, 'a') as f:
        for k, a in sorted(agg.items()):
            f.write(tok_json((k[0], re.sub(r'\s', '_', k[1]), re.sub(r'\s', '_', k[2]), re.sub(r'\s', '_', k[3]), a[0], a[1:6], a[6])))
    return len(agg)


def outside_use(now, msgs):
    """Per hour (UTC slot of the sample's end) over the profile window: the 5h-limit step between consecutive history
    samples (<= 2 h apart; a drop = a new 5h window, its new value is the step) against the cost of the messages that
    ended in between (same 5h reset = same window). Median 5h%-per-$ over hours with >= OUT_MIN_USD; an hour is flagged as outside use (another
    machine, claude.ai) when its step > OUT_RATIO x the expected one and by >= OUT_MIN_PTS points.
    -> (hours {slot: [d5h, dwk, usd]}, flagged {slot: excess 5h points over the median}, median)."""
    since = now - PROFILE_DAYS * 86400
    rows = []
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 5 and p[0].isdigit() and p[1].isdigit() and p[2].isdigit() and p[4].isdigit() and int(p[0]) >= since:
                rows.append((int(p[0]), int(p[1]), int(p[2]), int(p[4])))   # only rows with the 5h reset (from 2026-11-12): older ones flap between sessions
    except OSError:
        pass
    ends = sorted((v[8], v[1]) for v in msgs.values() if len(v) > 8 and v[8] >= since - 7200)
    ts, cum = [e[0] for e in ends], list(itertools.accumulate(e[1] for e in ends))
    def spent(t1, t2):
        i, j = bisect.bisect_right(ts, t1), bisect.bisect_right(ts, t2)
        return (cum[j - 1] if j else 0.0) - (cum[i - 1] if i else 0.0)
    H = {}
    for a, b in zip(rows, rows[1:]):
        if not 0 < b[0] - a[0] <= 7200:
            continue
        h = H.setdefault(b[0] // SLOT, [0, 0, 0.0])
        h[0] += ((b[2] - a[2]) if b[3] == a[3] else b[2]) / 100.0      # points of the 5h limit (new window: from 0)
        h[1] += max(0, b[1] - a[1]) / 100.0                                    # points of the weekly limit
        h[2] += spent(a[0], b[0])
    rs = [h[0] / h[2] for h in H.values() if h[2] >= OUT_MIN_USD]
    med = median(rs)
    flagged = {}
    if med and len(rs) >= OUT_MIN_HOURS:
        for sl, (d5, dwk, usd) in H.items():
            if d5 > OUT_RATIO * med * usd + OUT_MIN_PTS:
                flagged[sl] = round(d5 - med * usd, 1)
    return H, flagged, med


def active_rate(items, cov0, cur):
    """My usual intensity WHILE WORKING: units per active hour over slots [cov0, cur).
    items = (t, units). An hour is active from its first response to its end (the status line's 'act' rule),
    so idle hours do not dilute the average. -> (units per active hour, active hours)."""
    first, tot = {}, 0.0
    for t, u in items:
        s = int(t) // SLOT
        if cov0 <= s < cur:
            tot += u
            if s not in first or t < first[s]:
                first[s] = t
    hrs = sum(((s + 1) * SLOT - max(t, s * SLOT)) / SLOT for s, t in first.items())
    return (tot / hrs if hrs >= 1.0 else None), hrs


# ============================================================================ connections (all harnesses)
def host_of(url):
    m = re.match(r'\w+://([^/:]+)', url or '')
    return m.group(1).lower() if m else None


def conn_key(host):
    return re.sub(r'[^a-z0-9.-]', '_', (host or 'unknown').lower())


def read_json(p, comments=False):
    try:
        txt = open(p).read()
    except OSError:
        return {}
    if comments:                                    # jsonc: whole-line // comments and trailing commas
        txt = re.sub(r'^\s*//.*$', '', txt, flags=re.M)
        txt = re.sub(r',(\s*[}\]])', r'\1', txt)
    try:
        return json.loads(txt)
    except ValueError:
        return {}


def yaml_provider_hosts(p):
    """mcode config.yaml: provider name -> host. Only the section/name lines and baseURL values are looked at."""
    out, sect, name = {}, None, None
    try:
        lines = open(p).read().splitlines()
    except OSError:
        return out
    for l in lines:
        m = re.match(r'^(\S[^:]*):', l)
        if m:
            sect, name = m.group(1), None
            continue
        m = re.match(r'^  (\S[^:]*):\s*$', l)
        if m and sect in ('provider', 'custom_provider'):
            name = m.group(1)
            continue
        m = re.match(r'^\s+base_?url:\s*["\']?([^"\'\s]+)', l, re.I)
        if m and name:
            out[name] = host_of(m.group(1))
    return out


KNOWN_HOSTS = {'anthropic': ANTHROPIC, 'opencode': 'opencode.ai', 'openai': 'api.openai.com', 'minimax': 'api.minimax.io'}


def sqlite_rows(path, sql, args=()):
    import sqlite3
    if not os.path.exists(path):
        return []
    try:
        c = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=1)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()
    except Exception:
        return []


# A record = one model response: (t_end, harness, conn, model, tokens, out_tokens, duration_s|None, cost_usd|None)
def recs_qwen(since):
    out = []
    st = read_json(os.path.join(HOME, '.qwen/settings.json'))
    auth = ((st.get('security') or {}).get('auth') or {}).get('selectedType')
    key = conn_key(host_of((st.get('model') or {}).get('baseUrl')) or ('qwen-oauth' if auth == 'qwen-oauth' else 'qwen'))
    m0 = time.strftime('%Y-%m', time.gmtime(since - 86400))
    for f in sorted(glob.glob(os.path.join(HOME, '.qwen/usage/token-usage-*.jsonl'))):
        if f[-13:-6] < m0:
            continue
        try:
            fh = open(f)
        except OSError:
            continue
        for line in fh:
            try:
                d = json.loads(line)
                t = iso_ts(d['timestamp'])
            except Exception:
                continue
            if t < since:
                continue
            o = d.get('outputTokens') or 0
            ms = d.get('apiDurationMs')
            out.append((t, 'qwen', key, d.get('model') or '?', (d.get('inputTokens') or 0) + o, o,
                        ms / 1000.0 if ms else None, None))
    return out


def recs_mcode(since):
    hosts = yaml_provider_hosts(os.path.join(HOME, '.minimax/config.yaml'))
    out = []
    for ts, model, i_, o_, cr, c in sqlite_rows(os.path.join(HOME, '.minimax/v2/sqlite/runtime-state.sqlite'),
            'select ts, model, input_tokens, output_tokens, cache_read_tokens, cost_usd from local_runtime_token_usage where ts >= ?',
            (int(since * 1000),)):
        prov, _, name = (model or '?').split(':', 1)[-1].partition('/')
        host = hosts.get(prov) or KNOWN_HOSTS.get(prov) or prov
        out.append((ts / 1000.0, 'mcode', conn_key(host), name or prov, (i_ or 0) + (o_ or 0) + (cr or 0), o_ or 0, None, c))
    return out


def recs_opencode(since):
    cfg = read_json(os.path.join(HOME, '.config/opencode/opencode.jsonc'), True) or read_json(os.path.join(HOME, '.config/opencode/opencode.json'), True)
    prov = cfg.get('provider') or {}
    out = []
    for (data,) in sqlite_rows(os.path.join(HOME, '.local/share/opencode/opencode.db'),
                               'select data from message where time_created >= ?', (int(since * 1000),)):
        try:
            d = json.loads(data)
        except ValueError:
            continue
        if d.get('role') != 'assistant':
            continue
        tk, tm = d.get('tokens') or {}, d.get('time') or {}
        o = tk.get('output') or 0
        tot = (tk.get('input') or 0) + o + ((tk.get('cache') or {}).get('read') or 0)
        if not tot or not tm.get('created'):
            continue
        p = d.get('providerID') or '?'
        host = host_of(((prov.get(p) or {}).get('options') or {}).get('baseURL')) or KNOWN_HOSTS.get(p) or p
        t1 = (tm.get('completed') or tm['created']) / 1000.0
        dur = t1 - tm['created'] / 1000.0 if tm.get('completed') else None
        out.append((t1, 'opencode', conn_key(host), d.get('modelID') or '?', tot, o, dur, d.get('cost')))
    return out


def recs_pi(since):
    prov = read_json(os.path.join(HOME, '.pi/agent/models.json')).get('providers') or {}
    out = []
    for f in glob.glob(os.path.join(HOME, '.pi/agent/sessions/**/*.jsonl'), recursive=True):
        try:
            if os.path.getmtime(f) < since:
                continue
            fh = open(f)
        except OSError:
            continue
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
                m = d.get('message') or {}
                u = m.get('usage')
                if m.get('role') != 'assistant' or not u:
                    continue
                t = iso_ts(d['timestamp'])
            except Exception:
                continue
            if t < since:
                continue
            p = m.get('provider') or '?'
            host = host_of((prov.get(p) or {}).get('baseUrl')) or KNOWN_HOSTS.get(p) or p
            dur = t - m['timestamp'] / 1000.0 if isinstance(m.get('timestamp'), (int, float)) else None
            o = u.get('output') or 0
            out.append((t, 'pi', conn_key(host), m.get('model') or '?', (u.get('input') or 0) + o + (u.get('cacheRead') or 0), o,
                        dur if dur and 0 < dur < 1800 else None, (u.get('cost') or {}).get('total')))
    return out


def recs_claude(msgs):
    out = []
    for slot, c, key, i_, o_, cr_, cw_, *se in msgs.values():
        if len(se) < 2:
            continue
        out.append((se[1], 'claude', ANTHROPIC, key, i_ + o_ + cr_ + cw_, o_, se[1] - se[0], c))
    return out


def all_recs(since, claude_msgs):
    R = recs_claude(claude_msgs)
    for fn in (recs_qwen, recs_mcode, recs_opencode, recs_pi):
        try:
            R += fn(since)
        except Exception:
            pass
    return [r for r in R if r[0] >= since]


def billing_of(key, R, cfg):
    o = (cfg.get(key) or {}).get('billing')
    if o in ('free', 'metered', 'limited'):
        return o
    if key == ANTHROPIC:
        lim = last_limits()
        if lim and lim[1] > time.time() - 8 * 86400:
            return 'limited'
    return 'metered' if any((r[7] or 0) > 0 for r in R) else 'free'


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0) if n else None


def speed_fit(R, now):
    """Per-model median tokens/s and the pooled speed index per hour of the week (1 = the model's own median)."""
    sp = [(r[0], r[3], r[5] / r[6]) for r in R
          if r[6] and r[5] >= MIN_OUT and r[6] >= MIN_DUR and r[0] >= now - SPEED_DAYS * 86400]
    by = collections.defaultdict(list)
    for _, m, v in sp:
        by[m].append(v)
    base = {m: median(v) for m, v in by.items() if len(v) >= 5}
    s_sum, s_n = [0.0] * SLOTS, [0] * SLOTS
    for t, m, v in sp:
        if m in base:
            i = int(t) // SLOT % SLOTS
            s_sum[i] += min(max(v / base[m], 0.2), 5.0)
            s_n[i] += 1
    idx = [(s_sum[i] + SPEED_K) / (s_n[i] + SPEED_K) for i in range(SLOTS)]
    n = sum(s_n)
    days = len({int(t) // 86400 for t, m, _ in sp if m in base})
    status = 'fit' if n >= 300 and days >= 14 else ('prelim' if n >= 30 else '-')
    return base, idx, n, days, status


def write_conn_profiles(now, msgs):
    """Daily: one profile per connection (load matrix, speed matrix, load modes, model speeds)."""
    os.makedirs(HSL_STATE, exist_ok=True)
    cfg = read_json(HSL_CFG) or read_json(HSL_CFG_OLD)
    R = all_recs(now - SPEED_DAYS * 86400, msgs)
    by = collections.defaultdict(list)
    for r in R:
        by[r[2]].append(r)
    cur = int(now) // SLOT
    summary = {}
    for key, rs in by.items():
        bill = billing_of(key, rs, cfg)
        unit = 'usd' if bill == 'metered' else 'tok'
        cov0 = max(int(min(r[0] for r in rs)) // SLOT, cur - PROFILE_DAYS * 24)
        occ, tot = [0] * SLOTS, [0.0] * SLOTS
        for s_ in range(cov0, cur):
            occ[s_ % SLOTS] += 1
        for r in rs:
            sl = int(r[0]) // SLOT
            if cov0 <= sl < cur:
                tot[sl % SLOTS] += (r[7] or 0.0) * 10000 if unit == 'usd' else r[4]
        L = [int(round(tot[i] / occ[i])) if occ[i] else 0 for i in range(SLOTS)]
        ar, ahrs = active_rate([(r[0], (r[7] or 0.0) * 10000 if unit == 'usd' else r[4]) for r in rs], cov0, cur)
        base, idx, n, days, status = speed_fit(rs, now)
        if status == '-':
            idx = [1.0] * SLOTS
        modes = ''.join('2' if x < BUSY else ('0' if x > LIGHT else '1') for x in idx)
        base_s = ' '.join('%s=%d' % (re.sub(r'\s', '_', m), round(v)) for m, v in sorted(base.items())) or '-'
        os.makedirs(os.path.join(HSL_STATE, key), exist_ok=True)
        p = os.path.join(HSL_STATE, key, 'conn')
        with open(p + '.tmp', 'w') as f:
            f.write('%d %s %.1f %s %d %s\n%s\n%s\n%s\n%s\n%s\n' % (
                now, bill, (cur - cov0) / 24.0, unit, n, '%d' % round(ar) if ar else '-', ' '.join(map(str, L)),
                ' '.join(str(int(round(x * 100))) for x in idx), modes, base_s, status))
        os.replace(p + '.tmp', p)
        summary[key] = {'billing': bill, 'unit': unit, 'records': len(rs), 'harnesses': sorted({r[1] for r in rs}),
                        'models': sorted({r[3] for r in rs}), 'coverage_days': round((cur - cov0) / 24.0, 1),
                        'avg_weekly_units': sum(L) / (10000.0 if unit == 'usd' else 1),
                        'units_per_active_hour': round(ar / (10000.0 if unit == 'usd' else 1), 2) if ar else None,
                        'active_hours': round(ahrs, 1),
                        'speed': {'status': status, 'samples': n, 'days': days,
                                  'median_tps_by_model': {m: round(v, 1) for m, v in base.items()},
                                  'index_min_max': [round(min(idx), 2), round(max(idx), 2)],
                                  'hours_busy_light': [modes.count('2'), modes.count('0')]}}
    json.dump({'updated': int(now), 'connections': summary}, open(os.path.join(HSL_STATE, 'connections.json'), 'w'), indent=1)
    return summary


# ---------------------------------------------------------------------------- collector (every ~5 min)
def rt_dir():
    d = '/tmp/hsl-%d' % os.getuid()
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    except OSError:
        d = None
    if not d or os.path.islink(d) or os.stat(d).st_uid != os.getuid():
        d = os.path.join(HSL_STATE, 'rt')
        os.makedirs(d, exist_ok=True)
    return d


def read_conn_profile(key):
    try:
        lines = open(os.path.join(HSL_STATE, key, 'conn')).read().split('\n')
        h = lines[0].split()
        L = [int(x) for x in lines[1].split()]
        base = dict(kv.split('=', 1) for kv in lines[4].split() if '=' in kv)
        ar = float(h[5]) if len(h) > 5 and h[5] != '-' else None
        return {'billing': h[1], 'unit': h[3], 'L': L if len(L) == SLOTS else None, 'ar': ar if ar and ar > 0 else None,
                'base': {k: float(v) for k, v in base.items()}}
    except Exception:
        return None


def atomic_write(p, text):
    with open(p + '.%d' % os.getpid(), 'w') as f:
        f.write(text)
    os.replace(p + '.%d' % os.getpid(), p)


def sl_cache_dir():
    """The status line's cache dir (tmpfs, else its own fallback) or None."""
    d = '/tmp/claude-sl-%d' % os.getuid()
    try:
        if os.path.islink(d) or not os.path.isdir(d) or os.stat(d).st_uid != os.getuid():
            d = os.path.join(HSL_STATE, 'cache')
            if not os.path.isdir(d):
                return None
        return d
    except OSError:
        return None


def sync_hist_last(now, wk, fv, r5, rw):
    """Tell the CLI status lines about the row just appended: their writers compare against $CACHE_DIR/hist-last
    ("epoch 5h% wk% rwe wk_x100 5h_x100 r5e") so that a stale lower value is not appended after this one."""
    d = sl_cache_dir()
    try:
        if not d:
            return
        p = os.path.join(d, 'hist-last')
        try:
            f = open(p).read().split()
        except OSError:
            f = []
        h5 = f[5] if len(f) > 6 and f[5].isdigit() and f[6].isdigit() else '-'      # an unknown 5h keeps the last known one
        h5r = f[6] if h5 != '-' else '-'
        hf = f[1] if len(f) > 1 else '-'
        if fv is not None and r5:
            hf, h5, h5r = str((fv + 50) // 100), str(fv), str(r5)
        atomic_write(p, '%d %s %d %d %d %s %s\n' % (now, hf, (wk + 50) // 100, rw, wk, h5, h5r))
    except OSError:
        pass


POLL_STOP_FILE = 'usage-poll-stopped.lock'                      # in ANTH_DIR; while it exists nothing polls; its text is the reason


def poll_read(sp):
    """usage-poll state: "next_try_epoch fails last_status strikes"; stopped = the stop file exists."""
    try:
        f = open(sp).read().split()
    except OSError:
        f = []
    num = lambda i: int(f[i]) if len(f) > i and f[i].lstrip('-').isdigit() else 0
    return num(0), num(1), (f[2] if len(f) > 2 else '-'), num(3), os.path.exists(os.path.join(os.path.dirname(sp), POLL_STOP_FILE))


def poll_usage(now):
    """One poll of the usage endpoint -> a HISTORY row (on a whole-% change, a new window or the 30-min heartbeat).
    Stopped completely while the stop file `usage-poll-stopped` exists (it holds the reason). Never asks twice: it is skipped while the status lines (= the headers of the sessions' own API responses) hold fresh
    limits, while another process is polling or polled < POLL_MIN ago (cron, status lines of any number of sessions share
    one lock + one timer). A refusal (401/403/429) is retried with a growing wait (8/16/32 min), then once an hour, and never stops it; the stop file is written after POLL_STOP_AFTER consecutive other HTTP errors /
    unusable answers (no request from cron or any status line after that, until `--resume` or the file is deleted); network
    errors and an expired token only back off (nothing reached the server). Only the sessions' own responses are recorded then."""
    os.makedirs(ANTH_DIR, exist_ok=True)
    sp = os.path.join(ANTH_DIR, 'usage-poll')
    nt, fails, st0, strikes, stopped = poll_read(sp)
    if stopped or now < nt:
        return
    d_ = sl_cache_dir()
    try:                                                         # a session's own response limits are fresh -> nothing to ask
        if d_ and now - int(open(os.path.join(d_, 'lim')).read().split()[0]) < POLL_MIN:
            return
    except (OSError, ValueError, IndexError):
        pass
    lk = open(os.path.join(ANTH_DIR, 'usage-poll.mutex'), 'w')
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                                   # another process is asking right now
    nt, fails, st0, strikes, stopped = poll_read(sp)             # re-read under the lock: someone may have just polled
    if stopped or now < nt:
        return
    atomic_write(sp, '%d %d %s %d\n' % (now + POLL_MIN, fails, st0, strikes))        # claim the slot before asking
    def done(kind, status, detail=''):
        f_, s_, stop = fails, strikes, False
        if kind == 'ok':
            f_, s_, wait = 0, 0, POLL_MIN
        elif kind == 'soft':                                     # no / expired token: not asked, not an error
            wait = POLL_MIN
        else:
            f_ += 1
            wait = min(POLL_MIN * 2 ** min(f_, 10), POLL_MAX_BACKOFF)
            if kind == 'http' and status in POLL_REFUSED:        # 401/403/429: may pass by itself (token rotation, rate limit): 3 tries with a growing wait (8/16/32 min), then once an hour, never stopped for good
                pass
            elif kind in ('http', 'bad'):
                s_ += 1
                stop = s_ >= POLL_STOP_AFTER                     # any other HTTP error / unusable answer: three in a row
        atomic_write(sp, '%d %d %s %d\n' % (now + wait, min(f_, 10), status, s_))
        if stop:
            atomic_write(os.path.join(ANTH_DIR, POLL_STOP_FILE),
                         'stopped: %s UTC\nreason: %s%s (GET %s)\nrule: any HTTP error other than 401/403/429 (those are retried hourly) or unusable answer, %d in a row (this was #%d)\n'
                         'effect: no usage requests from cron or any status line; only the sessions\' own response limits are recorded\n'
                         'resume: python3 -I %s --resume   (or delete this file)\n' % (
                             datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                             status, (' ' + detail) if detail else '', USAGE_URL, POLL_STOP_AFTER, s_, os.path.abspath(__file__)))
    try:
        o = json.load(open(CREDENTIALS)).get('claudeAiOauth') or {}
        tok = o.get('accessToken')
        if not tok or (o.get('expiresAt') or 0) / 1000.0 < now + 60:
            return done('soft', 'no-token')                      # Claude Code renews it on its next use
        req = urllib.request.Request(USAGE_URL, headers={'Authorization': 'Bearer ' + tok, 'anthropic-beta': 'oauth-2025-04-20',
                                                         'User-Agent': 'harness-status-line'})
        d = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
    except urllib.error.HTTPError as e:
        try:                                                     # the server's own explanation (error JSON, www-authenticate), never our request; long tokens-like strings masked
            body = e.read(400).decode('utf-8', 'replace')
        except Exception:
            body = ''
        hdr = ' '.join('%s=%s' % (k, e.headers.get(k)) for k in ('server', 'www-authenticate', 'retry-after') if e.headers.get(k))
        info = re.sub(r'[A-Za-z0-9_.\-]{30,}', '...', ' '.join(('%s %s [%s]' % (e.reason, body, hdr)).split()))
        return done('http', e.code, info[:300])
    except Exception:
        return done('net', 'error')
    tok = req = None

    def part(k):
        v = d.get(k) or {}
        try:
            r = datetime.datetime.fromisoformat(v['resets_at']).timestamp()
            return int(round(float(v['utilization']) * 100)), int(round(r / 60.0)) * 60   # the CLI rows are whole minutes
        except Exception:
            return None, None
    wk, rw = part('seven_day')
    fv, r5 = part('five_hour')
    if wk is None or not rw or rw <= now:
        return done('bad', 'bad-data', 'unusable answer: no usable seven_day utilization / reset')
    if fv is None or not r5 or r5 <= now:
        fv, r5 = None, None                                      # no active 5h window
    try:                                                         # fresh server-side limits for a status line whose own are stale
        rt = rt_dir()
        if rt:
            atomic_write(os.path.join(rt, 'lim-poll'), '%d %s %s %d %d\n' % (now, fv if fv is not None else '-', r5 or '-', wk, rw))
    except OSError:
        pass
    last = None
    try:
        with open(HISTORY, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            ls = f.read().decode('utf-8', 'replace').strip().split('\n')
        last = ls[-1].split() if ls else None
    except OSError:
        pass
    write = True
    if last and len(last) >= 4 and last[0].isdigit() and last[3].isdigit():
        lt, lwk, lrw = int(last[0]), int(last[1]), int(last[3])
        l5 = int(last[2]) if last[2].isdigit() else None
        l5r = int(last[4]) if len(last) > 4 and last[4].isdigit() else None
        if rw < lrw:
            write = False                                        # an older weekly window than the last row: not ours to add
        elif now - lt < POLL_HEARTBEAT and rw == lrw and wk // 100 == lwk // 100 and (fv is None or (l5r == r5 and l5 is not None and fv // 100 == l5 // 100)):
            write = False                                        # no whole-% change inside the same windows
    if write:
        line = '%d %d %s %d %s\n' % (now, wk, fv if fv is not None else '-', rw, r5 if r5 else '-')
        fd = os.open(HISTORY, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        sync_hist_last(now, wk, fv, r5, rw)
    done('ok', 'ok')


def collect_main():
    now = time.time()
    os.makedirs(HSL_STATE, exist_ok=True)
    lock = open(os.path.join(HSL_STATE, 'collect.mutex'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    rt = rt_dir()
    atomic_write(os.path.join(rt, 'collect-last'), '%d\n' % now)
    since = now - 26 * 3600
    sp = os.path.join(HSL_STATE, 'collect-state.json')
    st = read_json(sp)
    if st.get('v') != STATE_VERSION:
        st = {}
    st['v'] = STATE_VERSION
    st.setdefault('files', {}); st.setdefault('msgs', {})
    for f in glob.glob(PROJECTS + '/**/*.jsonl', recursive=True):     # Claude: every session, also the VS Code extension
        try:
            if os.path.getmtime(f) < since:
                continue
            if f not in st['files']:                                    # first sight: only the tail can be this recent
                sz = os.path.getsize(f)
                st['files'][f] = [sz, max(0, sz - 4 * 1048576), None]
            scan(f, st)
        except OSError:
            continue
    cut = int(since) // SLOT
    st['msgs'] = {k: v for k, v in st['msgs'].items() if v[0] >= cut}
    st['files'] = {k: v for k, v in st['files'].items() if os.path.exists(k) and os.path.getmtime(k) >= since - 86400}
    atomic_write(sp, json.dumps(st))
    R = all_recs(since, st['msgs'])
    cmap, by = {}, collections.defaultdict(list)
    for r in sorted(R):
        by[r[2]].append(r)
        cmap[(r[1], r[3])] = r[2]
        cmap[(r[1], '-')] = r[2]                                        # the harness's most recent connection
    atomic_write(os.path.join(rt, 'conn-map'), ''.join('%s %s %s\n' % (h, re.sub(r'\s', '_', m), k) for (h, m), k in sorted(cmap.items())))
    h_now = int(now) // 3600
    lt = time.localtime(now)
    day0 = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
    for key, rs in by.items():
        first = {}
        for r in rs:
            hh = int(r[0]) // 3600
            if hh not in first or r[0] < first[hh]:
                first[hh] = r[0]
        atomic_write(os.path.join(rt, 'act-' + key), ''.join('%d\n' % first[h] for h in sorted(first)))
        prof = read_conn_profile(key) or {'billing': 'free', 'unit': 'tok', 'L': None, 'ar': None, 'base': {}}
        usd = prof['unit'] == 'usd'
        units = lambda r: (r[7] or 0.0) * 10000 if usd else r[4]
        rel = '-'
        ar = prof.get('ar')                                             # my usual use per ACTIVE hour (idle hours excluded)
        if ar:
            got = exp = secs = 0.0
            for i in range(3):                                          # current hour and the two before it, active only
                hh = h_now - i
                if hh not in first:
                    continue
                st_ = max(first[hh], hh * 3600)
                e = now if i == 0 else (hh + 1) * 3600
                if e <= st_:
                    continue
                secs += e - st_
                exp += ar * (e - st_) / 3600.0
                got += sum(units(r) for r in rs if st_ <= r[0] <= e)
            K = 0.1 * ar                                                # pseudo-observation of 6 usual minutes (damps a short window)
            if secs >= 120:
                rel = '%d' % round((got + K) * 100 / (exp + K))
        rr = [min(max(r[5] / r[6] / prof['base'][r[3]], 0.2), 5.0) for r in rs
              if r[0] >= now - 900 and r[6] and r[5] >= MIN_OUT and r[6] >= MIN_DUR and prof['base'].get(r[3])]
        spd = '%d' % round(sum(rr) * 100 / len(rr)) if rr else '-'
        today = sum(units(r) for r in rs if r[0] >= day0)
        today_s = ('%.2f' % (today / 10000.0)) if usd else '%d' % today
        last = max(r[0] for r in rs)
        atomic_write(os.path.join(rt, 'rt-' + key), '%d %s %s %d %s %s %s %d\n' % (
            now, rel, spd, len(rr), today_s, prof['unit'], prof['billing'], last))



def burn_fit():
    """--burn, part 2: a structured token model of the limit, with NO price list. Raw tokens (Mtok) per model family x type
    (output / cache read / cache write 5m+input / cache write 1h) enter as   limit = sum_g a_g * sum_m sum_k r_k * X[m,k]
    a_g = scale of a model group (limit points per weighted Mtok), r_k = type ratios relative to cache read (shared by the
    models, stable), searched on a grid and scored by leave-one-day-out error. The limit LEVEL at every history sample is
    regressed on the cumulative tokens since the window start (free intercept per weekly / 5h window, weights >= 0): levels
    are absolute, so no selection on the size of the rise, no gap limit. Then the burn per weighted token unit U is followed
    in time (mode, UTC block, day; jackknife +-2SE by day; each 5h window). Outside use (another machine) is not removed."""
    FAM = ('opus', 'sonnet', 'haiku'); TYP = ('out', 'read', 'write5m', 'write1h')
    T = []                                        # (t, 12 values: per family [out, read, write 5m + input, write 1h] in Mtok)
    for r in read_tokens():
        f = 0 if 'opus' in r[1] else 2 if 'haiku' in r[1] else 1
        v = [x / 1e6 for x in r[5]]
        x = [0.0] * 12
        x[f * 4], x[f * 4 + 1], x[f * 4 + 2], x[f * 4 + 3] = v[1], v[2], v[0] + v[3], v[4]
        T.append((r[0], x))
    T.sort(key=lambda r: r[0])
    S = []
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 4 and p[0].isdigit() and p[1].isdigit() and p[3].isdigit():
                S.append((int(p[0]), int(p[1]) / 100.0, int(p[2]) / 100.0 if p[2].isdigit() else None, int(p[3]),
                          int(p[4]) if len(p) > 4 and p[4].isdigit() else 0))
    except OSError:
        return
    if not T or not S:
        return
    times = [r[0] for r in T]

    def mk_cum(val, ng):                          # val(i) -> ng contributions of bucket i; cum(t) -> cumulative per group
        P = [[0.0] * (len(T) + 1) for _ in range(ng)]
        for i in range(len(T)):
            v = val(i)
            for g in range(ng):
                P[g][i + 1] = P[g][i] + v[g]

        def cum(t):
            i = bisect.bisect_right(times, t - TOK_BUCKET)
            out = [P[g][i] for g in range(ng)]
            if i < len(T) and times[i] < t:
                fr = (t - times[i]) / TOK_BUCKET
                out = [o + (P[g][i + 1] - P[g][i]) * fr for g, o in enumerate(out)]
            return out
        return cum

    def solve(X, y, w, iters=300):                # per-window centering, nonneg coordinate descent; weights, intercepts
        m = len(X[0])
        grp = collections.defaultdict(list)
        for i, k in enumerate(w):
            grp[k].append(i)
        Xc = [r[:] for r in X]; yc = y[:]
        for ids in grp.values():
            ym = sum(y[i] for i in ids) / len(ids)
            xm = [sum(X[i][j] for i in ids) / len(ids) for j in range(m)]
            for i in ids:
                yc[i] = y[i] - ym
                Xc[i] = [X[i][j] - xm[j] for j in range(m)]
        G = [[sum(r[a] * r[b] for r in Xc) for b in range(m)] for a in range(m)]
        c = [sum(r[a] * yv for r, yv in zip(Xc, yc)) for a in range(m)]
        b = [0.0] * m
        for _ in range(iters):
            for a in range(m):
                if G[a][a] > 1e-12:
                    b[a] = max(0.0, (c[a] - sum(G[a][k] * b[k] for k in range(m) if k != a)) / G[a][a])
        icp = {k: sum(y[i] - sum(bj * xj for bj, xj in zip(b, X[i])) for i in ids) / len(ids) for k, ids in grp.items()}
        return b, icp

    def err(X, y, w, b, icp, ids):
        e = [(y[i] - sum(bj * xj for bj, xj in zip(b, X[i])) - icp.get(w[i], 0.0)) ** 2 for i in ids]
        return (sum(e) / len(e)) ** .5

    def loo_rmse(X, y, w, d, days):
        e = []
        for dd in days:
            tr = [i for i in range(len(y)) if d[i] != dd]; te = [i for i in range(len(y)) if d[i] == dd]
            bb, ii = solve([X[i] for i in tr], [y[i] for i in tr], [w[i] for i in tr])
            e += [(y[i] - sum(bj * xj for bj, xj in zip(bb, X[i])) - ii.get(w[i], 0.0)) ** 2 for i in te]
        return (sum(e) / len(e)) ** .5

    rows = [s for s in S if s[1] is not None]
    w = [s[3] for s in rows]; y = [s[1] for s in rows]; d = [s[0] // 86400 for s in rows]
    days = sorted(set(d)); allids = range(len(rows))
    cumall = mk_cum(lambda i: T[i][1], 12)
    C = [cumall(s[0]) for s in rows]                       # cumulative Mtok, 12 per sample
    tot = [C[-1][j] - C[0][j] for j in range(12)]
    print('\n== token model: weekly limit level vs cumulative raw tokens (limit points per Mtok; no prices)')
    print(' tokens in the span, Mtok:  %s' % '   '.join('%s: %s' % (FAM[m], ' '.join('%s %.1f' % (TYP[k], tot[m * 4 + k]) for k in range(4))) for m in range(3)))
    print(' %d samples, %d window(s), %d days (rmse in limit points; LOO = leave one day out)' % (len(rows), len(set(w)), len(days)))
    ref = []
    X12 = C
    b12, i12 = solve(X12, y, w, 800)
    ref.append(('free 12 weights (model x type)', err(X12, y, w, b12, i12, allids), loo_rmse(X12, y, w, d, days) if len(days) >= 3 else None))
    Xa = [[sum(c)] for c in C]
    ba, ia = solve(Xa, y, w)
    ref.append(('every token alike', err(Xa, y, w, ba, ia, allids), loo_rmse(Xa, y, w, d, days) if len(days) >= 3 else None))
    GROUPS = [('one scale for all models', [[0, 1, 2]]), ('opus+sonnet | haiku', [[0, 1], [2]]), ('opus | sonnet | haiku', [[0], [1], [2]])]
    G_OUT, G_W5, G_W1 = (0, 10, 100), (0, 10, 30), (30, 100, 300, 1000)
    res = []
    for gname, groups in GROUPS:
        for ro, r5, r1 in itertools.product(G_OUT, G_W5, G_W1):
            r = (ro, 1.0, r5, r1)
            X = [[sum(c[m * 4 + k] * r[k] for m in g for k in range(4)) for g in groups] for c in C]
            b, icp = solve(X, y, w)
            lo = loo_rmse(X, y, w, d, days) if len(days) >= 3 else err(X, y, w, b, icp, allids)
            res.append((lo, err(X, y, w, b, icp, allids), gname, groups, r, b))
    res.sort(key=lambda r: r[0])
    for nm, e, lo in ref:
        print('   reference: %-32s rmse %.2f  LOO %s' % (nm, e, '%.2f' % lo if lo is not None else '-'))
    print(' structured model, best 5 (type ratios to cache read = 1):')
    for lo, e, gname, groups, r, b in res[:5]:
        print('   %-26s out %-4g write5m %-3g write1h %-4g | rmse %.2f LOO %.2f | scales %s' % (
            gname, r[0], r[2], r[3], e, lo, '  '.join('%s %.4f' % ('+'.join(FAM[m] for m in g), bv) for g, bv in zip(groups, b))))
    print(' best per model grouping (are the model scales different?):')
    for gname, _ in GROUPS:
        r0 = next(r for r in res if r[2] == gname)
        print('   %-26s rmse %.2f LOO %.2f | scales %s' % (gname, r0[1], r0[0], '  '.join(
            '%s %.4f' % ('+'.join(FAM[m] for m in g), bv) for g, bv in zip(r0[3], r0[5]))))
    print(' what the data can pin down (best LOO when one ratio is fixed; a flat row = not identified):')
    for nm, ix, grid in (('output', 0, G_OUT), ('cache write 5m', 2, G_W5), ('cache write 1h', 3, G_W1)):
        print('   %-15s %s' % (nm, '  '.join('%g: %.2f' % (v, min(r[0] for r in res if r[4][ix] == v)) for v in grid)))
    lo, e, gname, groups, r, b = res[0]
    wt = [0.0] * 12
    for g, bv in zip(groups, b):
        for m in g:
            for k in range(4):
                wt[m * 4 + k] = bv * r[k]
    print(' best model weights, limit points per Mtok:')
    for m in range(3):
        print('   %-7s %s' % (FAM[m], '   '.join('%s %.4f' % (TYP[k], wt[m * 4 + k]) for k in range(4))))
    print(' (output/read/write cumulative curves correlate at ~0.999: the data pin down a weighted sum much better than the single')
    print('  ratios, so read a ratio at the grid edge or a flat row as "not measured", and refit after the mix varies more)')
    unit = lambda i: sum(wt[k] * T[i][1][k] for k in range(12))        # limit points of the weekly limit per bucket
    print('\n== limit burn per token unit over time (unit U = tokens weighted by the best model above; 1.00 = average)')
    DOW = 'Mon Tue Wed Thu Fri Sat Sun'.split()
    keys_all = {'mode': lambda t: mode_of(t), 'UTC 4h block': lambda t: (t // 3600 % 24) // 4, 'day': lambda t: t // 86400}
    lab = {'mode': lambda k: ('eco', 'normal', 'peak')[k], 'UTC 4h block': lambda k: '%02d-%02d' % (k * 4, k * 4 + 4),
           'day': lambda k: '%s %s' % (datetime.date.fromtimestamp(k * 86400).isoformat(), DOW[(k + 3) % 7])}
    cum1 = mk_cum(lambda i: [unit(i)], 1)
    X1 = [cum1(s[0]) for s in rows]
    b1, i1 = solve(X1, y, w)
    for gname, kf in keys_all.items():
        used = sorted({kf(r[0]) for r in T if unit(T.index(r)) > 0}) if False else sorted({kf(t) for t, x in T if t >= rows[0][0] - 86400 * 8})
        ix = {k: j for j, k in enumerate(used)}
        cumg = mk_cum(lambda i: [unit(i) if kf(T[i][0]) == k else 0.0 for k in used], len(used))
        X = [cumg(s[0]) for s in rows]
        tot = [X[-1][j] - X[0][j] for j in range(len(used))]
        keep = [j for j in range(len(used)) if tot[j] > 1e-9]
        if len(keep) < 2:
            continue
        Xk = [[r[j] for j in keep] for r in X]
        bg, _ = solve(Xk, y, w)
        se = None
        if len(days) >= 3 and gname != 'day':
            jk = []
            for dd in days:
                ids = [i for i in range(len(rows)) if d[i] != dd]
                jk.append(solve([Xk[i] for i in ids], [y[i] for i in ids], [w[i] for i in ids])[0])
            mu = [sum(j[a] for j in jk) / len(jk) for a in range(len(keep))]
            se = [((len(jk) - 1) / len(jk) * sum((j[a] - mu[a]) ** 2 for j in jk)) ** .5 for a in range(len(keep))]
        print(' %s (overall x1.00 = %.3f pts per U)' % (gname, b1[0]))
        for a, j in enumerate(keep):
            print('   %-14s U %7.1f  x%.2f%s' % (lab[gname](used[j]), tot[j], bg[a] / b1[0] if b1[0] else 0,
                  '' if se is None else '  +-%.2f' % (2 * se[a] / b1[0] if b1[0] else 0)))
    print('\n 5h windows (each separately): burn of the 5h limit per weekly-weighted token unit U, x1.00 = median window')
    byw = collections.defaultdict(list)
    for s in S:
        if s[4] and s[2] is not None:
            byw[s[4]].append(s)
    out = []
    for r5, v in sorted(byw.items()):
        lv = [s[2] for s in v]
        if len(v) < 6 or max(lv) - min(lv) < 5:
            continue
        dx = cum1(v[-1][0])[0] - cum1(v[0][0])[0]
        if dx > 0:
            out.append((v[0][0], lv[-1] - lv[0], dx))
    if out:
        med = sorted(o[1] / o[2] for o in out)[len(out) // 2]
        for t0, dy, dx in out:
            dtm = datetime.datetime.fromtimestamp(t0, datetime.timezone.utc)
            print('   %s %s  +%4.1f pts of 5h  U %5.1f  x%.2f' % (dtm.strftime('%m-%d %H:%M'), DOW[dtm.weekday()], dy, dx, dy / dx / med))
    print(' no +-: too few days; groups with U ~ 0 are dropped; x is relative, the unit itself is a fitted number (see weights above).')


def burn_report():
    """--burn: does the burn rate (limit % per $) differ by hour / mode / weekday / day? Read-only, from the two raw axes
    (HISTORY + TOKENS): every interval between consecutive samples of ONE weekly window (<= 90 min) is spread over its
    hours by the cost spent in them. Index 1.00 = the average. The limit has whole-% resolution (1% of the week ~ $10),
    so a group needs a few $100 before its index means anything; the CI is a bootstrap over whole days."""
    B = collections.defaultdict(float)
    for r in read_tokens():
        B[r[0] // TOK_BUCKET] += sum(x * y for x, y in zip(r[5], price_for(r[1]))) / 1e6
    S = []
    try:
        for line in open(HISTORY):
            p = line.split()
            if len(p) >= 4 and p[0].isdigit() and p[1].isdigit() and p[3].isdigit():
                S.append((int(p[0]), int(p[1]), int(p[2]) if p[2].isdigit() else None, int(p[3])))
    except OSError:
        pass
    flagged = set()
    try:                                                          # hours of outside use (another machine) are not my cost
        o = (json.load(open(OUT + '.json')).get('outside_use') or {})
        flagged = {int(k) for k in (o.get('flagged_hours_utc (excess 5h points)') or {}) if str(k).isdigit()}
    except Exception:
        pass
    rows = []                                                     # (day, hour of day, mode, limit, d%, $)
    for (t1, w1, f1, r1), (t2, w2, f2, r2) in zip(S, S[1:]):
        if not 0 < t2 - t1 <= 5400 or w2 < w1 or r1 != r2:
            continue
        parts = {}
        for b in range(t1 // TOK_BUCKET, (t2 - 1) // TOK_BUCKET + 1):
            ov = min(t2, (b + 1) * TOK_BUCKET) - max(t1, b * TOK_BUCKET)
            if ov > 0 and b in B:
                h = b * TOK_BUCKET // 3600
                parts[h] = parts.get(h, 0.0) + B[b] * ov / TOK_BUCKET
        tot = sum(parts.values())
        if tot <= 0 or any(h in flagged for h in parts):
            continue
        for lim, d in (('wk', w2 - w1), ('5h', (f2 - f1) if f1 is not None and f2 is not None and f2 >= f1 else None)):
            if d is not None:
                for h, c in parts.items():
                    rows.append((h // 24, h % 24, mode_of(h * 3600), lim, d / 100.0 * c / tot, c))
    if not rows:
        print('no usable intervals yet (need history samples of the same weekly window plus token buckets)')
        return
    days = sorted({r[0] for r in rows})
    DOW = 'Mon Tue Wed Thu Fri Sat Sun'.split()
    dow = lambda d: DOW[(d + 3) % 7]
    print('history %s -> %s UTC, %d days; outside-use hours skipped: %d' % (
        datetime.datetime.fromtimestamp(S[0][0], datetime.timezone.utc).strftime('%m-%d %H:%M'),
        datetime.datetime.fromtimestamp(S[-1][0], datetime.timezone.utc).strftime('%m-%d %H:%M'), len(days), len(flagged)))
    if len(days) < 14:
        print('NOTE: %d days - the fit of mode factors needs >= 14; read the groups below as a hint, not a result' % len(days))
    groups = [('mode (eco / normal / peak, UTC schedule)', lambda r: r[2], lambda k: ('eco', 'normal', 'peak')[k]),
              ('UTC hour block', lambda r: r[1] // 3 * 3, lambda k: '%02d-%02d' % (k, k + 3)),
              ('weekday', lambda r: (r[0] + 3) % 7, lambda k: DOW[k]),
              ('day', lambda r: r[0], lambda k: '%s %s' % (datetime.date.fromtimestamp(k * 86400).isoformat(), dow(k)))]
    rnd = random.Random(1)
    for lim in ('wk', '5h'):
        R = [r for r in rows if r[3] == lim]
        y0, c0 = sum(r[4] for r in R), sum(r[5] for r in R)
        if c0 <= 0 or y0 <= 0:
            continue
        k0 = y0 / c0
        print('\n== %s limit: %.1f points over $%.0f -> %.3f %%/$ (1%% ~ $%.0f)' % (lim, y0, c0, k0, 1 / k0))
        for title, key, name in groups:
            print(' ' + title)
            G = collections.defaultdict(lambda: [0.0, 0.0])
            for r in R:
                g = G[key(r)]; g[0] += r[4]; g[1] += r[5]
            for gk in sorted(G):
                y, c = G[gk]
                idx = (y / c) / k0 if c > 0 else None
                ci = ''
                if title not in ('day', 'weekday') and len(days) >= 4 and c > 0 or title == 'weekday' and len(days) >= 14 and c > 0:
                    vals = []
                    for _ in range(300):
                        cnt = collections.Counter(rnd.choice(days) for _ in days)
                        ty = sum(r[4] * cnt[r[0]] for r in R); tc = sum(r[5] * cnt[r[0]] for r in R)
                        gy = sum(r[4] * cnt[r[0]] for r in R if key(r) == gk); gc = sum(r[5] * cnt[r[0]] for r in R if key(r) == gk)
                        if gc > 0 and tc > 0 and ty > 0:
                            vals.append((gy / gc) / (ty / tc))
                    vals.sort()
                    if len(vals) > 20:
                        ci = '  90%% CI %.2f..%.2f' % (vals[int(.05 * len(vals))], vals[int(.95 * len(vals))])
                print('  %-14s $%6.0f  %5.1f pts  idx %s%s' % (name(gk), c, y, '%.2f' % idx if idx is not None else '-', ci))


def collect_guarded():
    """--collect, the one entry for cron and the status lines: collector + usage poll. A crash is counted; after
    COLLECT_STOP_AFTER in a row it stops for good (the status lines then do not even start it) until `--resume`."""
    stop, fp = os.path.join(HSL_STATE, 'collect-stopped.lock'), os.path.join(HSL_STATE, 'collect-fails')
    if os.path.exists(stop):
        return
    try:
        collect_main()
        os.makedirs(HSL_STATE, exist_ok=True)
        poll_usage(int(time.time()))
    except Exception as e:
        try:
            os.makedirs(HSL_STATE, exist_ok=True)
            try:
                n = int(open(fp).read().split()[0]) + 1
            except (OSError, ValueError, IndexError):
                n = 1
            msg = '%s: %s' % (type(e).__name__, str(e)[:160].replace('\n', ' '))
            atomic_write(fp, '%d %d %s\n' % (n, time.time(), msg))
            if n >= COLLECT_STOP_AFTER:
                atomic_write(stop, 'stopped: %s UTC\nreason: %s (crash #%d in a row)\neffect: the collector (activity, work/spd) and the usage poll do not run; the status lines still record their own response limits\nresume: python3 -I %s --resume   (or delete this file)\n' % (
                    datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), msg, n, os.path.abspath(__file__)))
        except Exception:
            pass
    else:
        try:
            os.remove(fp)
        except OSError:
            pass


def health():
    """--health: the state of the collector and the usage poll (no network, nothing written)."""
    now = time.time()
    def rd(n, d=HSL_STATE):
        try:
            return open(os.path.join(d, n)).read().strip()
        except OSError:
            return ''
    print('collector: %s' % ('STOPPED: %s' % rd('collect-stopped.lock') if rd('collect-stopped.lock') else
                             'ok' if not rd('collect-fails') else 'failing (%s)' % rd('collect-fails')))
    nt, fails, st, strikes, stopped = poll_read(os.path.join(ANTH_DIR, 'usage-poll'))
    print('usage poll: %s | last status %s | consecutive failures %d, errors in a row %d/%d | next try %s' % (
        'STOPPED' if stopped else 'running', st, fails, strikes, POLL_STOP_AFTER,
        'never (until resumed)' if stopped else 'in %d s' % max(0, nt - now) if nt else '-'))
    if stopped:
        print('  ' + rd(POLL_STOP_FILE, ANTH_DIR).replace('\n', '\n  '))
    d = sl_cache_dir()
    try:
        age = now - int(open(os.path.join(d, 'lim')).read().split()[0])
        print('limits from the sessions: %d s old (%s)' % (age, 'fresh, no poll needed' if age < POLL_MIN else 'stale, the poll may ask'))
    except Exception:
        print('limits from the sessions: none')
    if rd('collect-stopped.lock'):
        print('-> `python3 -I %s --resume` after fixing the cause' % os.path.abspath(__file__))


def main():
    if '--burn' in sys.argv[1:]:
        burn_report()
        burn_fit()
        return
    if '--health' in sys.argv[1:]:
        health()
        return
    if '--resume' in sys.argv[1:]:
        for d, n in ((HSL_STATE, 'collect-stopped.lock'), (HSL_STATE, 'collect-fails'), (ANTH_DIR, 'usage-poll'), (ANTH_DIR, POLL_STOP_FILE)):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
        print('resumed: collector and usage poll errors cleared')
        return
    if '--collect' in sys.argv[1:]:
        collect_guarded()
        return
    if not os.path.isdir(PROJECTS):                  # no Claude Code here: connection profiles only
        write_conn_profiles(time.time(), {})
        return
    os.makedirs(ANTH_DIR, exist_ok=True)
    lock = open(os.path.join(ANTH_DIR, 'analyze.mutex'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                      # another run is active
    args, override = sys.argv[1:], None
    if '--wk-x100' in args and '--reset' in args:
        override = (int(args[args.index('--wk-x100') + 1]), int(args[args.index('--reset') + 1]))

    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    rescan = st.get('v') != STATE_VERSION
    if rescan:                            # record layout changed: rescan the transcripts, keep the old per-slot cost
        st = {k: st[k] for k in ('frozen', 'cal') if k in st}
    st['v'] = STATE_VERSION
    st.setdefault('files', {}); st.setdefault('msgs', {}); st.setdefault('cal', None); st.setdefault('frozen', {})

    now = time.time()
    for f in glob.glob(PROJECTS + '/**/*.jsonl', recursive=True):
        if os.path.getmtime(f) >= now - KEEP_DAYS * 86400:
            scan(f, st)
    imported = {}
    for d in sorted(glob.glob(os.path.join(IMPORTS, '*'))):      # copies of other machines' transcripts (read-only)
        host = re.sub(r'\W', '_', os.path.basename(d))
        if not os.path.isdir(d):
            continue
        for f in glob.glob(d + '/**/*.jsonl', recursive=True):
            if os.path.getmtime(f) >= now - KEEP_DAYS * 86400:
                scan(f, st, host)
        imported[host] = 0
    cut = int((now - KEEP_DAYS * 86400) // SLOT)
    for k, v in st['msgs'].items():                  # expiring messages: keep their cost per slot
        if v[0] < cut and not rescan:            # after a rescan the old slots are already in 'frozen'
            st['frozen'][str(v[0])] = st['frozen'].get(str(v[0]), 0.0) + v[1]
    st['msgs'] = {k: v for k, v in st['msgs'].items() if v[0] >= cut}
    st['frozen'] = {k: v for k, v in st['frozen'].items() if int(k) >= int((now - FROZEN_DAYS * 86400) // SLOT)}
    st['files'] = {k: v for k, v in st['files'].items() if os.path.exists(k)}
    try:
        tok_new = append_tokens(st['msgs'], now)
        for host in imported:
            imported[host] = {'messages': sum(1 for v in st['msgs'].values() if len(v) > 13 and v[13] == host),
                              'token_buckets_appended': append_tokens(st['msgs'], now, '%s@%s' % (TOKENS, host), host)}
    except OSError as e:
        tok_new = 'error: %s' % e
    o_hours, o_flag, o_med = outside_use(now, st['msgs'])

    buckets = collections.defaultdict(float)
    for slot, c, *_ in st['msgs'].values():
        buckets[slot] += c
    for k, c in st['frozen'].items():
        buckets[int(k)] += c
    cur = int(now) // SLOT

    # ---- weekly profile: mean cost per slot-of-week over the last PROFILE_DAYS (missing = 0) ----
    first = min(buckets) if buckets else cur
    cov0 = max(first, cur - PROFILE_DAYS * 24)
    occ, tot = [0] * SLOTS, [0.0] * SLOTS
    for s in range(cov0, cur):
        occ[s % SLOTS] += 1
        tot[s % SLOTS] += buckets.get(s, 0.0)
    mean = [tot[i] / occ[i] if occ[i] else 0.0 for i in range(SLOTS)]

    mf = analyze_factors(read_samples(), buckets, mean, skip=o_flag)
    fm = mf['mode_factors']
    fac = [x if x is not None else 1.0 for x in fm]                    # eco, norm, peak; 1 until anything is known
    wb = {sl_: c_ * fac[mode_of(sl_ * SLOT)] for sl_, c_ in buckets.items()}   # cost weighted by how fast each mode burns the limit
    # ---- calibration: % of the weekly limit per USD, from this week's cost vs current wk% ----
    lim = override or last_limits()
    scale, basis = None, 'none'
    if lim and lim[1] > now - 3600:
        wk_pct, week_start = (lim[0] + wk_credit_of(lim[1])) / 100.0, lim[1] - 604800
        week_cost = sum(c for s, c in wb.items() if s * SLOT >= week_start)
        o_wk = sum(o_hours[s][1] for s in o_flag if s * SLOT >= week_start)     # outside use: not my cost
        o_cost = sum(wb.get(s, 0.0) for s in o_flag if s * SLOT >= week_start)
        wk_pct, week_cost = wk_pct - o_wk, week_cost - o_cost
        if wk_pct >= 1.0 and week_cost > 0:
            scale, basis = wk_pct / week_cost, 'this week: %.1f%% / $%.2f' % (wk_pct, week_cost) + (
                ' (without %d outside-use hours: %.0f%% / $%.2f)' % (len([s for s in o_flag if s * SLOT >= week_start]), o_wk, o_cost) if o_wk or o_cost else '')
            if not override:
                st['cal'] = {'scale': scale, 'at': now}
    if scale is None and st.get('cal'):
        scale, basis = st['cal']['scale'], 'previous calibration'

    if scale is not None:
        line2 = ' '.join(str(int(round(m * scale * 1000))) for m in mean)   # MATRIX 1 (my load): thousandths of a % of the weekly limit per hour of the week, at the normal burn rate
    else:
        line2 = '-'
    line3 = ' '.join(str(int(round(x * 100))) if x is not None else '-' for x in fm)    # eco norm peak, hundredths
    line4 = ' '.join(str(int(round(fac[mode_of(sw * SLOT)] * 100))) for sw in range(SLOTS))   # MATRIX 2 (limit burn speed): factor x100 per hour of the week
    line6 = 'prelim' if mf.get('prelim') else ('fit' if mf['grid'] else '-')           # how the factors were obtained
    line5 = '%d %d %d' % tuple(SCHED)                      # peak start, peak end, eco end (UTC hours) - the status line follows these
    # line 7: MY USUAL PACE WHILE WORKING - thousandths of a % of the weekly limit per ACTIVE hour at the normal burn
    # rate (idle hours excluded, unlike matrix 1); the base of `work`. "-" until there is a calibration
    ar_usd, act_hours = active_rate([(v[8] if len(v) > 8 else v[0] * SLOT, v[1]) for v in st['msgs'].values()], cov0, cur)
    line7 = '%d' % round(ar_usd * scale * 1000) if scale and ar_usd else '-'
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        f.write('%d %s %.1f\n%s\n%s\n%s\n%s\n%s\n%s\n' % (now, ('%.6f' % scale) if scale else '-', (cur - cov0) / 24.0, line2, line3, line4, line5, line6, line7))
    os.replace(tmp, OUT)

    # ---- token burn per model / per mode / per hour of day, over the profile window ----
    hours = max((cur - cov0), 1.0)
    modehours = [0.0] * 3
    for s_ in range(cov0, cur):
        modehours[mode_of(s_ * SLOT)] += 1.0
    models, by_hour, tot_tok = {}, [0.0] * 24, {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0}
    out_by_mode = [0] * 3
    for slot, c, key, i_, o_, cr_, cw_, *_ in st['msgs'].values():
        if not (cov0 <= slot < cur):
            continue
        md = mode_of(slot * SLOT)
        by_hour[(slot * SLOT // 3600) % 24] += c
        out_by_mode[md] += o_
        m = models.setdefault(key, {'messages': 0, 'cost_usd': 0.0,
                                    'tokens': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                                    'cost_usd_by_mode(eco,norm,peak)': [0.0, 0.0, 0.0]})
        m['messages'] += 1
        m['cost_usd'] += c
        m['cost_usd_by_mode(eco,norm,peak)'][md] += c
        for k_, v_ in (('input', i_), ('output', o_), ('cache_read', cr_), ('cache_write', cw_)):
            m['tokens'][k_] += v_
            tot_tok[k_] += v_
    total_cost = sum(m['cost_usd'] for m in models.values()) or 1.0
    for m in models.values():
        m['share_of_cost'] = round(m['cost_usd'] / total_cost, 3)
        m['output_tokens_per_hour'] = round(m['tokens']['output'] / hours)
        m['cost_usd'] = round(m['cost_usd'], 2)
        m['cost_usd_by_mode(eco,norm,peak)'] = [round(x, 2) for x in m['cost_usd_by_mode(eco,norm,peak)']]

    # ---- long-term daily measures log: one line per completed UTC day (survives the 15-day state window) ----
    have = set()
    try:
        have = {json.loads(l)['day'] for l in open(MEASURES) if l.strip()}
    except OSError:
        pass
    days = collections.defaultdict(lambda: {'cost_usd': 0.0, 'messages': 0, 'tokens': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0}, 'models': {}})
    for slot, c, key, i_, o_, cr_, cw_, *_ in st['msgs'].values():
        day = time.strftime('%Y-%m-%d', time.gmtime(slot * SLOT))
        d = days[day]
        d['cost_usd'] += c; d['messages'] += 1
        for k_, v_ in (('input', i_), ('output', o_), ('cache_read', cr_), ('cache_write', cw_)):
            d['tokens'][k_] += v_
        dm = d['models'].setdefault(key, {'cost_usd': 0.0, 'output': 0})
        dm['cost_usd'] += c; dm['output'] += o_
    wk_last = {}
    try:
        for line in open(HISTORY):
            p_ = line.split()
            if len(p_) >= 2 and p_[0].isdigit() and p_[1].isdigit():
                wk_last[time.strftime('%Y-%m-%d', time.gmtime(int(p_[0])))] = int(p_[1])
    except OSError:
        pass
    today = time.strftime('%Y-%m-%d', time.gmtime(now))
    first_day = time.strftime('%Y-%m-%d', time.gmtime(cut * SLOT + 86400))      # first day with full data
    new = [d for d in sorted(days) if d < today and d >= first_day and d not in have]
    if new:
        with open(MEASURES, 'a') as f:
            for d in new:
                r = days[d]
                r['day'] = d
                r['cost_usd'] = round(r['cost_usd'], 3)
                for dm in r['models'].values():
                    dm['cost_usd'] = round(dm['cost_usd'], 3)
                if d in wk_last:
                    r['wk_pct_x100_last_sample'] = wk_last[d]
                f.write(json.dumps(r, separators=(',', ':')) + '\n')

    share, modecost = [0.0] * 3, [0.0] * 3
    for s in range(cov0, cur):
        modecost[mode_of(s * SLOT)] += buckets.get(s, 0.0)
    summary = {
        'updated': int(now), 'messages': len(st['msgs']), 'coverage_days': round((cur - cov0) / 24.0, 1),
        'scale_pct_per_usd': scale, 'calibration': basis,
        'avg_weekly_cost_usd': round(sum(mean), 2),
        'avg_weekly_pct_at_scale': round(sum(mean) * scale, 1) if scale else None,
        'active_hours': round(act_hours, 1),
        'cost_usd_per_active_hour': round(ar_usd, 2) if ar_usd else None,
        'wk_pct_per_active_hour (work base)': round(ar_usd * scale, 3) if scale and ar_usd else None,
        'token_log_buckets_appended': tok_new,
        'imports': imported or 'none (copies of other machines\' ~/.claude/projects go to %s/<host>/)' % IMPORTS,
        'outside_use': {
            'median_5h_pct_per_usd': round(o_med, 3) if o_med else None,
            'hours_seen': len(o_hours),
            'status': 'active' if o_med and sum(1 for h in o_hours.values() if h[2] >= OUT_MIN_USD) >= OUT_MIN_HOURS else
                      'collecting (%d/%d hours with >= $%g)' % (sum(1 for h in o_hours.values() if h[2] >= OUT_MIN_USD), OUT_MIN_HOURS, OUT_MIN_USD),
            'flagged_hours_utc (excess 5h points)': {time.strftime('%Y-%m-%d %H', time.gmtime(s_ * SLOT)): x for s_, x in sorted(o_flag.items())},
            'excess_5h_points_total': round(sum(o_flag.values()), 1),
            'rule': '5h step > %.0fx median x local cost + %.0f points; excluded from calibration and the mode fit' % (OUT_RATIO, OUT_MIN_PTS)},
        'cost_usd_by_mode(eco,norm,peak)': [round(x, 2) for x in modecost],
        'wall_hours_covered': hours,
        'tokens_total': tot_tok,
        'output_tokens_per_hour': round(tot_tok['output'] / hours),
        'cost_usd_per_hour_by_mode(eco,norm,peak)': [round(modecost[i] / modehours[i], 2) if modehours[i] else None for i in range(3)],
        'output_tokens_per_hour_by_mode(eco,norm,peak)': [round(out_by_mode[i] / modehours[i]) if modehours[i] else None for i in range(3)],
        'cost_usd_by_hour_utc_avg_per_day': [round(x / max(hours / 24.0, 1), 2) for x in by_hour],
        'burn_factors': {k: v for k, v in mf.items() if k != 'factors_slot'},
        'models': models,
        'daily_measures_appended': new,
        'assumptions': 'cache write 1.25x/2x of input, Haiku 5.5 cache read 0.1x - assumed; only ratios matter',
    }
    json.dump(summary, open(OUT + '.json', 'w'), indent=1)
    if not override:
        tmp = STATE + '.tmp'
        json.dump(st, open(tmp, 'w'))
        os.replace(tmp, STATE)
    try:
        summary['connections'] = write_conn_profiles(now, {k: v for k, v in st['msgs'].items() if len(v) < 14})   # this machine only
    except Exception as e:                            # never lose the Claude profile because of another harness
        summary['connections'] = 'error: %s' % e
    json.dump(summary, open(OUT + '.json', 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
