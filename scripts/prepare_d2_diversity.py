"""Prepare 34 previously unused MBPP parents with independent deterministic oracles."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# IDs, domains, and oracle cases are frozen before agent outcomes. Candidate
# calls use fresh literals, so mutations cannot modify the oracle's inputs.
ORACLES = {
    19: ('sequence', 'Integer list, including empty.', '''
for xs in [[], [1], [1,1], [-2,0,2]] + [rng.choices(range(-5,6), k=n) for n in range(30)]:
    add((xs,), len(set(xs)) < len(xs))
'''),
    57: ('digits', 'Nonempty list of digits 0..9; duplicates allowed.', '''
for xs in [[0], [0,0], [1,0,1]] + [rng.choices(range(10), k=n) for n in range(1,31)]:
    add((xs,), int(''.join(map(str, sorted(xs, reverse=True)))))
'''),
    58: ('bitwise', 'Opposite sign means one integer is negative and the other is nonnegative; zero is nonnegative.', '''
for a,b in itertools.product([-100,-2,-1,0,1,2,99], repeat=2):
    add((a,b), (a < 0) != (b < 0))
'''),
    59: ('number_sequence', 'n is a positive integer.', '''
value = 0
for n in range(1,36):
    value += 6*n - 5
    add((n,), value)
'''),
    61: ('substring_counting', 's contains ASCII digits; empty input is valid. Count all contiguous nonempty substrings.', '''
for s in ['', '0', '1', '9', '11111'] + [''.join(rng.choices('0123', k=n)) for n in range(1,30)]:
    total = sum(sum(map(int,s[a:b])) == b-a for a in range(len(s)) for b in range(a+1,len(s)+1))
    add((s,), total)
'''),
    62: ('selection', 'Nonempty integer list.', '''
for xs in [[0], [-10], [4,4]] + [rng.choices(range(-100,101),k=n) for n in range(1,31)]:
    add((xs,), sorted(xs)[0])
'''),
    63: ('tuple_reduction', 'Nonempty list of integer pairs. Return the maximum absolute difference within a pair.', '''
for n in range(1,36):
    xs = [(rng.randrange(-100,101), rng.randrange(-100,101)) for _ in range(n)]
    add((xs,), sorted(abs(a-b) for a,b in xs)[-1])
'''),
    64: ('stable_sort', 'Sort ascending by numeric second value, preserving input order for ties. Empty list is valid.', '''
for n in range(36):
    xs = [(f'x{i}',rng.randrange(-4,5)) for i in range(n)]
    expected = [x for _,x in sorted(enumerate(xs),key=lambda pair:(pair[1][1],pair[0]))]
    add((xs,),expected)
'''),
    65: ('recursive_structure', 'Input is a possibly empty nested list whose leaves are integers.', '''
for n in range(36):
    xs = [rng.randrange(-20,21) for _ in range(n)]
    nested = [xs[:n//3], [xs[n//3:2*n//3], []], xs[2*n//3:]]
    add((nested,),sum(xs))
'''),
    67: ('combinatorics', 'Return Bell number B(n), the number of partitions of an n-element set; 0 <= n <= 56.', '''
bell = [1]
for n in range(20):
    if n:
        bell.append(sum(math.comb(n-1,k)*bell[k] for k in range(n)))
    add((n,),bell[n])
'''),
    68: ('monotonicity', 'Nondecreasing or nonincreasing; ties, empty and singleton lists are valid.', '''
for xs in [[],[1],[1,1],[2,1,2]] + [rng.choices(range(-3,4),k=n) for n in range(30)]:
    ordered = sorted(xs)
    add((xs,), xs == ordered or xs == ordered[::-1])
'''),
    106: ('tuple_extension', 'Append the list to the tuple in that order; return a tuple. Empty inputs valid.', """
for n in range(36):
    xs=rng.choices(range(-10,11),k=n%9)
    ts=tuple(rng.choices(range(-10,11),k=n%7))
    add((xs,ts),(*ts,*xs))
"""),
    72: ('number_theory', 'n is a nonnegative integer. Squares may include zero.', '''
representable = {a*a-b*b for a in range(70) for b in range(a+1)}
for n in range(100):
    add((n,), n in representable)
'''),
    74: ('bijection', 'Two string lists follow the same pattern iff equal symbols correspond bijectively; lengths must match. Empty lists valid.', '''
for n in range(36):
    a = rng.choices(['red','blue','green'],k=n%9)
    b = rng.choices(['a','b','c'],k=n%10)
    ok = len(a)==len(b) and all((a[i]==a[j]) == (b[i]==b[j]) for i in range(len(a)) for j in range(len(a)))
    add((a,b),ok)
add((['red','blue','red'],['x','y','x']),True)
'''),
    75: ('filtering', 'K is a nonzero integer. Empty tuples pass the all-elements condition; preserve input order.', '''
for n in range(36):
    xs = [tuple(rng.choices(range(-10,11),k=i%5)) for i in range(n%12)]
    k = rng.choice([-5,-2,1,2,3,5])
    add((xs,k),[t for t in xs if not any(x%k for x in t)])
'''),
    77: ('divisibility', 'Integer n, including zero and negative integers.', '''
for n in list(range(-22,23)) + [rng.randrange(-100000,100001) for _ in range(20)]:
    add((n,), n == (n//11)*11)
'''),
    80: ('number_sequence', 'n is a nonnegative integer; numeric return type int or float is acceptable.', '''
for n in range(36):
    add((n,),sum(k*(k+1)//2 for k in range(n+1)))
'''),
    84: ('dynamic_programming', 'Newman-Conway P(1)=P(2)=1 and P(n)=P(P(n-1))+P(n-P(n-1)); 1 <= n <= 16.', '''
v = [0,1,1]
for n in range(3,17):
    v.append(v[v[n-1]]+v[n-v[n-1]])
for n in range(1,17): add((n,),v[n])
'''),
    86: ('number_sequence', 'n is a positive integer.', '''
v = 1
for n in range(1,36):
    if n>1: v += 6*(n-1)
    add((n,),v)
'''),
    87: ('dictionary_merge', 'Conflicts use the LEFTMOST dictionary: dict1 wins over dict2, which wins over dict3. Empty dicts valid.', '''
for n in range(36):
    ds = [{k:rng.randrange(-10,11) for k in rng.sample(list('abcde'),rng.randrange(6))} for _ in range(3)]
    expected = {}
    for d in ds:
        for k,v in d.items():
            if k not in expected: expected[k]=v
    add(tuple(ds),expected)
'''),
    88: ('frequency', 'Integer list, including empty; return a mapping comparable to dict.', '''
for n in range(36):
    xs = rng.choices(range(-5,6),k=n)
    add((xs,),{x:xs.count(x) for x in set(xs)})
'''),
    90: ('string_reduction', 'Nonempty list of strings; individual strings may be empty.', '''
for n in range(1,37):
    xs = ['x'*rng.randrange(20) for _ in range(n)]
    add((xs,),len(sorted(xs,key=len)[-1]))
'''),
    91: ('substring_search', 'List of strings and literal substring; matching is case sensitive; empty list/substrings valid.', '''
for n in range(36):
    xs = [''.join(rng.choices('abA',k=rng.randrange(8))) for _ in range(n%10)]
    sub = ''.join(rng.choices('abA',k=n%4))
    add((xs,sub),any(s.find(sub)>=0 for s in xs))
'''),
    92: ('digit_pattern', 'n is a nonnegative integer. Its decimal representation must have length >=3 and period two; repeated equal digits also pass.', '''
for n in [0,1,11,111,121,123,1212,90909] + [rng.randrange(100,1000000) for _ in range(30)]:
    s=str(n)
    add((n,),len(s)>2 and s == (s[:2]*((len(s)+1)//2))[:len(s)])
'''),
    93: ('exponentiation', 'a is an integer; b is an integer 0..12. Define 0**0 as 1.', '''
for a,b in itertools.product(range(-4,5),range(8)):
    expected=1
    for _ in range(b):expected*=a
    add((a,b),expected)
'''),
    94: ('argmin', 'Nonempty list of (name, numeric value) tuples; ties use first input occurrence.', '''
for n in range(1,37):
    xs=[(f'name{i}',rng.randrange(-5,6)) for i in range(n)]
    target=min(v for _,v in xs)
    add((xs,),next(k for k,v in xs if v==target))
'''),
    95: ('nested_reduction', 'Nonempty outer list of possibly empty lists; return shortest inner length.', '''
for n in range(1,37):
    xs=[list(range(rng.randrange(20))) for _ in range(n)]
    add((xs,),sorted(map(len,xs))[0])
'''),
    96: ('divisor_count', 'n is a positive integer, tested up to 100.', '''
for n in range(1,51):
    factors={1,n}
    for d in range(2,math.isqrt(n)+1):
        if n%d==0:factors.update([d,n//d])
    add((n,),len(factors))
'''),
    97: ('nested_frequency', 'Possibly empty list of possibly empty integer lists.', '''
for n in range(36):
    xs=[rng.choices(range(-3,4),k=rng.randrange(8)) for _ in range(n%12)]
    flat=sum(xs,[])
    add((xs,),{x:flat.count(x) for x in set(flat)})
'''),
    99: ('base_conversion', 'n is a nonnegative integer; zero is represented as "0".', '''
for n in [0,1,2,255,256,1024]+[rng.randrange(2**24) for _ in range(30)]:
    x=n;digits=[]
    while x:digits.append(str(x%2));x//=2
    add((n,),''.join(reversed(digits)) or '0')
'''),
    102: ('case_conversion', 'Return PascalCase: split on underscores, capitalize each nonempty component (rest lowercase); empty components each remain one underscore.', '''
for text in ['','_','a_b','AA_bb','_ab','ab_','a__b']+['_'.join(''.join(rng.choices('aBZ',k=rng.randrange(5))) for _ in range(4)) for _ in range(30)]:
    expected=''.join(part[:1].upper()+part[1:].lower() if part else '_' for part in text.split('_'))
    add((text,),expected)
'''),
    103: ('combinatorics', 'Eulerian A(n,m) counts permutations of 1..n with m descents; A(0,m)=0, out-of-range m gives 0.', '''
for n in range(1,7):
    counts={m:0 for m in range(n+1)}
    for p in itertools.permutations(range(n)):
        counts[sum(a>b for a,b in zip(p,p[1:]))]+=1
    for m in range(n+1):add((n,m),counts[m])
add((0,0),0)
'''),
    105: ('boolean_count', 'List contains only booleans; empty list valid.', '''
for n in range(36):
    xs=rng.choices([True,False],k=n)
    add((xs,),len([v for v in xs if v is True]))
'''),
    108: ('multiway_merge', 'Three integer lists may be unsorted or empty; preserve duplicates in ascending output.', '''
for n in range(36):
    ds=[rng.choices(range(-10,11),k=(n+j)%12) for j in range(3)]
    add(tuple(ds),sorted(sum(ds,[])))
'''),
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def module(checks):
    return 'import math\nfrom solution import ' + checks[0] + ' as candidate\n\n' + '\n\n'.join(
        f'def test_case_{i:03d}():\n    {c}' for i,c in enumerate(checks[1:])) + '\n'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'.artifacts/d2-diversity-v3')
    args=parser.parse_args()
    home=args.output.resolve()
    home.mkdir(parents=True,exist_ok=False)
    source=ROOT/'.artifacts/d2-next/source/sanitized-mbpp.json'
    if sha(source)!='ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9':raise ValueError('source hash mismatch')
    (home/'source').mkdir()
    shutil.copy2(source,home/'source'/source.name)
    rows={r['task_id']:r for r in json.loads(source.read_text())}
    old={r['instance_id'] for r in json.loads((ROOT/'.artifacts/d2-next/tasks.json').read_text())['tasks']}
    assert len(ORACLES)==34 and all(f'mbpp-{i:04d}' not in old for i in ORACLES)
    records=[]
    for tid,(family,domain,body) in ORACLES.items():
        row=rows[tid]
        fns=[n for n in ast.parse(row['code']).body if isinstance(n,ast.FunctionDef)]
        if len(fns)!=1 or row['test_imports']:raise ValueError('unexpected public task shape')
        fn=fns[0];signature=f'def {fn.name}({ast.unparse(fn.args)}):'
        extras=[]
        def add(args,expected):
            call='candidate('+','.join(repr(a) for a in args)+')'
            extras.append(f'assert {call} == {expected!r}')
        import itertools,math,random
        exec(body,{'add':add,'rng':random.Random(23000+tid),'itertools':itertools,'math':math})
        public=[re.sub(r'\b'+re.escape(fn.name)+r'\b','candidate',c) for c in row['test_list']]
        reference={}
        exec(row['code'],reference)
        reference['candidate']=reference[fn.name]
        reference['math']=math
        for check in public+extras:exec(check,reference)
        visible=module([fn.name,public[0]])
        workload=module([fn.name,*extras[:6]])
        hidden=module([fn.name,*public[1:],*extras[6:]])
        name=f'mbpp-{tid:04d}'
        evaluation=home/'evaluation'/name;evaluation.mkdir(parents=True)
        (evaluation/'test_independent.py').write_text(hidden)
        (evaluation/'reference.py').write_text(row['code'])
        statement=row['prompt']+'\nExplicit input/output contract: '+domain+(
            '\nImplement solution.py without changing its API. Read README.md, solution.py, test_visible.py and test_workload.py. '
            "Write your complete module noninteractively using cat <<'PY' > solution.py; do not use sed -i. "
            'Run exactly `python -m pytest -q test_visible.py test_workload.py` in a separate action before submitting. '
            'Only solution.py may be edited. No network, installs, parent directories or reference files. '
            'Submit with exactly `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.')
        work=home/'workspaces/greedy-guided'/name;work.mkdir(parents=True)
        files={'solution.py':signature+'\n    raise NotImplementedError("Implement the task")\n',
               'README.md':statement+'\n\nImplement '+signature+'\n','test_visible.py':visible,
               'test_workload.py':workload,'.gitignore':'__pycache__/\n.pytest_cache/\n'}
        for filename,text in files.items():(work/filename).write_text(text)
        subprocess.run(['git','init','-q',str(work)],check=True)
        subprocess.run(['git','-C',str(work),'add','.'],check=True)
        subprocess.run(['git','-C',str(work),'-c','user.name=LeasePath Pilot','-c','user.email=leasepath@localhost','commit','-qm','Frozen new MBPP parent'],check=True)
        records.append({'instance_id':name,'task_id':tid,'parent_task_id':name,'group_id':'google-research/mbpp:'+name,
                        'source_group':'google-research/mbpp','problem_statement':statement,'function_signature':signature,
                        'task_family':family,'evidence_scope':'mbpp_diversity_v3_agent_development',
                        'split':'pilot_development_only','workload_family':family,'work_units':6,'work_unit':'test_cases',
                        'visible_tests':7,'independent_tests':len(public)-1+len(extras)-6,
                        'generated_checks':len(extras),'workspaces':{'greedy-guided':str(work)},
                        'visible_test_sha256':sha(work/'test_visible.py'),'independent_test_sha256':sha(evaluation/'test_independent.py'),
                        'protected_sha256':{f:sha(work/f) for f in files if f!='solution.py'},
                        'reference_sha256':sha(evaluation/'reference.py'),'reference_validated':True})
    manifest={'dataset':'MBPP-diversity-v3','revision':'f82046ba5aabbbb427dbfd38a254d26bff08b533',
              'source_sha256':sha(Path(__file__)),'public_source_sha256':sha(source),
              'selection':'34 IDs and independent deterministic cases frozen before agent outcomes; no overlap with prior10',
              'limits':['public benchmark-derived development data; possible pretraining exposure','single execution per new parent','not official MBPP pass@1'],
              'tasks':records}
    (home/'tasks.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (home/'calibration.json').write_text(json.dumps({'reference_checks_passed':True,'tasks':34,'generated_checks':sum(t['generated_checks'] for t in records),'runtime_calibration':False},indent=2)+'\n')
    print(json.dumps({'prepared':len(records),'generated_checks':sum(t['generated_checks'] for t in records)}))


if __name__=='__main__':main()
