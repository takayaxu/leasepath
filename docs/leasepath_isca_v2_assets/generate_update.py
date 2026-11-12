"""Rebuild paper v2 from the uploaded manuscript and audited development metrics.
Run with python -B docs/leasepath_isca_v2_assets/generate_update.py from repo root.
No inference, experimental configuration, or raw evidence is changed.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs'
ASSET = Path(__file__).resolve().parent
BASE = ASSET / 'source/uploaded_manuscript_v1.tex'
EVID = ROOT / 'results/agent/continuous_workflow_v6_2026-11-11'
COLLECT = ROOT / 'results/agent/continuous_pilot_v7_2026-11-11/audit/summary.json'
TRAIN = ROOT / 'results/agent/continuous_workflow_v5_2026-11-11/prediction/summary.json'
OUT = DOC / 'leasepath_isca2027_paper_v2_2026-11-11.tex'
M = json.loads((EVID / 'verified-e2e-metrics.json').read_text())
C = json.loads(COLLECT.read_text())
T = json.loads(TRAIN.read_text())
U = json.loads((EVID / 'online-cutoff-replay.json').read_text())
assert M['status'] == C['status'] == U['status'] == 'passed'
assert M['all_per_owner_prompt_sequences_identical']
assert len(U['updates']) == 6
for row in U['updates']:
    assert row['source_reconstructed_from_observed_prefix'] and row['model_sha_verified'] and row['used_only_after_deployment']
assert len(M['arms']) == 5
for a in M['arms']:
    assert a['successful_rounds'] == a['planned_rounds'] == 48
    assert a['requests'] == 244 and a['input_tokens'] == 1227620
    assert a['cached_tokens'] + a['prefill_tokens'] == a['input_tokens']
    assert a['same_exact_per_owner_prompts_as_lru']
    audit = EVID / 'e2e' / a['arm'] / 'audit/summary.json'
    assert hashlib.sha256(audit.read_bytes()).hexdigest() == M['audit_sha256'][str(audit.relative_to(ROOT))]

labels = ['Retired-first LRU', 'Empirical, dynamic, distribution',
          'GBDT, static, distribution', 'GBDT, dynamic, fixed window',
          'GBDT, dynamic, distribution']
lines = [r'\begin{table*}[t]', r'\centering\small',
 r'\caption{Continuous-agent development comparison: six projects, eight user turns each, six concurrent sessions, 150 persistent blocks. Every arm passes 48/48 turns with 244 model requests and 1,227,620 input tokens. Totals are per arm; response is averaged per user turn. This is one ordered cycle, without confidence intervals.}',
 r'\label{tab:continuous-e2e}', r'\setlength{\tabcolsep}{4pt}',
 r'\begin{tabular}{@{}lrrrrrrr@{}}', r'\toprule',
 r'Policy & Cached tokens & Prefill tokens & Prefill (s) & Saved & Response (s) & Saved & Updates \\', r'\midrule']
for label, a in zip(labels, M['arms']):
    lines.append(f"{label} & {a['cached_tokens']:,} & {a['prefill_tokens']:,} & {a['prefill_s']:,.3f} & {a['prefill_reduction_pct']:.2f}\\% & {a['mean_response_s']:.3f} & {a['response_reduction_pct']:.2f}\\% & {a['updates']} " + r'\\')
lines += [r'\bottomrule', r'\end{tabular}', r'\end{table*}']
(ASSET / 'generated/continuous_e2e_table.tex').write_text('\n'.join(lines)+'\n')
lines = [r'\begin{table*}[t]', r'\centering\small',
 r'\caption{Measured timing categories for the continuous-agent comparison, in seconds per arm. Tools and background training overlap service; controller preparation includes prediction, so columns cannot be added to reconstruct a critical path. Model loading precedes the response window.}',
 r'\label{tab:continuous-overhead}', r'\setlength{\tabcolsep}{4pt}',
 r'\begin{tabular}{@{}lrrrrrrr@{}}',r'\toprule',
 r'Policy & Decode & Tools & Prediction & Preparation & Online fit & Load & Makespan \\', r'\midrule']
for label, a in zip(labels, M['arms']):
    lines.append(label + ' & ' + ' & '.join(f"{a[k]:,.3f}" for k in ['decode_s','tools_s','prediction_s','controller_prepare_s','training_wall_s','model_load_s','project_makespan_s']) + r' \\')
lines += [r'\bottomrule', r'\end{tabular}', r'\end{table*}']
(ASSET / 'generated/continuous_overhead_table.tex').write_text('\n'.join(lines)+'\n')
lines = [r'\begin{table}[t]',r'\centering\small',
 r'\caption{Chronological diagnostic on 274 later snapshots of the same six development projects, after fitting on 805 earlier snapshots. Binary demand Brier, lower is better; not the three-class metric of Table 2 and not unseen-project evaluation.}',
 r'\label{tab:continuous-prediction}',r'\begin{tabular}{@{}rrr@{}}',r'\toprule',r'Horizon (s) & Empirical & GBDT \\',r'\midrule']
for h in ['1','3','10','30']:
    vals = [T['validation'][k]['horizons'][h]['Brier_U'] for k in ['empirical','gbdt']]
    lines.append(h + ' & ' + ' & '.join(f'{v:.5f}' for v in vals) + r' \\')
lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}']
(ASSET / 'generated/continuous_prediction_table.tex').write_text('\n'.join(lines)+'\n')

plt.rcParams.update({'font.size': 9, 'pdf.fonttype':42, 'ps.fonttype':42})
fig, axes = plt.subplots(1, 3, figsize=(10.4, 2.9), layout='constrained')
short = ['LRU','Emp.\ndyn/dist','GBDT\nstatic/dist','GBDT\ndyn/3s','GBDT\ndyn/dist']
colors = ['#777777','#62a8ac','#8fa8ce','#d5ae63','#4c719c']
for ax, key, scale, title, ylabel in zip(axes,['cached_tokens','prefill_s','mean_response_s'],[1000,1,1],
        ['Actual KV reuse','Executed prefill','User-turn response'],['Tokens (thousands)','Seconds per arm','Mean seconds per turn']):
    vals=[a[key]/scale for a in M['arms']]
    bars=ax.bar(short,vals,color=colors,width=.68)
    ax.set_ylim(0,max(vals)*1.21)
    ax.bar_label(bars,labels=[f'{v:.1f}' for v in vals],padding=3,fontsize=8)
    ax.set_title(title); ax.set_ylabel(ylabel); ax.tick_params(axis='x',labelsize=8)
    ax.spines[['top','right']].set_visible(False)
fig.savefig(ASSET/'figures/figure5_continuous_e2e.pdf')
fig.savefig(ASSET/'figures/figure5_continuous_e2e.png',dpi=180)
plt.close(fig)
for name in ['figure1_distribution_example.pdf','figure2_preliminary_results.pdf','figure3_paired_cycles.pdf']:
    shutil.copy2(DOC/'leasepath_iclr_v1_assets/figures'/name,ASSET/'figures'/name)

s = BASE.read_text()
def replace(old,new):
    global s
    assert old in s, old[:90]
    s=s.replace(old,new,1)
def section(start,end,text):
    global s
    a=s.index(start); b=s.index(end,a)
    s=s[:a]+text.strip()+'\n\n'+s[b:]
s='% Updated research draft, 2026-11-11; generated from the user-uploaded ACM source.\n% Evidence and build provenance: leasepath_isca_v2_assets/manifest.json.\n'+s
s=s.replace('leasepath_iclr_v1_assets/figures/','leasepath_isca_v2_assets/figures/')
section(r'\begin{abstract}',r'\end{abstract}',r'''
\begin{abstract}
Tool-augmented large language model agents repeatedly return to growing
conversation prefixes after uncertain tool, client, and queue delays.
LeasePath predicts the time to the next legal key--value (KV) prefix
demand and maps its finite-horizon probability mass to a discounted
retention score. A pressure-triggered controller applies the score to
legal prefix leaves, protecting active references and accounting for
shared physical occupancy. We implement native CUDA KV eviction and
audit consumed tool feedback, actual prefill, and validated deliveries.
An initial three-task experiment supports equal-cost scoring over a
calibrated cost extension. In a subsequent continuous-agent development
comparison, six authored projects execute eight user turns each with
nine tool types and identical per-project prompt sequences across five
policies. Dynamic GBDT distribution scoring reuses 329,344 additional
tokens, reduces aggregate prefill time by 44.86\%, and reduces mean
user-turn response time by 19.76\% relative to retired-first LRU, with
48/48 accepted turns in each arm. Its two online updates use only
observed event prefixes. Static GBDT and fixed-window probability
scoring achieve similar response times; this single ordered cycle on a
shared server does not establish an advantage for dynamic updates or
for distribution scoring over that window. Independent projects,
balanced repetitions, drift, and complete-system baselines remain open.
''')
section('The present evidence is deliberately limited', 'This draft makes three contributions', r'''
The present evidence comprises a completed prototype and two development
experiments, reported separately. A three-task comparison holds the
predictor fixed to examine the scoring functional and a cost extension.
A subsequent continuous-agent comparison uses six growing project
histories, real tool feedback, and repeated user turns. Relative to
retired-first LRU, its four prediction policies reduce observed mean
user-turn response by 17.95\%--19.80\%. However, the differences among
static GBDT, dynamic GBDT, and fixed-window scoring are small; the
preferred predictor and update regime remain empirical choices. A
mixture is a candidate for future validation, not a tested result.
''')
replace('validated-answer completion time, alongside predictor comparisons and\n  a negative result for the cost extension.',
        'validated-answer completion time, alongside a continuous-agent\n  comparison with causally audited online updates and a negative result\n  for the cost extension on the earlier workload.')
replace('Our frozen predictor has no\nmeasured queue-state feature.', 'The earlier frozen predictor has no\nmeasured queue-state feature; the continuous-agent predictor adds\ncausally observed queue and execution state.')
replace('For the completed experiment,\nthe measured endpoint is precisely', 'For the earlier three-task experiment,\nthe measured endpoint is precisely')
replace("This submission timestamp is distinct from each model request's cache\nadmission time.", "This submission timestamp is distinct from each model request's cache\nadmission time. For continuous projects, the endpoint is instead\nuser-turn response: actual arrival of the current user message to an\nindependently validated delivery. It includes queueing, model service,\ntools, controller work, and validation, and excludes user think time\nbefore that arrival. Project makespan includes the think intervals.\nThese endpoints are not pooled across experiments.")
replace('are excluded. Prefix identity and length are used by cache management\nand validation; they are not learned features in the frozen model.',
        'are excluded. Prefix identity and length are used by cache management\nand validation; they are not learned features in the earlier frozen\nmodel. The continuous-agent version retains tool identity and adds\ncausal execution state and current prefix length, as detailed below.')
replace('weights sum to one per parent, with equal episode weights within a\nparent and equal snapshot weights within an episode.',
        'in the earlier predictor sum to one per parent, with equal episode\nweights within a parent and equal snapshot weights within an episode.\nThe continuous-agent version preserves these relative weights but\nnormalizes their mean to one; this changes their absolute scale relative\nto the fixed regularization constants.')
replace('At the\n30-second interval, the frozen training data provide only six workflows.',
        'At the\n30-second interval, both the earlier frozen training data and the\ninitial continuous-agent data provide only six workflows.')
replace("training-global fallback; an unsupported compatibility class yields an\nunsupported distribution.",
        "training-global fallback in the earlier predictor. The continuous-agent\nGBDT instead maps unseen tools to an unknown category and retains the\nstate features; deterministic training-only masking of approximately\none tenth of episodes trains that category. An unsupported compatibility\nclass yields an unsupported distribution.")
replace('No distribution\nfallback was observed in the completed natural experiment,',
        'No distribution\nfallback was observed in the earlier natural experiment,')
replace('The completed experiments address whether a different functional of the\nsame predictor changes actual reuse and measured completion time,\nwhether the frozen predictor improves on empirical estimates, and\nwhether a marginal-cost multiplier helps.',
        'The earlier experiments address whether a different functional of the\nsame predictor changes actual reuse and measured completion time,\nwhether the frozen predictor improves on empirical estimates, and\nwhether a marginal-cost multiplier helps. The continuous-agent\ncomparison additionally examines real multi-turn reuse and deployable\nonline updates; its single cycle is not an isolated adaptation test.')
replace('Table 1 separates the data used for these questions.', 'Table 1 separates the earlier data used for these questions.')
replace('The completed experiment uses 24', 'The earlier experiment uses 24')
replace('controller remains a required comparator.', 'controller is measured in the subsequent continuous-agent experiment.')
replace('Whether the\nstronger empirical estimator yields better online retention remains\nunmeasured.',
        'This comparison does not establish online policy ranking. The later\ncontinuous-agent experiment evaluates a newly fitted empirical policy\nand GBDT policies; its feature contract and data differ from this\nfrozen-predictor comparison.')
replace(r'\subsection{Conditional empirical prediction is stronger on current'+ '\n'+r'data}',r'\subsection{Earlier offline predictor comparison}')
replace('Table 3 reports the completed natural comparison.', 'Table 3 reports the earlier completed three-task comparison.')
replace('LRU also incurs shadow-prediction auditing in this experiment.', 'LRU also incurs shadow-prediction auditing in this earlier experiment.')

newsetup=r'''
\subsection{Continuous-agent projects and updated predictors}
\label{continuous-setup}

The subsequent workload contains six authored development projects:
a Python inventory library, CSV/SQLite billing, JSONL log analysis,
a real loopback HTTP API, a 12,000-row numerical experiment, and release
documentation. Each has eight sequential user requirements, with the
full conversation retained and only the current requirement revealed.
The model chooses tools, arguments, revisions, and stopping; it consumes
actual tool results. Nine tool types cover file listing, reading,
writing, search, checks, SQL, Python, HTTP, and numerical experiments.
For example, a public consistency check rejects mismatched regional
billing totals before the model repairs the artifact. These are
model-driven loops with fixed user requirements, rather than a fully
scripted tool path. They are not independently sourced benchmark tasks.

The accepted collection has 244 native requests, 196 consumed tool
results, 1,079 decision snapshots, and 234 observable idle episodes,
with no excluded snapshots and 48/48 validated user turns. Snapshot
phases are 191 waiting for tools, 836 queued, and 52 waiting for users.
All 1,079 labeled snapshot outcomes are demand (\(U\)); terminal
retirement events exist, but these snapshots do not supply observed
retirement or censoring labels. Earlier failed collections and startup
errors remain archived. The accepted success rate follows development
repairs, including protocol, capacity, runtime-integrity, and public
artifact-check fixes; it is not a first-attempt success rate.

Both estimators are refitted on this target-domain development data.
Empirical conditioning retains tool identity with phase, elapsed-time,
and queue bins. The GBDT adds elapsed idle/tool time and knownness,
readiness, queue length and knownness, current prefix length, retry
state, phase, and interval index; task identity and future information
remain excluded. Categorical tool identity remains an input, even
though its gain is zero in this narrow initial fit. This does not show
that tool identity is uninformative under heterogeneous long tools.
There are 40 boosting rounds, learning rate 0.05, four leaves, depth two,
minimum leaf size 20, and L2 penalty 10, without a tuning search.
State buckets extend through 60 seconds, unlike the earlier
one-second elapsed bucket ceiling. Tool argument size is not yet a
feature. A chronological diagnostic fits 805 earlier snapshots and
scores 274 later snapshots of the same projects; the initial service
models are subsequently fitted on all 1,079 development snapshots.
The end-to-end arms rerun these same six project specifications after
this initial fit; consequently, they evaluate known development
histories, not adaptation to previously unseen projects.

Static means frozen model weights, with predictions still conditioned
on current state. Dynamic arms refit after 100 additional mature live
episodes spanning at least five projects. A 600-second polling rule
does not bypass this evidence gate. One background training thread uses
the initial observations plus the already observed live prefix; new
weights switch atomically at a request boundary without clearing KV.
Each of the three dynamic arms deploys two updates. All six training
source hashes are reconstructed from observed event prefixes, and
model hashes and use-after-deployment order pass the causal audit.

The model and native adapter are unchanged, but context grows to 32,768
tokens and persistent capacity to 150 blocks (19,200 tokens, 4.6875 GiB
payload), with 2,048 transient headroom tokens and six concurrent
sessions. Capacity follows a registered longest-request rule with an
eight-block allowance; histories are not truncated. Requests still
execute serially in FCFS order, while sessions and tools overlap.
The score remains equal cost, \(H=30\) seconds, \(\tau=10\) seconds,
maximum-owner aggregation, five-project risk support across the
horizon, pressure-only legal-leaf eviction, and retired-first LRU
fallback. There is no prefetch, offload, or request reordering.
The earlier baseline manifest is retained as score provenance, not as
an assertion that its predictor or retraining flag remains unchanged.

Five arms execute once in this order: retired-first LRU, dynamic
empirical distribution, static GBDT distribution, dynamic GBDT
\(F_U(3\,\mathrm{s})\), and dynamic GBDT distribution. LRU has zero
prediction calls and no shadow predictor in this comparison. Per-owner
prompt hashes and request sequences match exactly across all arms;
global interleaving changes naturally with service times. User think
intervals are fixed and shared, while actual tools are executed without
inserted timing sleeps. This is one ordered development cycle, not a
balanced repetition or a sealed test.
'''
s=s.replace(r'\section{Results and Analysis}',newsetup+'\n\n'+r'\section{Results and Analysis}',1)
section(r'\subsection{Independent evaluation remains',r'\section{Limitations and Responsible',r'''
\subsection{Continuous-agent reuse and end-to-end response}
\label{continuous-results}

\input{leasepath_isca_v2_assets/generated/continuous_e2e_table.tex}

Table~\ref{tab:continuous-e2e} reports the new comparison separately
from the earlier three-task batches. All five arms execute the same
1,227,620 input tokens and validate 48/48 user turns. Dynamic GBDT
distribution scoring reuses 762,880 tokens versus LRU's 433,536:
329,344 additional cached tokens exactly replace the same amount of
executed prefill. Aggregate prefill time falls from 1,602.567 to
883.732 seconds, saving 718.835 seconds (44.86\%). Mean user-turn
response falls from 394.295 to 316.368 seconds, saving 77.927 seconds
(19.76\%). The other prediction policies reduce response by
17.95\%--19.80\%. This establishes an observed end-to-end difference
under real KV competition in these development histories, rather than
an offline prediction result alone.

\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v2_assets/figures/figure5_continuous_e2e.pdf}
\caption{Continuous-agent development comparison. Each bar summarizes
one arm with 48 user turns across six authored projects. Axes start at
zero; aggregate prefill and average response have different units.
There are no repeated-cycle uncertainty estimates.}
\Description{Three bar charts show actual cached tokens, cumulative
prefill seconds, and mean user-turn response for the five policies. All
prediction arms improve these observed metrics over LRU; the three GBDT
arms are close.}
\label{fig:continuous-results}
\end{figure*}

The close GBDT results leave policy selection open. Dynamic distribution
reuses only 4,096 more tokens than static distribution and lowers mean
response by about 0.20\%. Relative to dynamic fixed-window scoring, it
reuses 896 more tokens but has about 0.04\% higher response. The
fixed-window arm is the cumulative probability \(F_U(3\,\mathrm{s})\),
not a prediction of a three-second return and not CacheWise. Neither
the extra value of online adaptation nor that of full distribution
scoring is established by these differences. A future empirical/GBDT
mixture requires its own causal training and ablation; it is not one of
the measured arms.

\subsection{Continuous-agent timing and prediction diagnostics}

\input{leasepath_isca_v2_assets/generated/continuous_overhead_table.tex}

Decode totals span 1,796.477--1,797.737 seconds and actual tool execution
20.198--20.688 seconds across arms. The large measured change is in
prefill, with downstream queueing affecting response. This breakdown
supports the prefill mechanism but does not identify every second of
the response difference. Tools and training overlap service, and
controller preparation includes prediction. Summing these accumulated
categories would misrepresent a critical path. Model loading spans
30.180--178.907 seconds outside the steady-response window, indicating
that host conditions are not constant.

Dynamic distribution uses 0.758 seconds of aggregate prediction,
27.758 seconds of controller preparation, and 1.041 seconds of
background fit wall time (0.886 thread CPU seconds). Boundary data
preparation is also within measured response; fit time is not the whole
update overhead. Initial full-development fitting of both models takes
0.688 seconds, excluding the earlier diagnostic fits and model loading,
so it is not total startup cost. Empirical online updates are cheaper,
but the current cycle alone cannot determine a predictive policy's
long-run tradeoff between overhead and retained prefill savings.

\input{leasepath_isca_v2_assets/generated/continuous_prediction_table.tex}

In the within-project chronological diagnostic
(Table~\ref{tab:continuous-prediction}), empirical prediction has lower
binary demand Brier at 1, 3, and 10 seconds, whereas GBDT is lower at
30 seconds. These are not directly comparable to the earlier
three-class scores: the data, features, weighting scale, split, and
metric differ. Better performance at one prediction horizon need not
yield better retention or response. Causal online prediction quality,
calibration, and drift response remain to be measured on independent
projects.

\subsection{Independent evaluation and policy selection remain open}

The six continuous projects exclude the old 50 parents, but were authored
and debugged during development. They do not establish source-independent
generalization. The six previously held-out source families remain
unused by these models; no independent main-result table is filled.
The older \(24/32/64\)-block pressure registration is a different
workload configuration, and its capacities cannot simply be reused for
these much longer untruncated requests.

The next controlled tests are balanced repeats, independent continuous
projects, a feasible capacity/concurrency matrix, and genuine changes
in tool duration and user return times. Predictor family, static versus
online training, and fixed-window versus distribution scoring need
separate contrasts, with a common frozen checkpoint for the scoring
contrast. Faithful CacheWise, TTL, and lifecycle-matched comparators
remain required for a complete-system claim. Predictor or hybrid choice
will use development evidence before a final test is unsealed.
''')
section(r'\section{Limitations and Responsible',r'\section{Conclusion}',r'''
\section{Limitations and Responsible Evaluation}
\label{limitations-and-responsible-evaluation}

The datasets are authored development workloads. The earlier 50-parent
collection has demand and retirement but no natural censoring; the new
continuous-agent snapshots all end in demand. Although retirement and
censoring are supported in the event interface, this does not validate
competing-risk prediction under cancellation, context replacement, or
long unresolved human waits. More snapshots from the same histories do
not supply independent projects or missing event types. Development
repairs and earlier failures must remain visible in any release.

The continuous projects add genuine iteration and causal queue state,
but tools are short and serial model service creates substantial
waiting. Their results need not predict behavior under long external
computations, broad branch structures, or a different arrival process.
Both the old coarse predictor and the new target-data predictor have
limited independent support. The scoring horizon, discount,
maximum-owner rule, and leaf-greedy eviction are engineering choices,
without a global JCT optimality guarantee.

System evidence covers one model, one native adapter, three GPUs split
by layers, 16K or 32K context, and serial FCFS model service. It does not
establish production continuous-batching behavior. The new five-arm
comparison has one fixed order on a shared server; model loading alone
shows host variation. It supplies observed reductions, not a population
significance claim. Its 48 correlated turns per arm are not 48
independent workloads. Static, dynamic, and fixed-window GBDT policies
are too close here to select a final policy, and no hybrid has been
validated. Balanced repeats, matched quality, independent projects,
stronger comparators, and startup/overhead amortization are still needed.

Tool permission and resource boundaries are part of the workload
contract. Released traces require review of sensitive inputs and
redistribution permissions. Reproducibility must preserve failed
attempts and distinguish constructed examples, chronological prediction
diagnostics, and natural execution. Public tool logs without actual
compatible backend lookup events are not ground-truth demand data.
''')
section(r'\section{Conclusion}',r'\begin{acks}',r'''
\section{Conclusion}\label{conclusion}

LeasePath maps finite-horizon distributions of legal prefix demand to
feasible native KV retention actions. An initial fixed-predictor
experiment supports the equal-cost scoring configuration. A subsequent
six-project continuous-agent comparison shows 44.86\% less cumulative
prefill time and 19.76\% lower mean user-turn response for dynamic GBDT
distribution scoring than retired-first LRU, with identical per-owner
prompt sequences and accepted deliveries. Causal update audits show
that online fitting is deployable in the prototype. This development
cycle does not establish an advantage over static GBDT or fixed-window
probability scoring. Final predictor choice, possible mixtures,
independent generalization, and complete-system comparisons remain
open experimental questions.
''')
# Repair inherited captions that originally appeared outside their table floats.
s=s.replace('\\begin{longtable}[]', '\\begin{table*}[t]\n\\centering\\small\n\\begin{tabular}')
s=re.sub(r'\\end\{longtable\}\s*\\captionof\{table\}\{([^\n]*)\}',
         lambda m:'\\end{tabular}\n\\caption{'+m.group(1)+'}\n\\end{table*}',s)
assert '\\begin{longtable}[' not in s
# Break inherited display equations to fit an ACM column without scaling fonts.
s=s.replace(r'''S_0=1,\qquad p_k^U=S_{k-1}h_k^U,\qquad
p_k^D=S_{k-1}h_k^D,\qquad S_k=S_{k-1}h_k^0,''',r'''\begin{aligned}
S_0&=1,& p_k^U&=S_{k-1}h_k^U,\\
p_k^D&=S_{k-1}h_k^D,& S_k&=S_{k-1}h_k^0.
\end{aligned}''')
s=s.replace(r'''W_A=0.2e^{-0.1}+0.6e^{-1}\approx0.402,
\qquad W_B=0.2e^{-0.1}+0.6e^{-3}\approx0.211.''',r'''\begin{aligned}
W_A&=0.2e^{-0.1}+0.6e^{-1}\approx0.402,\\
W_B&=0.2e^{-0.1}+0.6e^{-3}\approx0.211.
\end{aligned}''')
s=s.replace(r'''\widehat C_{i,b}=\widehat T(P_i+1,\ell_i-128)
 -\widehat T(P_i+1,\ell_i),
\qquad
V_b^{\mathrm{cost}}=\frac{\max_{i\in\mathcal O_b}
 (\widehat C_{i,b}W_i)}{B_b}.''',r'''\begin{aligned}
\widehat C_{i,b}&=\widehat T(P_i+1,\ell_i-128)\\
 &\quad-\widehat T(P_i+1,\ell_i),\\
V_b^{\mathrm{cost}}&=\frac{\max_{i\in\mathcal O_b}
 (\widehat C_{i,b}W_i)}{B_b}.
\end{aligned}''')
s=s.replace(r'\usepackage{microtype}',r'\usepackage{microtype}'+'\n'+r'\setlength{\emergencystretch}{3em}')
descriptions={
    'fig:agent-loop':'A growing conversation alternates model calls and real tool execution. A predicted remaining-time distribution guides legal KV retention under pressure.',
    'fig:distribution-example':'Two illustrative demand distributions share the same three-second cumulative probability but have different discounted mass through thirty seconds.',
    'fig:preliminary-results':'The older three-task experiment compares cached tokens, prefill time and mean completion time across four policies.',
    'fig:paired-cycles':'Within-cycle completion-time differences for four repeated cycles of the same three development tasks.',
}
for key,description in descriptions.items():
    s=s.replace('\\label{'+key+'}', '\\Description{'+description+'}\n\\label{'+key+'}',1)
# Native Unicode maths in narrative need a Unicode text font under XeLaTeX.
s=s.replace('−',r'\ensuremath{-}').replace('×',r'\ensuremath{\times}')
OUT.write_text(s)
files=[BASE,EVID/'verified-e2e-metrics.json',EVID/'online-cutoff-replay.json',COLLECT,TRAIN,
       ROOT/'configs/baselines/kv_equal_cost_distribution_v1.json',
       DOC/'leasepath_iclr_outline_2026-11-10.md',
       ASSET/'figure-extraction.json',OUT,Path(__file__).resolve()]
files += sorted((ASSET/'generated').glob('*.tex'))+sorted((ASSET/'figures').glob('*.pdf'))
manifest={'manuscript':str(OUT.relative_to(ROOT)), 'source':'user-uploaded ACM TeX; local v1 untouched',
          'evidence_scope':M['scope'], 'final_policy_selected':False,'hybrid_evaluated':False,
          'sha256':{str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(ASSET/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(OUT)
print('Verified evidence; generated 3 tables, chart, manuscript, provenance.')
