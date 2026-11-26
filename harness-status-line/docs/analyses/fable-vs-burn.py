"""Does Fable (own limit system) burn the weekly limit differently from the other models? Read-only.
Segments of the weekly limit as in ctx-vs-burn.py; features = $ of non-Fable and Fable (Fable priced with the analyzer's
DEFAULT_PRICE, an assumption: there is no Fable row in PRICES) and Fable tokens by type; non-negative regression + a timeline of today."""
import os, sys
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ctx-vs-burn.py')).read().split("print('\\n== regressions")[0].replace("MINPTS = float(sys.argv[1]) if len(sys.argv) > 1 else 3", "MINPTS = 2"))
isf = lambda b: 'fable' in b[1]
print('\n== Fable in the token axis')
for d in sorted({b[0] // 86400 for b in B if isf(b)}):
    s = [b for b in B if isf(b) and b[0] // 86400 == d]
    allb = [b for b in B if b[0] // 86400 == d]
    print(' %s  fable msgs %3d  out %7d  read %9d  write %8d  $(assumed price) %.1f of %.1f  (%.0f%% of the day)' % (
        datetime.date.fromtimestamp(d * 86400), sum(b[2] for b in s), sum(b[5][1] for b in s), sum(b[5][2] for b in s),
        sum(b[5][3] + b[5][4] for b in s), sum(sum(b[4]) for b in s), sum(sum(b[4]) for b in allb), 100 * sum(sum(b[4]) for b in s) / sum(sum(b[4]) for b in allb)))
print('\n== regression: non-Fable $ and Fable $ (segments %d)' % len(seg))
X = [feat(s[0], s[1], lambda b: [0.0 if isf(b) else sum(b[4]), sum(b[4]) if isf(b) else 0.0]) or [0.0, 0.0] for s in seg]
fit_report('M0 one price', [[x[0] + x[1]] for x in X], y, day, names=['$'])
fit_report('Mf $ non-Fable | $ Fable', X, y, day, names=['other', 'fable'])
Xo = [feat(s[0], s[1], lambda b: [0.0 if isf(b) else b[5][1] / 1e6, 0.0 if isf(b) else b[5][2] / 1e6, 0.0 if isf(b) else (b[5][3] + b[5][4]) / 1e6,
                                  b[5][1] / 1e6 if isf(b) else 0.0, b[5][2] / 1e6 if isf(b) else 0.0, (b[5][3] + b[5][4]) / 1e6 if isf(b) else 0.0]) or [0.0] * 6 for s in seg]
fit_report('Mt tokens: other out/read/write | Fable', Xo, y, day, names=['o.out', 'o.read', 'o.write', 'f.out', 'f.read', 'f.write'])
print('\n== segments containing Fable vs not (pts per $ at the non-Fable price; excess = what the segment burned beyond 0.081 x $)')
for s, u, x in zip(seg, usd, X):
    if x[1] > 0.5:
        print(' %s..%s  pts %4.1f  $ %6.1f (fable $ %4.1f = %2.0f%%)  pts/$ %.3f  expected %.1f' % (
            datetime.datetime.utcfromtimestamp(s[0]).strftime('%m-%d %H:%M'), datetime.datetime.utcfromtimestamp(s[1]).strftime('%H:%M'),
            s[2], u[0], x[1], 100 * x[1] / u[0], s[2] / u[0], 0.081 * u[0]))
nf = [(s, u) for s, u, x in zip(seg, usd, X) if x[1] <= 0.5]
print(' without Fable: %d segments, %.1f pts on $%.1f = %.3f pts/$' % (len(nf), sum(s[2] for s, u in nf), sum(u[0] for s, u in nf), sum(s[2] for s, u in nf) / sum(u[0] for s, u in nf)))
wf = [(s, u) for s, u, x in zip(seg, usd, X) if x[1] > 0.5]
print(' with Fable   : %d segments, %.1f pts on $%.1f = %.3f pts/$' % (len(wf), sum(s[2] for s, u in wf), sum(u[0] for s, u in wf), sum(s[2] for s, u in wf) / sum(u[0] for s, u in wf)))
print('\n== today (UTC): limit samples and Fable buckets')
t0 = max(b[0] for b in B) // 86400 * 86400
for t, v in H:
    if t >= t0 and (t // 60) % 20 < 2:
        pass
last = None
for t, v in H:
    if t >= t0 and v != last:
        f = sum(b[2] for b in B if isf(b) and t - 1800 <= b[0] <= t); a = sum(sum(b[4]) for b in B if t - 1800 <= b[0] <= t)
        print(' %s wk %5.1f%%   last 30 min: $%.1f  fable msgs %d' % (datetime.datetime.utcfromtimestamp(t).strftime('%H:%M'), v / 100, a, f)); last = v
