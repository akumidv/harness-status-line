import importlib.util, sys, os, bisect, collections, itertools, math
HOME = os.path.expanduser('~')
sp = importlib.util.spec_from_file_location('an', HOME + '/.local/share/harness-status-line/statusline-analyze.py')
an = importlib.util.module_from_spec(sp); sp.loader.exec_module(an)
B = []   # (t, model, msgs, ctx_avg, usd_by_type[5], tokens)
for t, model, eff, spd, msgs, (inp, out, cr, w5, w1), th in an.read_tokens():
    p = an.price_for(model)
    usd = [inp*p[0]/1e6, out*p[1]/1e6, cr*p[2]/1e6, w5*p[3]/1e6, w1*p[4]/1e6]
    ctx = (inp + cr + w5 + w1) / max(msgs, 1)
    B.append((t, model, msgs, ctx, usd, (inp, out, cr, w5, w1)))
B.sort()
print('buckets', len(B), 'msgs', sum(b[2] for b in B))
# distribution of ctx, usd-weighted
tot = sum(sum(b[4]) for b in B)
for lo, hi in ((0,50e3),(50e3,100e3),(100e3,150e3),(150e3,200e3),(200e3,300e3),(300e3,500e3),(500e3,1e9)):
    s = [b for b in B if lo <= b[3] < hi]
    print('ctx %4.0fK-%-5.0fK buckets %4d msgs %6d usd %7.1f (%.0f%%)' % (lo/1e3, min(hi,9e8)/1e3, len(s), sum(b[2] for b in s), sum(sum(b[4]) for b in s), 100*sum(sum(b[4]) for b in s)/tot))
by_m = collections.defaultdict(lambda: [0,0,0.0])
for b in B:
    m = by_m[b[1]]; m[0]+=b[2]; m[1]+=b[3]*b[2]; m[2]+=sum(b[4])
for k, v in by_m.items(): print(k, 'msgs', v[0], 'avg ctx %.0fK' % (v[1]/max(v[0],1)/1e3), 'usd %.1f' % v[2])

# ---- segments of the weekly limit
H = [tuple(int(x) for x in l.split()[:2]) for l in open(an.HISTORY) if len(l.split()) >= 2 and l.split()[0].isdigit() and l.split()[1].isdigit()]
H.sort()
times = [b[0] for b in B]
def feat(t1, t2, fn):
    """sum fn(bucket) over buckets overlapping [t1,t2], fractionally"""
    out = None
    i = bisect.bisect_left(times, t1 - 300)
    while i < len(B) and times[i] < t2:
        b = B[i]; ov = min(t2, b[0] + 300) - max(t1, b[0])
        if ov > 0:
            v = fn(b)
            out = [(out[k] if out else 0) + v[k] * ov / 300 for k in range(len(v))]
        i += 1
    return out
import datetime
def utc(s): return int(datetime.datetime.strptime(s, '%Y-%m-%d %H').replace(tzinfo=datetime.timezone.utc).timestamp())
skip = [(utc('2026-11-12 05'), utc('2026-11-12 08'))]
MINPTS = float(sys.argv[1]) if len(sys.argv) > 1 else 3
seg = []
i0 = 0
for j in range(1, len(H)):
    if H[j][1] < H[j-1][1]:      # reset
        i0 = j; continue
    if H[j][0] - H[j-1][0] > 4*3600:   # long gap: start again
        i0 = j; continue
    if H[j][1] - H[i0][1] >= MINPTS * 100:
        t1, t2 = H[i0][0], H[j][0]
        if not any(t1 < b and t2 > a for a, b in skip):
            seg.append((t1, t2, (H[j][1] - H[i0][1]) / 100.0))
        i0 = j
print('segments', len(seg), 'pts', sum(s[2] for s in seg), 'hours', sum(s[1]-s[0] for s in seg)/3600)

def nnls(X, y, wt=None, iters=500):
    m = len(X[0]); wt = wt or [1.0]*len(y)
    G = [[sum(w*r[a]*r[b] for r, w in zip(X, wt)) for b in range(m)] for a in range(m)]
    c = [sum(w*r[a]*v for r, v, w in zip(X, y, wt)) for a in range(m)]
    b = [0.0]*m
    for _ in range(iters):
        for a in range(m):
            if G[a][a] > 1e-12:
                b[a] = max(0.0, (c[a] - sum(G[a][k]*b[k] for k in range(m) if k != a)) / G[a][a])
    return b
def loo(X, y, day, wt=None):
    e = []; days = sorted(set(day))
    for d in days:
        tr = [i for i in range(len(y)) if day[i] != d]; te = [i for i in range(len(y)) if day[i] == d]
        b = nnls([X[i] for i in tr], [y[i] for i in tr], [wt[i] for i in tr] if wt else None)
        e += [(y[i] - sum(bj*xj for bj, xj in zip(b, X[i])))**2 for i in te]
    return (sum(e)/len(e))**.5
def fit_report(name, X, y, day, wt=None, names=None):
    b = nnls(X, y, wt)
    r = (sum((yy - sum(bj*xj for bj, xj in zip(b, x)))**2 for x, yy in zip(X, y))/len(y))**.5
    print('%-34s rmse %.2f  LOO %.2f  coefs %s' % (name, r, loo(X, y, day, wt), ' '.join('%s=%.3f' % (n, v) for n, v in zip(names or range(len(b)), b))))

y = [s[2] for s in seg]; day = [s[1]//86400 for s in seg]
usd = [feat(s[0], s[1], lambda b: [sum(b[4])]) or [0.0] for s in seg]
print('overall pts/$ = %.4f' % (sum(y)/sum(u[0] for u in usd)))
# per-segment avg ctx (msgs-weighted)
mc = [feat(s[0], s[1], lambda b: [b[2], b[2]*b[3]]) for s in seg]
actx = [(m[1]/m[0]) if m and m[0] else 0 for m in mc]
# 1) ratio by segment context, binned
rows = sorted(r for r in zip(actx, y, [u[0] for u in usd]) if r[0] > 0 and r[2] > 0)   # segments with no local tokens (other machine) are dropped
print('\n== burn per $ vs the segment avg ctx (terciles/quartiles of segments)')
n = len(rows); q = 4
for k in range(q):
    r = rows[k*n//q:(k+1)*n//q]
    print(' ctx %3.0fK..%3.0fK  segs %2d  pts %5.1f  $ %6.1f  pts/$ %.4f' % (r[0][0]/1e3, r[-1][0]/1e3, len(r), sum(a[1] for a in r), sum(a[2] for a in r), sum(a[1] for a in r)/sum(a[2] for a in r)))
import statistics
print(' corr(log ctx, pts/$ ) = %.2f' % statistics.correlation([math.log(a[0]) for a in rows], [a[1]/a[2] for a in rows]))

print('\n== regressions on %d segments (y = limit points; non-negative coefs; LOO by day)' % len(y))
def cls(b, edges):
    c = sum(1 for e in edges if b[3] >= e); v = [0.0]*(len(edges)+1); v[c] = sum(b[4]); return v
X0 = usd
fit_report('M0 one price (all $ alike)', X0, y, day, names=['$'])
for edges, nm in (((100e3,),'ctx<100K|>=100K'), ((120e3,),'ctx<120K|>=120K'), ((150e3,),'ctx<150K|>=150K'), ((200e3,),'ctx<200K|>=200K'),
                  ((100e3,150e3,200e3),'<100|100-150|150-200|200+'), ((90e3,130e3,180e3),'<90|90-130|130-180|180+')):
    X = [feat(s[0], s[1], lambda b: cls(b, edges)) or [0.0]*(len(edges)+1) for s in seg]
    fit_report('M1 $ by ctx class '+nm, X, y, day, names=['c%d' % i for i in range(len(edges)+1)])
# per-request term
X = [feat(s[0], s[1], lambda b: [sum(b[4]), b[2]/1000.0]) or [0.0, 0.0] for s in seg]
fit_report('M2 $ + per request', X, y, day, names=['$', 'req/1000'])
# $ by type + per request
X = [feat(s[0], s[1], lambda b: b[4] + [b[2]/1000.0]) or [0.0]*6 for s in seg]
fit_report('M3 $ by type + per request', X, y, day, names=['in', 'out', 'read', 'w5', 'w1', 'req/1000'])
X = [feat(s[0], s[1], lambda b: b[4]) or [0.0]*5 for s in seg]
fit_report('M3b $ by type (no req)', X, y, day, names=['in', 'out', 'read', 'w5', 'w1'])
# super-linear ctx: $ * (ctx/100K)^k
for k in (-0.5, -0.25, 0.25, 0.5, 1.0, 1.5):
    X = [feat(s[0], s[1], lambda b, k=k: [sum(b[4]) * (max(b[3], 1)/100e3)**k]) or [0.0] for s in seg]
    fit_report('M4 $ x (ctx/100K)^%g' % k, X, y, day, names=['s'])
# read part only scales with ctx? cache_read tokens * ctx (attention cost ~ tokens x ctx)
X = [feat(s[0], s[1], lambda b: [sum(b[4]), b[2]*b[3]/1e9]) or [0.0, 0.0] for s in seg]
fit_report('M5 $ + msgs*ctx^2 (quadratic)', X, y, day, names=['$', 'm*ctx^2/1e9'])

# ---- per day: burn per $ vs avg ctx, big-ctx share
print('\n== per day (weekly limit)')
byd = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
for s, u, m in zip(seg, usd, mc):
    d = byd[s[1]//86400]; d[0] += s[2]; d[1] += u[0]; d[2] += m[1] if m else 0; d[3] += m[0] if m else 0
rows = []
for d, v in sorted(byd.items()):
    big = feat(d*86400, d*86400+86400, lambda b: [sum(b[4]) if b[3] >= 150e3 else 0.0, sum(b[4])])
    rows.append((d, v[0], v[1], v[2]/v[3] if v[3] else 0, big[0]/big[1] if big and big[1] else 0))
    print(' %s %s  pts %5.1f $ %6.1f pts/$ %.4f  avg ctx %4.0fK  $ share ctx>=150K %2.0f%%' % (datetime.date.fromtimestamp(d*86400), 'MTWTFSS'[(d+3)%7], v[0], v[1], v[0]/v[1], v[2]/v[3]/1e3 if v[3] else 0, 100*rows[-1][4]))
if len(rows) > 3:
    print(' corr(day avg ctx, pts/$) = %.2f   corr(share>=150K, pts/$) = %.2f' % (statistics.correlation([r[3] for r in rows], [r[1]/r[2] for r in rows]), statistics.correlation([r[4] for r in rows], [r[1]/r[2] for r in rows])))

# ---- per hour of day: avg ctx vs the data's hourly burn factor
import json
pj = json.load(open(HOME + '/.local/state/harness-status-line/api.anthropic.com/profile.json'))

r2 = [r for r in rows if r[0] != max(x[0] for x in rows)]
print('\nwithout the incomplete last day: corr(avg ctx, pts/$) = %.2f   corr(share>=150K, pts/$) = %.2f' % (
    statistics.correlation([r[3] for r in r2], [r[1]/r[2] for r in r2]), statistics.correlation([r[4] for r in r2], [r[1]/r[2] for r in r2])))

L = open(HOME + '/.local/state/harness-status-line/api.anthropic.com/profile').read().split('\n')
hx = [int(x) for x in L[7].split()]
agg = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
for b in B:
    h = (b[0] // 3600) % 168
    a = agg[h]; a[0] += b[2]; a[1] += b[2]*b[3]; a[2] += sum(b[4])
xs, ys, ws = [], [], []
for h in range(168):
    a = agg[h]
    if hx[h] and a[0] > 30 and a[2] >= 15:
        xs.append(a[1]/a[0]); ys.append(hx[h]/100.0); ws.append(a[2])
print('\nhours of the week with data: %d; corr(avg ctx of the hour, hourly burn factor) = %.2f; Spearman-ish by rank = %.2f' % (
    len(xs), statistics.correlation(xs, ys), statistics.correlation([sorted(xs).index(v) for v in xs], [sorted(ys).index(v) for v in ys])))
# weighted terciles
pairs = sorted(zip(xs, ys, ws)); n = len(pairs)
for k in range(3):
    r = pairs[k*n//3:(k+1)*n//3]
    print(' ctx %3.0fK..%3.0fK hours %2d weighted burn factor %.2f' % (r[0][0]/1e3, r[-1][0]/1e3, len(r), sum(a[1]*a[2] for a in r)/sum(a[2] for a in r)))
