#!/usr/bin/env python3
"""Status-line helper: weekly token-COST profile in 1-hour slots, calibrated to the weekly limit %.

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
STATE_VERSION = 4
HISTORY = os.path.join(CLAUDE, '.statusline-history')
SLOT, SLOTS = 3600, 168          # 1-hour grid; 168 slots = one week. slot index = (epoch // 3600) % 168
KEEP_DAYS, PROFILE_DAYS, FROZEN_DAYS = 15, 14, 120   # frozen = per-slot cost kept after per-message detail expires

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


def scan(path, st):
    size = os.path.getsize(path)
    rec = st['files'].get(path)
    off = rec[1] if rec and rec[0] <= size else 0
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
        if b'"usage"' not in line:
            continue
        try:
            d = json.loads(line)
            if d.get('type') != 'assistant':
                continue
            m = d.get('message') or {}
            u, mid, ts = m.get('usage'), m.get('id'), d.get('timestamp')
            if not u or not mid or not ts or m.get('model') == '<synthetic>':
                continue
            t = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        except Exception:
            continue
        c = cost(u, price_for(m.get('model')))
        prev = msgs.get(mid)
        if prev is None or c >= prev[1]:          # streamed copies of one message: keep the fullest
            cc = u.get('cache_creation') or {}
            cw = ((cc.get('ephemeral_5m_input_tokens') or 0) + (cc.get('ephemeral_1h_input_tokens') or 0)) or (u.get('cache_creation_input_tokens') or 0)
            msgs[mid] = [int(t) // SLOT, c, model_key(m.get('model')), u.get('input_tokens') or 0,
                         u.get('output_tokens') or 0, u.get('cache_read_input_tokens') or 0, cw]
    st['files'][path] = [size, off + end + 1]


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


def main():
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
    if st.get('v') != STATE_VERSION:      # record layout changed: rescan from scratch
        st = {}
    st['v'] = STATE_VERSION
    st.setdefault('files', {}); st.setdefault('msgs', {}); st.setdefault('cal', None); st.setdefault('frozen', {})

    now = time.time()
    for f in glob.glob(PROJECTS + '/**/*.jsonl', recursive=True):
        if os.path.getmtime(f) >= now - KEEP_DAYS * 86400:
            scan(f, st)
    cut = int((now - KEEP_DAYS * 86400) // SLOT)
    for k, v in st['msgs'].items():                  # expiring messages: keep their cost per slot
        if v[0] < cut:
            st['frozen'][str(v[0])] = st['frozen'].get(str(v[0]), 0.0) + v[1]
    st['msgs'] = {k: v for k, v in st['msgs'].items() if v[0] >= cut}
    st['frozen'] = {k: v for k, v in st['frozen'].items() if int(k) >= int((now - FROZEN_DAYS * 86400) // SLOT)}
    st['files'] = {k: v for k, v in st['files'].items() if os.path.exists(k)}

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
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        f.write('%d %s %.1f\n%s\n%s\n%s\n%s\n%s\n' % (now, ('%.6f' % scale) if scale else '-', (cur - cov0) / 24.0, line2, line3, line4, line5, line6))
    os.replace(tmp, OUT)

    # ---- token burn per model / per mode / per hour of day, over the profile window ----
    hours = max((cur - cov0), 1.0)
    modehours = [0.0] * 3
    for s_ in range(cov0, cur):
        modehours[mode_of(s_ * SLOT)] += 1.0
    models, by_hour, tot_tok = {}, [0.0] * 24, {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0}
    out_by_mode = [0] * 3
    for slot, c, key, i_, o_, cr_, cw_ in st['msgs'].values():
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
    for slot, c, key, i_, o_, cr_, cw_ in st['msgs'].values():
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
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
