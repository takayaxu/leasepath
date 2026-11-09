"""Offline reference solvers. Never import this module in a model-facing tool."""
from collections import deque
from fractions import Fraction
from functools import lru_cache
from itertools import combinations, permutations, product
from math import gcd, lcm


def solve(task):
    p=task['problem'];k=task['kind'];score=0
    if k=='job_shop':
        ops=p['operations'];machines=sorted({m for m,d in ops});best=None
        orders=[list(permutations([i for i,(m,d) in enumerate(ops) if m==machine])) for machine in machines]
        for order in product(*orders):
            edges=list(map(tuple,p['precedence']))+[e for seq in order for e in zip(seq,seq[1:])]
            start=[0]*len(ops);remaining=set(range(len(ops)))
            while remaining:
                ready=[v for v in remaining if all(a not in remaining for a,b in edges if b==v)]
                if not ready:break
                for v in ready:
                    start[v]=max((start[a]+ops[a][1] for a,b in edges if b==v),default=0);remaining.remove(v)
            if remaining:continue
            end=max(start[i]+d for i,(m,d) in enumerate(ops))
            if best is None or end<best[0]:best=(end,start)
        score,candidate=best
    elif k=='propositional_sat':
        for mask in range(1<<p['variables']):
            truth={i+1 if mask&(1<<i) else -(i+1) for i in range(p['variables'])}
            if all(truth.intersection(c) for c in p['clauses']):
                candidate=[int(i+1 in truth) for i in range(p['variables'])];break
        else:raise ValueError('unsatisfiable fixture')
    elif k=='package_resolution':
        possible=[]
        for v in product(range(1,4),repeat=4):
            if v[0]==1:continue
            if any(v[a]==x and v[b]<y for a,x,b,y in p['dependencies']):continue
            if any((v[a],v[b])==(x,y) for a,x,b,y in p['conflicts']):continue
            possible.append(v)
        candidate=list(min(possible,key=lambda v:(sum(v),v)));score=sum(candidate)
    elif k=='max_flow':
        n=p['nodes'];s=p['source'];t=p['sink'];res=[[0]*n for _ in range(n)]
        for a,b,cap in p['edges']:res[a][b]=cap
        while True:
            prev={s:None};q=deque([s])
            while q and t not in prev:
                a=q.popleft()
                for b in range(n):
                    if res[a][b]>0 and b not in prev:prev[b]=a;q.append(b)
            if t not in prev:break
            path=[];b=t
            while b!=s:a=prev[b];path.append((a,b));b=a
            amount=min(res[a][b] for a,b in path)
            for a,b in path:res[a][b]-=amount;res[b][a]+=amount
        candidate=[cap-res[a][b] for a,b,cap in p['edges']]
        # Independently establish the optimum with the min-cut theorem.
        interior=[x for x in range(n) if x not in (s,t)]
        cuts=[]
        for mask in range(1<<len(interior)):
            left={s}|{x for i,x in enumerate(interior) if mask&(1<<i)}
            cuts.append(sum(cap for a,b,cap in p['edges'] if a in left and b not in left))
        score=-min(cuts)
    elif k=='exact_cover':
        def search(remaining,selected):
            if not remaining:return selected
            x=min(remaining)
            for i,s in enumerate(p['subsets']):
                if x in s and set(s)<=remaining:
                    answer=search(remaining-set(s),selected+[i])
                    if answer is not None:return answer
        candidate=search(set(p['universe']),[])
    elif k=='error_correction':
        prime=p['prime'];rows=[[pow(x,i,prime) for i in range(3)]+[y] for x,y in p['symbols']]
        for col in range(3):
            pivot=next(i for i in range(col,len(rows)) if rows[i][col]%prime)
            rows[col],rows[pivot]=rows[pivot],rows[col]
            inv=pow(rows[col][col],-1,prime);rows[col]=[x*inv%prime for x in rows[col]]
            for i in range(len(rows)):
                if i!=col:
                    factor=rows[i][col];rows[i]=[(a-factor*b)%prime for a,b in zip(rows[i],rows[col])]
        candidate=[rows[i][-1] for i in range(3)]
    elif k=='bin_packing':
        weights=p['sizes'];n=len(weights);full=(1<<n)-1
        @lru_cache(None)
        def pack(mask):
            if not mask:return ()
            first=mask & -mask;best=None;sub=mask
            while sub:
                if sub&first and sum(w for i,w in enumerate(weights) if sub&(1<<i))<=p['capacity']:
                    result=(sub,)+pack(mask^sub)
                    if best is None or len(result)<len(best):best=result
                sub=(sub-1)&mask
            return best
        bins=pack(full);candidate=[next(b for b,mask in enumerate(bins) if mask&(1<<i)) for i in range(n)];score=len(bins)
    elif k=='automata_reachability':
        q=deque([(p['start'],'')]);seen={p['start']}
        while q:
            state,word=q.popleft()
            if state==p['accept']:candidate=word;score=len(word);break
            for symbol,nxt in zip(p['alphabet'],p['transitions'][state]):
                if nxt not in seen:seen.add(nxt);q.append((nxt,word+symbol))
        else:raise ValueError('unreachable fixture')
    elif k=='access_control':
        allow=set().union(*map(set,p['allow_policies']));deny=set().union(*map(set,p['deny_policies']))
        candidate=[int(x in allow-deny) for x in p['actions']]
    elif k=='reaction_balance':
        matrix=[[Fraction(x) for x in row] for row in p['signed_atom_matrix']]
        # This fixture has two independent conservation laws, last coefficient free.
        for col in range(2):
            pivot=next(i for i in range(col,2) if matrix[i][col]);matrix[col],matrix[pivot]=matrix[pivot],matrix[col]
            divisor=matrix[col][col];matrix[col]=[x/divisor for x in matrix[col]]
            for i in range(2):
                if i!=col:
                    factor=matrix[i][col];matrix[i]=[x-factor*y for x,y in zip(matrix[i],matrix[col])]
        vector=[-matrix[0][2],-matrix[1][2],Fraction(1)];den=lcm(*(v.denominator for v in vector))
        vector=[int(v*den) for v in vector];div=gcd(*vector);candidate=[v//div for v in vector];score=sum(candidate)
    elif k=='voting_rule':
        # Three-candidate independent two-round tabulation; no shared evaluator.
        valid=[]
        for ballot in permutations(p['candidates']):
            ballots=p['ballots']+[list(ballot)]
            counts={x:sum(b[0]==x for b in ballots) for x in p['candidates']}
            last=sorted(counts,key=lambda x:(counts[x],-ord(x)))[0]
            finalists=[x for x in p['candidates'] if x!=last]
            votes={x:sum(next(c for c in b if c!=last)==x for b in ballots) for x in finalists}
            winner=sorted(votes,key=lambda x:(-votes[x],x))[0]
            if winner==p['target']:
                inv=sum(p['original'].index(ballot[i])>p['original'].index(ballot[j]) for i in range(3) for j in range(i+1,3))
                valid.append((inv,ballot))
        score,ballot=min(valid);candidate=list(ballot)
    elif k=='edit_distance':
        a,b=p['source'],p['target'];dp=[[0]*(len(b)+1) for _ in range(len(a)+1)]
        for i in range(len(a)+1):dp[i][0]=i
        for j in range(len(b)+1):dp[0][j]=j
        for i in range(1,len(a)+1):
            for j in range(1,len(b)+1):dp[i][j]=min(dp[i-1][j]+1,dp[i][j-1]+1,dp[i-1][j-1]+(a[i-1]!=b[j-1]))
        i,j=len(a),len(b);ops=[];score=dp[i][j]
        while i or j:
            if i and j and dp[i][j]==dp[i-1][j-1]+(a[i-1]!=b[j-1]):ops.append('M' if a[i-1]==b[j-1] else 'S');i-=1;j-=1
            elif i and dp[i][j]==dp[i-1][j]+1:ops.append('D');i-=1
            else:ops.append('I');j-=1
        candidate=''.join(reversed(ops))
    else:raise ValueError('unknown reference kind')
    return {'candidate':candidate,'optimum_minimize':score,'solver':'independent_agent_reference_v1'}
