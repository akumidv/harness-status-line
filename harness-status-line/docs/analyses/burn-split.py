import importlib.util, collections, random, sys, os
spec = importlib.util.spec_from_file_location('an', os.path.expanduser('~/.local/share/harness-status-line/statusline-analyze.py'))
an = importlib.util.module_from_spec(spec); spec.loader.exec_module(an)
# measures per bucket: dollars, input, output, cache_read, cache_write(5m+1h), all raw tokens (Mtok)
M = ['usd', 'output', 'cache_read', 'cache_write', 'input']
B = collections.defaultdict(lambda: [0.0]*5)
for r in an.read_tokens():
    t = r[5]  # TOK_FIELDS order
    f = dict(zip(an.TOK_FIELDS, t))
    usd = sum(x*y for x, y in zip(t, an.price_for(r[1])))/1e6
    v = [usd, f['output']/1e6, f['cache_read']/1e6, (f['cache_write_5m']+f['cache_write_1h'])/1e6, f['input']/1e6]
    b = B[r[0]//an.TOK_BUCKET]
    for i, x in enumerate(v): b[i] += x
S=[]
for line in open(an.HISTORY):
    p=line.split()
    if len(p)>=4 and p[0].isdigit() and p[1].isdigit() and p[3].isdigit():
        S.append((int(p[0]),int(p[1]),int(p[2]) if p[2].isdigit() else None,int(p[3])))
flagged=set()
rows=[]  # day, hour, mode, lim, dpts, vec(5)
for (t1,w1,f1,r1),(t2,w2,f2,r2) in zip(S,S[1:]):
    if not 0<t2-t1<=5400 or w2<w1 or r1!=r2: continue
    parts={}
    for b in range(t1//an.TOK_BUCKET,(t2-1)//an.TOK_BUCKET+1):
        ov=min(t2,(b+1)*an.TOK_BUCKET)-max(t1,b*an.TOK_BUCKET)
        if ov>0 and b in B:
            h=b*an.TOK_BUCKET//3600
            pv=parts.setdefault(h,[0.0]*5)
            for i in range(5): pv[i]+=B[b][i]*ov/an.TOK_BUCKET
    tot=sum(p[0] for p in parts.values())
    if tot<=0: continue
    for lim,d in (('wk',w2-w1),('5h',(f2-f1) if f1 is not None and f2 is not None and f2>=f1 else None)):
        if d is None: continue
        for h,v in parts.items():
            rows.append((h//24,h%24,an.mode_of(h*3600),lim,d/100.0*v[0]/tot,v))
days=sorted({r[0] for r in rows}); rnd=random.Random(1)
names=('eco','normal','peak')
for lim in ('wk','5h'):
    R=[r for r in rows if r[3]==lim]
    print('\n===== %s limit =====' % lim)
    # mix per mode
    print('mix per mode:   usd   out(Mtok) read(Mtok) write(Mtok)  | read/out  out$share')
    for m in range(3):
        v=[sum(r[5][i] for r in R if r[2]==m) for i in range(5)]
        print('  %-7s %7.0f %9.1f %10.1f %10.1f  | %7.0f' % (names[m],v[0],v[1],v[2],v[3], v[2]/v[1] if v[1] else 0))
    for mi,mn in enumerate(M[:4]):
        y0=sum(r[4] for r in R); c0=sum(r[5][mi] for r in R); k0=y0/c0
        print('-- pts per %s (avg %.4f pts/unit; unit = $ or Mtok)' % (mn,k0))
        for m in range(3):
            y=sum(r[4] for r in R if r[2]==m); c=sum(r[5][mi] for r in R if r[2]==m)
            vals=[]
            for _ in range(300):
                cnt=collections.Counter(rnd.choice(days) for _ in days)
                ty=sum(r[4]*cnt[r[0]] for r in R); tc=sum(r[5][mi]*cnt[r[0]] for r in R)
                gy=sum(r[4]*cnt[r[0]] for r in R if r[2]==m); gc=sum(r[5][mi]*cnt[r[0]] for r in R if r[2]==m)
                if gc>0 and tc>0 and ty>0: vals.append((gy/gc)/(ty/tc))
            vals.sort()
            ci='%.2f..%.2f'%(vals[int(.05*len(vals))],vals[int(.95*len(vals))]) if len(vals)>20 else ''
            print('   %-7s units %8.1f  idx %.2f  CI %s' % (names[m],c,(y/c)/k0 if c else 0,ci))
print('\n===== per day: pts per $ by mode (weekdays only have peak) =====')
import datetime
for lim in ('wk','5h'):
    R=[r for r in rows if r[3]==lim]
    print(lim)
    for d in days:
        out=[]
        for m in range(3):
            y=sum(r[4] for r in R if r[0]==d and r[2]==m); c=sum(r[5][0] for r in R if r[0]==d and r[2]==m)
            out.append('%s %5.3f ($%4.0f, %4.1f pts)'%(names[m][:4],y/c if c>20 else float('nan'),c,y))
        print('  %s %s  %s' % (datetime.date.fromtimestamp(d*86400).isoformat(), 'MTWTFSS'[(d+3)%7], ' | '.join(out)))
