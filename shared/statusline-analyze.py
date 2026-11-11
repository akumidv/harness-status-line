#!/usr/bin/env python3
"""Status-line analyzer shared by all harnesses (Claude Code, Qwen Code, mcode, OpenCode, pi).

Two kinds of connection (the API endpoint a harness talks to, keyed by host):
  * limited  - a subscription with 5h/weekly limits (Claude Code passes rate_limits): load matrix + burn speed,
               calibrated to the weekly limit % (the original Claude analysis below, files in ~/.claude);
  * free / metered - no limits (own gateway, pay-per-token): load matrix in tokens (free) or USD (metered) and
               a SPEED matrix - how fast the models answer in each hour of the week vs their own median, pooled
               over models = the load of the endpoint. Files in ~/.local/state/harness-status-line/conn-<host>.
Every connection also gets the speed matrix and per-model median speeds (Claude: the colour of the speed).

  statusline-analyze.py              daily profile (background, nice 19)
  statusline-analyze.py --collect    fast collector (every ~5 min): activity hours of ALL sessions (VS Code
                                     extension, OpenCode, pi, ...), current pace and speed per connection -> tmpfs

Claude part: weekly token-COST profile in 1-hour slots, calibrated to the weekly limit %.

Reads Claude Code transcripts (read-only, incrementally: only bytes added since the last run),
weights every assistant message by price (per model and token type) and buckets it into the
168 hourly slots of a week. The weekly limit % is account-wide, so the absolute scale
("% of the weekly limit per USD") is calibrated from this week's cost vs the current wk%.

Writes ~/.claude/.statusline-profile (shell-readable) and .statusline-profile.json (inspection).
Run in the background once a day by statusline-command.sh. stdlib only.
usage: statusline-analyze.py [--wk-x100 N --reset EPOCH]   (override calibration, for tests; not persisted)
"""
import collections, datetime, fcntl, glob, json, os, random, re, sys, time

CLAUDE = os.path.expanduser('~/.claude')
PROJECTS = os.path.join(CLAUDE, 'projects')
STATE = os.path.join(CLAUDE, '.statusline-analyze-state.json')
OUT = os.path.join(CLAUDE, '.statusline-profile')
MEASURES = os.path.join(CLAUDE, '.statusline-measures.jsonl')   # one record per completed UTC day (long-term log)
STATE_VERSION = 7                # 5: per-message start/end (speed); 6: + cache write 1h (5m = cw - cw1h); 7: + effort, speed, thinking
HISTORY = os.path.join(CLAUDE, '.statusline-history')            # LIMIT axis: "epoch wk_x100 5h_x100 wk_reset 5h_reset"
TOKENS = os.path.join(CLAUDE, '.statusline-tokens')              # TOKEN axis: raw tokens per 5-min bucket, model, effort, speed (never trimmed)
TOK_HEADER = '# epoch(5min bucket, UTC) model effort speed msgs input output cache_read cache_write_5m cache_write_1h thinking(of output)\n'
TOK_BUCKET = 300
SLOT, SLOTS = 3600, 168          # 1-hour grid; 168 slots = one week. slot index = (epoch // 3600) % 168
KEEP_DAYS, PROFILE_DAYS, FROZEN_DAYS = 15, 14, 120   # frozen = per-slot cost kept after per-message detail expires
HOME = os.path.expanduser('~')
HSL_STATE = os.path.join(HOME, '.local/state/harness-status-line')        # durable: connection profiles, collector state
HSL_CFG = os.path.join(HOME, '.config/harness-status-line/connections.json')   # optional overrides (billing, prices)
SPEED_DAYS = 42                  # speed matrix window (needs more weeks than the load: one hour of the week = 1 sample/week)
SPEED_K = 4.0                    # pseudo-requests at index 1 per hour of the week (shrinkage of the speed matrix)
MIN_OUT, MIN_DUR = 50, 1.0       # a response counts for speed only from 50 output tokens and 1 s
BUSY, LIGHT = 0.92, 1.08         # speed index thresholds of the load modes (busy = slower than usual)
ANTHROPIC = 'api.anthropic.com'

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


def build_pairs(samples, buckets):
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
        if not cs:
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


def analyze_factors(samples, buckets, mean, min_pairs=150, min_cost=100.0, min_days=14, n_perm=20):
    """Pick the time grid the data supports (cross-validated by day-blocks), fit it, report factors per week slot."""
    res = {'status': 'insufficient data', 'grid': None, 'factors_slot': None, 'mode_factors': [None] * 3}
    pairs = build_pairs(samples, buckets)
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


def scan(path, st):
    """Incremental read of one Claude transcript. msgs[id] = [slot, cost, model, in, out, cache_read, cache_write,
    start, end, cache_write_1h, effort, speed, thinking]: start = the previous entry (the request), end = the last
    streamed copy -> response speed; cache_write = 5m + 1h; effort = reasoning effort of the request (low..max, '-' if
    absent), speed = 'standard' or 'fast' (fast mode), thinking = thinking tokens (part of output)."""
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
                         (u.get('output_tokens_details') or {}).get('thinking_tokens') or 0]
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


def append_tokens(msgs, now):
    """TOKEN axis (raw, no prices): append completed 5-min buckets newer than the file's last one.
    Line: TOK_HEADER (bucket start, message end time; key = bucket, model, effort, speed). Kept forever (~150 lines
    a day); the LIMIT axis is HISTORY - join them by epoch for any analysis. A file with another header (older
    layout) is moved aside to '<file>.<epoch>' (never deleted) and the new one is backfilled from the messages kept."""
    last = 0
    try:
        with open(TOKENS) as f:
            head = f.readline()
        if head != TOK_HEADER:
            os.rename(TOKENS, '%s.%d' % (TOKENS, int(now)))
    except OSError:
        pass
    try:
        with open(TOKENS, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            for line in f.read().split(b'\n'):
                p = line.split()
                if p and p[0].isdigit():
                    last = int(p[0])
    except OSError:
        pass
    upto = (int(now) - TOK_BUCKET) // TOK_BUCKET * TOK_BUCKET          # buckets ending >= 5 min ago are complete
    agg = {}
    for v in msgs.values():
        if len(v) < 13:
            continue
        b = int(v[8]) // TOK_BUCKET * TOK_BUCKET
        if not (last < b < upto):
            continue
        a = agg.setdefault((b, v[2], v[10], v[11]), [0] * 7)
        a[0] += 1; a[1] += v[3]; a[2] += v[4]; a[3] += v[5]; a[4] += v[6] - v[9]; a[5] += v[9]; a[6] += v[12]
    if not agg:
        return 0
    new = not os.path.exists(TOKENS)
    with open(TOKENS, 'a') as f:
        if new:
            f.write(TOK_HEADER)
        for k, a in sorted(agg.items()):
            f.write('%d %s %s\n' % (k[0], ' '.join(re.sub(r'\s', '_', x) for x in k[1:]), ' '.join(map(str, a))))
    return len(agg)


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
    cfg = read_json(HSL_CFG)
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
        p = os.path.join(HSL_STATE, 'conn-' + key)
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
        lines = open(os.path.join(HSL_STATE, 'conn-' + key)).read().split('\n')
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


def collect_main():
    now = time.time()
    os.makedirs(HSL_STATE, exist_ok=True)
    lock = open(os.path.join(HSL_STATE, 'collect.lock'), 'w')
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



def main():
    if '--collect' in sys.argv[1:]:
        return collect_main()
    if not os.path.isdir(PROJECTS):                  # no Claude Code here: connection profiles only
        write_conn_profiles(time.time(), {})
        return
    lock = open(STATE + '.lock', 'w')
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
    cut = int((now - KEEP_DAYS * 86400) // SLOT)
    for k, v in st['msgs'].items():                  # expiring messages: keep their cost per slot
        if v[0] < cut and not rescan:            # after a rescan the old slots are already in 'frozen'
            st['frozen'][str(v[0])] = st['frozen'].get(str(v[0]), 0.0) + v[1]
    st['msgs'] = {k: v for k, v in st['msgs'].items() if v[0] >= cut}
    st['frozen'] = {k: v for k, v in st['frozen'].items() if int(k) >= int((now - FROZEN_DAYS * 86400) // SLOT)}
    st['files'] = {k: v for k, v in st['files'].items() if os.path.exists(k)}
    try:
        tok_new = append_tokens(st['msgs'], now)
    except OSError as e:
        tok_new = 'error: %s' % e

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

    mf = analyze_factors(read_samples(), buckets, mean)
    fm = mf['mode_factors']
    fac = [x if x is not None else 1.0 for x in fm]                    # eco, norm, peak; 1 until anything is known
    wb = {sl_: c_ * fac[mode_of(sl_ * SLOT)] for sl_, c_ in buckets.items()}   # cost weighted by how fast each mode burns the limit
    # ---- calibration: % of the weekly limit per USD, from this week's cost vs current wk% ----
    lim = override or last_limits()
    scale, basis = None, 'none'
    if lim and lim[1] > now - 3600:
        wk_pct, week_start = lim[0] / 100.0, lim[1] - 604800
        week_cost = sum(c for s, c in wb.items() if s * SLOT >= week_start)
        if wk_pct >= 1.0 and week_cost > 0:
            scale, basis = wk_pct / week_cost, 'this week: %.1f%% / $%.2f' % (wk_pct, week_cost)
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
        summary['connections'] = write_conn_profiles(now, st['msgs'])
    except Exception as e:                            # never lose the Claude profile because of another harness
        summary['connections'] = 'error: %s' % e
    json.dump(summary, open(OUT + '.json', 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
