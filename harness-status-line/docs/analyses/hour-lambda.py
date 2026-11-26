import os
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schedule-tz.py')).read().split("# hour-level rows")[0])
for lam in (1.0, 0.3, 0.1, 0.03):
    an.HOUR_LAMBDA = lam
    r = an.derive_hourly(pairs)
    print('lambda', lam, 'mode factors', r['mode_factors'])
    for dt in ('weekday','weekend'):
        print('  ', dt, r['windows_utc'][dt])
        print('     ', ' '.join('%s' % ('-' if x is None else int(x*100)) for x in r['hourly_factor_utc'][dt]))
