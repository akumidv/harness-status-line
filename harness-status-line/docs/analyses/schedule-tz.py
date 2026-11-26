import importlib.util, collections, os, json, datetime
spec = importlib.util.spec_from_file_location('an', os.path.expanduser('~/.local/share/harness-status-line/statusline-analyze.py'))
an = importlib.util.module_from_spec(spec); spec.loader.exec_module(an)
buckets = collections.defaultdict(float)
for r in an.read_tokens():
    buckets[r[0]//3600] += sum(x*y for x,y in zip(r[5], an.price_for(r[1])))/1e6
prof = json.load(open(os.path.expanduser('~/.local/state/harness-status-line/api.anthropic.com/profile.json')))
skip = {int(time_) for time_ in []}
import time, calendar
fl = prof['outside_use']['flagged_hours_utc (excess 5h points)']
skip = {calendar.timegm(time.strptime(k, '%Y-%m-%d %H'))//3600 for k in fl}
pairs = an.build_pairs(an.read_samples(), buckets, skip)
# hour-level rows: (epoch_hour, yn_share, cost)
rows = []
for p in pairs:
    tot = sum(p['cs'].values())
    for sw, c in p['cs'].items():
        pass
# need real epoch hour: rebuild from samples directly
samples = an.read_samples()
H = collections.defaultdict(lambda: [0.0, 0.0])   # epoch hour -> [normalised pts, $]
for lim in ('wk','5h'):
    pts = []
    S = [(t,w,f) for t,w,f in samples]
    ivs = []
    for (t1,w1,f1),(t2,w2,f2) in zip(S,S[1:]):
        dt=t2-t1
        if dt<=0: continue
        if lim=='wk':
            if dt<=10800 and w2>=w1: ivs.append((t1,t2,(w2-w1)/100.0))
        else:
            if f1 is not None and f2 is not None and dt<=18000 and f2>=f1: ivs.append((t1,t2,(f2-f1)/100.0))
    Y=sum(y for *_,y in ivs); C=0; hrs=[]
    for t1,t2,y in ivs:
        cs={}
        for h in range(t1//3600,(t2-1)//3600+1):
            ov=min(t2,(h+1)*3600)-max(t1,h*3600)
            if ov>0 and h in buckets and h not in skip: cs[h]=buckets[h]*ov/3600
        tc=sum(cs.values())
        if tc<=0: continue
        hrs.append((y,cs,tc))
    Y=sum(y for y,_,_ in hrs); C=sum(tc for *_,tc in hrs); k=Y/C
    for y,cs,tc in hrs:
        for h,c in cs.items():
            H[(lim,h)][0]+= y*c/tc/k; H[(lim,h)][1]+= c
def local(h,o): return h+o   # local epoch-hour
def ratio(o, a, b):
    """published peak = weekdays local a..b in zone UTC+o; returns pooled index in/out over both limits"""
    yi=ci=yo=co=0.0
    for (lim,h),(y,c) in H.items():
        lh=(h+o)%24; d=(h+o)//24; dow=(d+3)%7   # Mon=0
        inside = dow<5 and a<=lh<b
        if inside: yi+=y; ci+=c
        else: yo+=y; co+=c
    return (yi/ci)/(yo/co), ci/2
print('Published peak 05-11 local in zone UTC+o (PT = -8 -> 13-19 UTC); pooled wk+5h index inside/outside window (1.0 = same, >1 = dearer inside):')
res=[]
for o in range(-12,15):
    r,c=ratio(o,5,11); res.append((o,r,c))
    print('  UTC%+3d  window UTC %02d-%02d  idx %.2f  ($%d)%s' % (o,(5-o)%24,(11-o)%24,r,c,'   <- PT (published)' if o==-8 else '   <- CET (you)' if o==1 else ''))
b=max(res,key=lambda x:x[1]); print('best fit zone: UTC%+d idx %.2f'%(b[0],b[1]))
# per weekday summary: daily index + mode windows from profile
L=open(os.path.expanduser('~/.local/state/harness-status-line/api.anthropic.com/profile')).read().split('\n')
mm=L[4]; hf=[int(x) for x in L[7].split()]
names=('eco','norm','peak'); DOW='Mon Tue Wed Thu Fri Sat Sun'.split()
print('\nActual modes by weekday (UTC hours; "." = no data, E = eco, n = normal, P = peak) and factor x100:')
print('     '+''.join('%02d '%h for h in range(24)))
for d in range(7):
    cells=[];fs=[]
    for h in range(24):
        # slot for dow d (Mon=0) hour h: epoch day with (day+3)%7==d -> day%7==(d-3)%7... find sw
        day=(d-3)%7  # epoch day index mod 7 with Mon=(day+3)%7==0 -> day%7==4 ; general: day%7=(d+4)%7
        day=(d+4)%7
        sw=(day*24+h)%168
        cells.append('.' if hf[sw]==0 else 'EnP'[int(mm[sw])]); fs.append(hf[sw])
    print('%s  '%DOW[d]+'  '.join(cells)+'   ')
    print('  x100 '+' '.join('%3s'%(f if f else ' -') for f in fs))
# daily index by weekday raw ($, idx pooled)
print('\nRaw index by weekday (pooled wk+5h, outside use excluded) and $:')
D=collections.defaultdict(lambda:[0.0,0.0])
for (lim,h),(y,c) in H.items():
    dow=((h//24)+3)%7; D[dow][0]+=y; D[dow][1]+=c
tot=sum(v[0] for v in D.values())/sum(v[1] for v in D.values())
for d in range(7): 
    if D[d][1]: print('  %s idx %.2f  $%d'%(DOW[d],D[d][0]/D[d][1]/tot,D[d][1]/2))
