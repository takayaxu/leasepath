"""Locally authored, source-family separated diagnostic fixtures.

These small instances exercise feedback-driven decisions. They are not public
benchmark samples or representative tool-duration data. No reference answers
are exposed by the tools. No old analytical task is relabelled as independent.
"""
import itertools


def fixtures():
    specs = [
        ('job_shop','development','Schedule four operations with minimum makespan. Candidate: integer start time per operation in listed order.',
         {'operations':[[0,2],[1,1],[1,2],[0,2]],'precedence':[[0,1],[2,3]],'start_max':8}),
        ('propositional_sat','development','Find a satisfying Boolean assignment. Candidate: list of 0/1 values for variables 1..5.',
         {'variables':5,'clauses':[[1,2],[-1,3],[-2,-3,4],[-4,5],[-5,2],[-2,3]]}),
        ('package_resolution','development','Resolve versions 1..3 for packages A,B,C,D. A must be at least 2. Minimize sum of version numbers. Dependencies are [package index, selected version, required package index, minimum version]. Candidate: four integer versions.',
         {'packages':['A','B','C','D'],'dependencies':[[0,2,1,2],[0,3,1,3],[1,2,2,3],[1,3,2,2],[2,3,3,2]],'conflicts':[[1,3,3,1]]}),
        ('max_flow','development','Find a maximum integral flow from node 0 to node 3. Candidate: nonnegative integer flow on each listed directed edge.',
         {'nodes':4,'source':0,'sink':3,'edges':[[0,1,3],[0,2,2],[1,2,1],[1,3,2],[2,3,3]]}),
        ('exact_cover','development','Select subsets covering every universe element exactly once. Candidate: list of distinct zero-based subset indices.',
         {'universe':[0,1,2,3,4,5],'subsets':[[0,2],[1,3],[4,5],[0,1,4],[2,3],[1,5],[3,4]]}),
        ('error_correction','development','Recover a degree-at-most-two message polynomial over GF(17) from surviving encoded symbols. Candidate: coefficients [constant, linear, quadratic], each 0..16.',
         {'prime':17,'symbols':[[1,10],[3,6],[5,9]],'erased_x':[2,4]}),
        ('bin_packing','holdout','Pack all items into the fewest bins of capacity 6. Candidate: one integer bin label 0..4 per item.',
         {'sizes':[4,3,3,2,2],'capacity':6}),
        ('automata_reachability','holdout','Reach accepting state 4 from state 0 with the shortest word over a,b. Candidate: the word string (at most 10 letters).',
         {'start':0,'accept':4,'transitions':[[1,0],[1,2],[3,0],[1,4],[4,4]],'alphabet':['a','b']}),
        ('access_control','holdout','Classify each action as allowed (1) or denied (0). Explicit deny overrides any allow; absent allow is deny. Candidate: 0/1 list in actions order.',
         {'actions':['read','write','delete','list','admin'],'allow_policies':[['read','write'],['list','delete']],'deny_policies':[['delete'],['admin']]}),
        ('reaction_balance','holdout','Balance Fe + O2 -> Fe2O3 using the smallest positive integer coefficients. Candidate: [Fe, O2, Fe2O3], each 1..12.',
         {'species':['Fe','O2','Fe2O3'],'signed_atom_matrix':[[1,0,-2],[0,2,-3]]}),
        ('voting_rule','holdout','Add one ballot to make B win instant-runoff voting, with minimum pair inversions relative to A,B,C. Eliminate the alphabetically greatest tied last-place candidate. Candidate: ranking permutation of A,B,C.',
         {'candidates':['A','B','C'],'ballots':[['A','C','B'],['A','B','C'],['B','C','A'],['C','B','A']],'target':'B','original':['A','B','C']}),
        ('edit_distance','holdout','Transform source into target at minimum unit edit cost. Candidate: operation string; M matches equal characters, S substitutes, D deletes source character, I inserts next target character. Consume both strings completely; at most 10 operations.',
         {'source':'GATT','target':'GCT'})]
    return [dict(id='independent_'+kind,source_family=kind,split=split,intent=intent,kind=kind,problem=problem,
                 input_origin='locally authored deterministic small instance; cited source supplies problem semantics, not this data',
                 max_generations=12,max_tool_calls=11) for kind,split,intent,problem in specs]


def ints(candidate,n,lo,hi):
    if not isinstance(candidate,list) or len(candidate)!=n or any(type(x) is not int or not lo<=x<=hi for x in candidate):
        raise ValueError(f'candidate must contain {n} integers in [{lo},{hi}]')


def evaluate(task,candidate):
    """Forward execution only: report actual constraint violations and objective."""
    p=task['problem'];k=task['kind'];bad=[];score=0
    if k=='job_shop':
        ints(candidate,len(p['operations']),0,p['start_max'])
        ends=[s+op[1] for s,op in zip(candidate,p['operations'])]
        for a,b in p['precedence']:
            if ends[a]>candidate[b]:bad.append(['precedence',a,b])
        for i,j in itertools.combinations(range(len(candidate)),2):
            if p['operations'][i][0]==p['operations'][j][0] and max(candidate[i],candidate[j])<min(ends[i],ends[j]):bad.append(['machine_overlap',i,j])
        score=max(ends)
    elif k=='propositional_sat':
        ints(candidate,p['variables'],0,1)
        bad=[['unsatisfied_clause',i] for i,c in enumerate(p['clauses']) if not any(bool(candidate[abs(x)-1])==(x>0) for x in c)]
    elif k=='package_resolution':
        ints(candidate,4,1,3)
        if candidate[0]<2:bad.append(['requested_A_at_least_2'])
        for a,v,b,w in p['dependencies']:
            if candidate[a]==v and candidate[b]<w:bad.append(['dependency',a,v,b,w])
        for a,v,b,w in p['conflicts']:
            if candidate[a]==v and candidate[b]==w:bad.append(['conflict',a,v,b,w])
        score=sum(candidate)
    elif k=='max_flow':
        ints(candidate,len(p['edges']),0,max(e[2] for e in p['edges']))
        net=[0]*p['nodes']
        for i,(f,(a,b,cap)) in enumerate(zip(candidate,p['edges'])):
            if f>cap:bad.append(['edge_capacity',i])
            net[a]-=f;net[b]+=f
        bad += [['conservation',i] for i,n in enumerate(net) if i not in (p['source'],p['sink']) and n]
        score=net[p['source']]  # minimized negative flow
    elif k=='exact_cover':
        if not isinstance(candidate,list) or not candidate:raise ValueError('nonempty subset indices required')
        ints(candidate,len(candidate),0,len(p['subsets'])-1)
        if len(set(candidate))!=len(candidate):bad.append(['duplicate_subsets'])
        counts={x:0 for x in p['universe']}
        for i in candidate:
            for x in p['subsets'][i]:counts[x]+=1
        bad += [['cover_multiplicity',x,n] for x,n in counts.items() if n!=1]
    elif k=='error_correction':
        ints(candidate,3,0,p['prime']-1)
        bad=[['symbol_mismatch',x] for x,y in p['symbols'] if sum(c*x**i for i,c in enumerate(candidate))%p['prime']!=y]
    elif k=='bin_packing':
        ints(candidate,len(p['sizes']),0,len(p['sizes'])-1)
        for b in set(candidate):
            weight=sum(w for w,label in zip(p['sizes'],candidate) if label==b)
            if weight>p['capacity']:bad.append(['over_capacity',b,weight])
        score=len(set(candidate))
    elif k=='automata_reachability':
        if not isinstance(candidate,str) or len(candidate)>10 or any(c not in p['alphabet'] for c in candidate):raise ValueError('bounded alphabet word required')
        state=p['start']
        for c in candidate:state=p['transitions'][state][p['alphabet'].index(c)]
        if state!=p['accept']:bad.append(['terminal_state',state])
        score=len(candidate)
    elif k=='access_control':
        ints(candidate,len(p['actions']),0,1)
        for i,a in enumerate(p['actions']):
            allowed=any(a in row for row in p['allow_policies']) and not any(a in row for row in p['deny_policies'])
            if candidate[i]!=int(allowed):bad.append(['incorrect_decision_index',i])
    elif k=='reaction_balance':
        ints(candidate,3,1,12)
        for i,row in enumerate(p['signed_atom_matrix']):
            residual=sum(a*b for a,b in zip(row,candidate))
            if residual:bad.append(['atom_residual',i,residual])
        score=sum(candidate)
    elif k=='voting_rule':
        if not isinstance(candidate,list) or sorted(candidate)!=sorted(p['candidates']):raise ValueError('candidate must be a ranking permutation')
        remaining=set(p['candidates']);rounds=[]
        while len(remaining)>1:
            counts={x:0 for x in remaining}
            for ballot in p['ballots']+[candidate]:counts[next(x for x in ballot if x in remaining)]+=1
            eliminate=max(remaining,key=lambda x:(-counts[x],x));remaining.remove(eliminate);rounds.append(counts)
        winner=next(iter(remaining))
        if winner!=p['target']:bad.append(['winner',winner])
        score=sum(candidate.index(a)>candidate.index(b) for a,b in itertools.combinations(p['original'],2))
    elif k=='edit_distance':
        if not isinstance(candidate,str) or len(candidate)>10 or any(x not in 'MSDI' for x in candidate):raise ValueError('bounded edit operation string required')
        i=j=0
        for op in candidate:
            if op in 'MS' and (i>=len(p['source']) or j>=len(p['target'])):raise ValueError('edit beyond sequence')
            if op=='M' and p['source'][i]!=p['target'][j]:bad.append(['nonmatching_M',i,j])
            if op in 'MSD':i+=1
            if op in 'MSI':j+=1
            if i>len(p['source']) or j>len(p['target']):raise ValueError('edit beyond sequence')
            score+=op!='M'
        if i!=len(p['source']) or j!=len(p['target']):bad.append(['incomplete_consumption',i,j])
    else:raise ValueError('unknown task kind')
    return {'valid':not bad,'objective_minimize':score,'violations':bad}
