"""Add audited workload characterization to v2 without modifying v2 or evidence."""
from pathlib import Path
import json,re,hashlib
from short_captions import apply_captions, retain_measurement_details
from generate_problem_setting_figure import revise_problem_figure
ROOT=Path(__file__).resolve().parents[2];DOC=ROOT/'docs';A=Path(__file__).resolve().parent
R=ROOT/'results/analysis/agent_workload_v1_2026-11-11';m=json.loads((R/'summary.json').read_text())
base=DOC/'leasepath_isca2027_paper_v2_2026-11-11.tex';s=base.read_text()
names={'inventory':'Inventory library','billing':'CSV/SQLite billing','logs':'JSONL diagnostics','api':'HTTP integration','experiment':'Numerical experiment','documents':'Release documents'}
lines=[r'\begin{table*}[t]',r'\centering\small',
 r'\caption{Accepted continuous-agent development collection. Requirements and fixtures are authored; tool calls and model outputs are real. Every project has eight user turns. A model request is one LLM invocation, so it is a different unit from a user turn. The six project groups share authorship and are not external holdout sources.}',r'\label{tab:workload-projects}',r'\begin{tabular}{@{}lrrrrrr@{}}',r'\toprule',
 r'Project & User turns & Model requests & Tool calls & Tool types & First input & Last input \\',r'\midrule']
for p in m['projects']:
 name=names[p['owner'].removeprefix('continuous_')]
 lines.append(name+' & '+' & '.join(f"{p[k]:,}" for k in ['user_turns','model_requests','tools','tool_types','first_input_tokens','last_input_tokens'])+r' \\')
lines+=[r'\midrule',r'Total & 48 & 244 & 196 & 9 distinct & --- & --- \\',r'\bottomrule',r'\end{tabular}',r'\end{table*}']
(A/'generated/workload_projects.tex').write_text('\n'.join(lines)+'\n')
lines=[r'\begin{table*}[t]',r'\centering\small',
 r'\caption{Trace provenance and measurement scope. Existing collections are not pooled. Public CATraces counts refer to our pinned-release audit, not a reproduction of the paper-wide token totals. Real tool execution does not make authored requirements natural human interactions.}',
 r'\label{tab:workload-provenance}',r'\begin{tabular}{@{}p{.20\textwidth}p{.23\textwidth}p{.24\textwidth}p{.25\textwidth}@{}}',r'\toprule',
 r'Data & Size and origin & Observable evidence & Use and limit \\',r'\midrule',
 r'Earlier task collection & 50 authored parent tasks; 150 episodes, 347 snapshots & Real model/tools and native lookup & Internal 40/10 split; short tasks, already inspected \\',
 r'Continuous development & 6 authored projects; 48 user turns, 244 requests & Real feedback, native prefix/lookup, queue and delivery & Target-domain training and known-project E2E; not a natural user trace \\',
 r'Public CATraces release & 11 anonymized projects; 94 session files, 81 nonempty & Tool identities and log timestamps; content/arguments redacted & External workload reference; no exact compatible-prefix/lookup labels \\',
 r'\bottomrule',r'\end{tabular}',r'\end{table*}']
(A/'generated/workload_provenance.tex').write_text('\n'.join(lines)+'\n')
# Label inherited tables before adding new earlier floats.
old_labels=iter(['tab:old-evidence','tab:old-prediction','tab:old-e2e','tab:old-overhead'])
pattern=r'(\\begin\{table\*\}.*?)(\\end\{table\*\})'
# The inherited longtable macro contains a syntactic table* pair but no caption.
def label_old(match):
 text=match.group(1)
 if '\\caption{' not in text:return match.group(0)
 return text+'\\label{'+next(old_labels)+'}\n'+match.group(2)
s=re.sub(pattern,label_old,s,flags=re.S)
s=s.replace('Table 1 separates',r'Table~\ref{tab:old-evidence} separates')
s=s.replace('Table 2 shows',r'Table~\ref{tab:old-prediction} shows')
s=s.replace('Table 3 reports',r'Table~\ref{tab:old-e2e} reports')
s=s.replace('(Figure 4)',r'(Figure~\ref{fig:paired-cycles})')
s=s.replace('as defined in Section 3.3',r'as defined in Section~\ref{evaluation-objective}')
s=s.replace(r'\documentclass[sigconf, screen, review]{acmart}',r'\documentclass[sigconf, screen, review, balance=false]{acmart}')
for name in ['continuous_e2e_table.tex','continuous_overhead_table.tex','continuous_prediction_table.tex']:
 text=(DOC/'leasepath_isca_v2_assets/generated'/name).read_text().replace('Table 2',r'Table~\ref{tab:old-prediction}')
 (A/'generated'/name).write_text(text)
 s=s.replace('leasepath_isca_v2_assets/generated/'+name,'leasepath_isca_v3_assets/generated/'+name)
new=r'''
\section{Agent Workload Characterization}\label{workload-characterization}

\subsection{Provenance and measurement units}

CacheWise characterizes real coding-assistant sessions to motivate
long-lived, tool-driven KV reuse (Tiwari et al., 2026, Section 3).
We characterize our own workload to establish which of those properties
it covers, and which it does not. This section uses only the accepted
six-project collection, before the separate five-policy E2E comparison;
its timings are not additional policy repetitions. The collection uses
six concurrent sessions, serial FCFS model service, a 32K native context,
and 192 persistent blocks. The later E2E experiment uses 150 blocks.

\input{leasepath_isca_v3_assets/generated/trace_examples_section.tex}

\input{leasepath_isca_v3_assets/generated/workload_provenance.tex}
\input{leasepath_isca_v3_assets/generated/workload_projects.tex}

Table~\ref{tab:workload-provenance} separates the earlier authored
tasks, continuous projects, and a public trace reference. Our user
requirements, fixtures, and user-return schedule are authored, while
model outputs and tool executions are real. Consequently this is a
controlled continuous-agent workload, not a natural human usage sample.
A project is a persistent conversation; a user turn is one revealed
requirement through validated delivery; a model request is one native
LLM invocation. The 48 user turns produce 244 model requests and 196
tool calls (Table~\ref{tab:workload-projects}). All counts below retain
these distinct units.

Linked logs retain model inputs, native KV measurements, tool
arguments/results, and labeled snapshots. Figure~\ref{fig:trace-examples}
shows each project's first use of one selected tool, all in user turn 1.
Execution times come from start/end timestamps, rounded to 0.001 ms,
and differ from remaining-demand labels.

\subsection{Growing contexts create substantial reuse opportunity}

Figure~\ref{fig:workload-context} shows full input and generated-output
lengths, input growth through each project, and verified compatible
prefix fractions. Full input length includes reusable history; it is
not the number of tokens actually computed after a cache
lookup. Median input is 3,989.5 tokens (P95 13,929.9, maximum 18,196),
whereas median generated output is 39 tokens (P95 296.3). The collection
contains 1,227,620 input-token occurrences and 19,850 generated tokens,
a ratio of 61.84. This describes the workload's repeated-input
opportunity, not a measured compute-speedup ratio.

\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/workload_context.pdf}
\caption{Observed context structure in the accepted collection. Left:
request-weighted empirical CDFs of full input and generated output
(244 requests). Center: each of the six project histories, without
smoothing or extrapolation. Right: verified block-aligned prior-input
prefix as a fraction of current input; six first requests have zero.
These curves describe correlated development requests, without
independent-sample confidence intervals.}
\Description{Three panels show much longer inputs than outputs,
increasing input histories in six projects, and a high compatible
prior-input fraction for continuation requests.}
\label{fig:workload-context}
\end{figure*}

For each continuation, we compare actual native token IDs with the
previous same-project input. The entire previous input's managed
128-token-aligned prefix is compatible in all 238 continuations.
Summed over requests, those prefixes cover 1,157,760 tokens, or 94.31\%
of total input occurrences. This is a verified opportunity independent
of whether the cache retained it. It excludes generated suffix reuse
and cross-owner sharing, and is not the achieved cache-hit rate.
Input lengths grow from 808--881 tokens initially to 5,285--18,196 at
project end. This makes retention valuable but also creates growing
capacity demand; it does not show that all histories should remain
resident indefinitely.

\subsection{Real tool loops, with authored user arrivals}

Each project makes 33--50 model requests, with a median of five requests
and four tool calls per user turn. Exactly 196 requests (80.33\%) follow
consumed tool results and 48 follow user arrivals. Public checks return
four rejected or nonzero results, which the model consumes before
subsequent accepted deliveries. These observations establish actual
feedback-driven iteration in this collection, without requiring a
fixed tool path. They do not demonstrate broad branch diversity or a
natural distribution of human interventions.

First-to-last lookup spans 40.10--57.36 minutes across projects
(median 51.88). These durations include contention on this serial
backend and fixed user-return intervals; they are not measurements of
natural developer session lengths. Additional per-project counts and
session CDFs appear in Appendix~\ref{app:workload-loops}.

\subsection{Observed delays expose a queue-heavy coverage limit}

\input{leasepath_isca_v3_assets/generated/request_duration_section.tex}

\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/workload_timing.pdf}
\caption{Timing structure of the collection. Left: separate empirical
CDFs for measured tool calls (196), scheduled user waits (42), and
readiness-to-native-lookup delays (244); these have different observation
units. Center: next-demand residuals at the 1,079 actual decision
snapshots, separated by controller-observed phase. Right: measured
call durations by tool type. The dotted 30-second line is the frozen
scoring horizon, not a truncation of observed labels.}
\Description{Measured tools finish in less than a second, scheduled
user waits take seconds, and ready requests often wait tens of seconds.
Snapshot residuals vary by controller-observed phase. Tool-specific
curves illustrate narrow, predominantly short execution coverage.}
\label{fig:workload-timing}
\end{figure*}

Figure~\ref{fig:workload-timing} separates measured tool execution from
readiness, client/controller delay, and backend demand. Tools take
13.1 ms at the median, 318.0 ms at P95, and at most 366.5 ms. Their
21.051-second total belongs to collection, not the approximately
20-second totals in each later E2E arm. Scheduled user waits have a
4.001-second median and an 11.006-second maximum. Readiness to native
lookup has a 48.225-second median and 151.373-second P95. This interval
includes notification processing, FCFS waiting, prompt preparation,
and admission; it is not a pure model-queue measurement.

For the 238 complete release-to-next-lookup intervals, the median is
51.215 seconds and P95 is 152.644 seconds. Decision snapshots ask a
different question: the remaining time after some waiting has already
elapsed. Their median residual is 28.350 seconds; 52.83\% are followed
by demand within 30 seconds, versus 27.73\% of complete intervals.
Equal total weight per observed episode changes the snapshot fraction
to 54.08\%. Snapshot and interval distributions cannot be pooled.

Of the decision snapshots, 836 (77.48\%) are queued, 191 (17.70\%)
are controller-observed waiting for tools, and 52 (4.82\%) wait for a
user. Tool notifications are consumed between model calls, so observed
phase need not mean a tool is still physically executing. The lag from
actual tool-result notification time to controller consumption is
9.214 seconds at the median and 37.398 seconds at P95; this is an
orchestration limitation of the serial prototype, not tool execution.
All labeled
snapshot outcomes are demand; these data do not validate retirement
or censoring prediction. The 30-second score deliberately leaves
later mass unresolved and does not renormalize it into that window.
This coverage motivates causal state and lookup-time prediction while
also limiting the present claim: long tool executions, natural human
returns, and lifecycle diversity need new data. Tool-category
separation in these short calls does not establish predictive accuracy
or generalization.
'''
s=s.replace(r'\section{Problem Formulation}',new+'\n\n'+r'\FloatBarrier'+'\n'+r'\section{Problem Formulation}',1)
s=s.replace(r'\usepackage{microtype}',r'\usepackage{microtype}'+'\n'
 +r'% Keep punctuation and hyphens within the visible column edges.'+'\n'
 +r'\microtypesetup{protrusion=false}'+'\n'+r'\usepackage{placeins}',1)
diag=m['same_state_diagnostic'];case=diag['first_case'];wd=case['distribution_victim'];pt=case['window_victim']
score=r'''
\subsection{Logged score information can change a legal choice}
\label{score-disagreement}

We additionally compare the first eviction sweep at each admission of
the static-GBDT distribution arm. This read-only diagnostic uses the
same already deployed model outputs, legal leaves, maximum-owner rule,
and tie-break fields. Both scores must be available and distribution
fallback absent. Among 135 admissions with at least two comparable
leaves, replacing discounted mass by \(F_U(3\,\mathrm{s})\) changes the
first victim in 11 (8.15\%). Across repeated candidate pairs, there
are 22 strict ordering reversals among 526 pairs with strict values for
both scores, and 14 window ties distinguished by the distribution.
Pairs and admissions are correlated; these counts are not independent
samples or confidence estimates.
Of the 11 differing choices, the distribution victim's observed next
demand is earlier in six cases and later in five; this is a local
ordering diagnostic, not a completion-time comparison.

\begin{figure*}[t]
\centering
\includegraphics[width=.83\textwidth]{leasepath_isca_v3_assets/figures/workload_score_case.pdf}
\caption{First chronological differing choice in the static-GBDT arm,
selected without filtering on a favorable outcome. Left: the two
owners' logged cumulative demand predictions. Right: their actual next
compatible lookup times along the observed distribution-policy run.
The distribution evicts logs, demanded after 37.47 seconds; the window
would evict API, demanded after 78.81 seconds. This negative local
example demonstrates a real decision difference, not an extra
end-to-end gain.}
\Description{Two logged cumulative demand curves lead to different
first legal victims. In the observed run, the distribution victim has
an earlier next demand than the window's counterfactual victim.}
\label{fig:score-case}
\end{figure*}

The first chronological disagreement
(Figure~\ref{fig:score-case}) is a cautionary example: distribution
evicts the logs owner's leaf, whose next demand occurs in 37.47 seconds;
the window would evict the API owner's leaf, demanded in 78.81 seconds.
An earlier demand is not proof of an optimal action, but it prevents
presenting every score difference as helpful. The diagnostic changes
one action at the same state; it neither reconstructs later alternate
cache states nor predicts the alternate run's arrivals or response.
The observed near tie between complete GBDT policy arms remains the
relevant end-to-end result.
'''
marker=r'\subsection{Independent evaluation and policy selection remain open}'
s=s.replace(marker,score+'\n\n'+marker,1)
appendix=r'''
\clearpage
\onecolumn
\normalsize
\appendix
\section{Additional Workload Distributions}\label{app:workload-loops}

\begin{figure}[h]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/workload_loops.pdf}
\caption{Additional collection statistics. Left: model requests and
tool calls in each project, all with eight user turns. Center:
request/call counts per user turn, 48 correlated observations. Right:
first-to-last native lookup span in six projects, including serial
service contention and authored user-return delays. No curve is a
natural human usage distribution or a policy repetition.}
\Description{Project-level bars show 33 to 50 model requests and 25 to
42 tool calls. User-turn CDFs summarize model and tool iterations, and
a six-project CDF shows observed session spans.}
\label{fig:workload-loops}
\end{figure}

'''
s=s.replace(r'\end{document}',appendix+r'\end{document}')
s=s.replace('% Updated research draft, 2026-11-11; generated from the user-uploaded ACM source.','% Workload characterization update v3, 2026-11-11; v2 is preserved.')
s=s.replace('% Evidence and build provenance: leasepath_isca_v2_assets/manifest.json.','% Evidence and build provenance: leasepath_isca_v3_assets/manifest.json.')
s=revise_problem_figure(s)
method_heading=r'\section{LeasePath}\label{leasepath}'
assert s.count(method_heading)==1
s=s.replace(method_heading,method_heading+'\n\n'
 +r'\input{leasepath_isca_v3_assets/generated/method_overview_intro.tex}'+'\n'
 +r'\input{leasepath_isca_v3_assets/generated/method_overview.tex}',1)
# Leave space for text beneath the dense preliminary-results float group.
s=re.sub(r'(\\includegraphics\[width=)\\textwidth(?=[^\n]*figure2_preliminary_results\.pdf)',
 lambda match:match.group(1)+r'.98\textwidth',s,count=1)
s=retain_measurement_details(apply_captions(s))
from generate_method_overview import revise_training_description, keep_block_score_together
s=revise_training_description(s)
s=keep_block_score_together(s)
for path in (A/'generated').glob('*.tex'):
 path.write_text(apply_captions(path.read_text()))
out=DOC/'leasepath_isca2027_paper_v3_2026-11-11.tex';out.write_text(s)
files=[base,out,R/'summary.json',R/'request_duration_summary.json',Path(__file__).resolve(),A/'analyze_workload.py',A/'extract_characterization.py',A/'short_captions.py',A/'generate_request_duration_figures.py',A/'generate_problem_setting_figure.py',A/'check_paper_layout.py',R/'collection-source-sha256.json',R/'static-source-sha256.json']
files+=list((A/'generated').glob('*.tex'))+list((A/'figures').glob('*.pdf'))+list(R.glob('*.csv'))
files+=[A/'generate_trace_examples.py',DOC/'leasepath_dataset_examples_2026-11-12.md']+list((A/'examples').glob('*'))
files+=[A/'generate_method_overview.py']
files+=[DOC/'leasepath_gbdt_training_contract_2026-11-12.md',
 A/'design_references/README.md',A/'icons/source_manifest.json']
files+=list((A/'icons/lucide').glob('*'))
manifest={'scope':m['scope'],'source':str(base.relative_to(ROOT)),'inference_calls':0,'holdout_access':False,
 'raw_sources':{**json.loads((R/'collection-source-sha256.json').read_text()),**json.loads((R/'static-source-sha256.json').read_text())},
 'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(A/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(out)
