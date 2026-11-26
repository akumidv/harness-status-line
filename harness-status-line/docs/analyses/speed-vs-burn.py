import os, math, statistics as st
d=os.path.expanduser('~/.local/state/harness-status-line/')
C=open(d+'api.anthropic.com/conn').read().split('\n')
spd=[int(x) for x in C[2].split()]          # speed index x100 per week slot
P=open(d+'api.anthropic.com/profile').read().split('\n')
hf=[int(x) for x in P[7].split()]; mm=P[4]; L=[int(x) for x in P[1].split()]  # load: 0.001% per hour
use=[x for x in L]
assert len(spd)==168==len(hf)
# slots where both have data: hf>0 (burn data) and speed matrix != 100 exactly (pseudo-response default means few responses)
pairs=[(hf[i],spd[i],int(mm[i]),use[i]) for i in range(168) if hf[i]>0 and spd[i]!=100 and use[i]>0]
print('hours with both burn and speed data:',len(pairs))
def corr(a,b):
    ma,mb=st.mean(a),st.mean(b); 
    num=sum((x-ma)*(y-mb) for x,y in zip(a,b)); den=math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
    return num/den if den else float('nan')
def rank(v):
    o=sorted(range(len(v)),key=lambda i:v[i]); r=[0]*len(v)
    for k,i in enumerate(o): r[i]=k
    return r
a=[p[0] for p in pairs]; b=[p[1] for p in pairs]
print('Pearson burn-factor vs speed-index: %.2f   Spearman: %.2f'%(corr(a,b),corr(rank(a),rank(b))))
names=('eco','normal','peak')
for m in range(3):
    s=[p[1] for p in pairs if p[2]==m]
    if s: print('  %-6s hours %3d  speed idx mean %.1f  (min %d max %d)  burn factor mean %.2f'%(names[m],len(s),st.mean(s),min(s),max(s),st.mean([p[0] for p in pairs if p[2]==m])/100))
print('speed index overall: mean %.1f sd %.1f; burn factor sd %.2f'%(st.mean(b),st.pstdev(b),st.pstdev(a)/100))
# by day type: hourly speed list
DOW='Mon Tue Wed Thu Fri Sat Sun'.split()
print('\nspeed index x100 by hour (weekday = Mon, weekend = Sat), UTC:')
for lab,dd in (('Mon',0),('Sat',5)):
    day=(dd+4)%7
    print(lab,' '.join('%3d'%spd[(day*24+h)%168] for h in range(24)))
