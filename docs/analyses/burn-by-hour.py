import os
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'burn-split.py')).read().split("days=sorted")[0])
import collections
for lim in ('wk','5h'):
    R=[r for r in rows if r[3]==lim]
    k0=sum(r[4] for r in R)/sum(r[5][0] for r in R)
    print('\n==',lim,'avg %.3f'%k0)
    for wd,lab in ((True,'weekday'),(False,'weekend')):
        G=collections.defaultdict(lambda:[0,0])
        for r in R:
            if ((r[0]+3)%7<5)==wd: G[r[1]][0]+=r[4]; G[r[1]][1]+=r[5][0]
        print(lab,' hr: idx($)')
        print('  '+'  '.join('%02d:%s'%(h,('%.2f($%d)'%(G[h][0]/G[h][1]/k0,G[h][1])) if G[h][1]>5 else '  -  ') for h in range(24)))
    # window scan on weekdays: contiguous [a,b) peak window, idx inside vs outside (weekday only)
    W=[r for r in R if (r[0]+3)%7<5]
    best=[]
    for a in range(24):
        for L in range(3,10):
            ins=[r for r in W if (r[1]-a)%24<L]; out=[r for r in W if (r[1]-a)%24>=L]
            ci=sum(r[5][0] for r in ins); co=sum(r[5][0] for r in out)
            if ci<150 or co<150: continue
            ki=sum(r[4] for r in ins)/ci; ko=sum(r[4] for r in out)/co
            best.append((ki/ko,a,(a+L)%24,ci))
    best.sort()
    print('weekday window in/out ratio: cheapest',[('%.2f'%x[0],'%02d-%02d'%x[1:3],'$%d'%x[3]) for x in best[:4]])
    print('                            dearest ',[('%.2f'%x[0],'%02d-%02d'%x[1:3],'$%d'%x[3]) for x in best[-4:]])
    cur=[x for x in best if (x[1],x[2])==(13,19)]
    print('  current 13-19:',[('%.2f'%x[0],'$%d'%x[3]) for x in cur])
