"""Concise captions, with panel explanations for the problem and method figures."""
from pathlib import Path
import re
from generate_problem_setting_figure import CAPTION as PROBLEM_SETTING_CAPTION
from generate_method_overview import CAPTION as METHOD_OVERVIEW_CAPTION

CAPTIONS = {
    'fig:agent-loop': PROBLEM_SETTING_CAPTION,
    'fig:method-overview': METHOD_OVERVIEW_CAPTION,
    'fig:workload-context': 'Long, growing inputs create substantial prefix-reuse opportunities across six development projects (244 model requests).',
    'fig:workload-timing': 'Short tools and longer readiness-to-lookup delays characterize the current workload; residual demand times vary by phase.',
    'fig:request-durations': 'Tool execution is brief, while request intervals and latency span much longer timescales; markers show empirical P50/P99.',
    'fig:distribution-example': 'Illustrative distributions with equal three-second demand probability produce different discounted retention scores.',
    'fig:preliminary-results': 'Equal-cost distribution scoring improves reuse, prefill, and mean JCT in the three-task development comparison.',
    'fig:paired-cycles': 'Equal-cost distribution scoring lowers mean JCT versus LRU and fixed-window scoring in all four development cycles.',
    'fig:continuous-results': 'Prediction policies improve reuse and response versus LRU; the three GBDT configurations perform similarly (six development projects).',
    'fig:score-case': 'First observed scoring disagreement: the distribution policy evicts the sooner-returning prefix (37.47 versus 78.81 seconds).',
    'fig:workload-loops': 'Six development projects produce repeated tool-driven model calls; session spans include FCFS contention and scheduled user waits.',
    'fig:trace-examples': 'Selected real tool invocations from six development projects; arguments and measured execution times are shown.',
    'tab:workload-provenance': 'Data sources, measurement coverage, and evaluation roles; authored workloads and public traces are reported separately.',
    'tab:workload-projects': 'Six authored development projects: eight user turns each, 244 model requests and 196 real tool calls in total.',
    'tab:old-evidence': r'Earlier development data and evaluation splits: 50 parent tasks with episode outcomes \(U/D/C=100/50/0\).',
    'tab:old-prediction': 'Parent-weighted Brier scores on 72 snapshots from ten internal evaluation tasks; lower is better.',
    'tab:old-e2e': 'Three-task policy comparison: 24 blocks, three sessions, four cycles per policy; all 48 executions validate.',
    'tab:old-overhead': 'Controller timings in milliseconds per three-session batch; overlapping categories are reported separately.',
    'tab:continuous-e2e': 'Five-policy comparison at 150 blocks and six sessions: totals per arm, mean response per user turn; 48/48 turns validate.',
    'tab:continuous-overhead': 'Continuous-agent timing breakdown, seconds per arm; categories overlap and model loading is outside the response window.',
    'tab:continuous-prediction': 'Within-project chronological validation on 274 snapshots: empirical prediction leads at short horizons, GBDT at 30 seconds (binary Brier).',
}


def caption_spans(text):
    """Find balanced caption arguments, including nested mathematical braces."""
    for match in re.finditer(r'\\caption\{', text):
        depth, at = 1, match.end()
        while depth:
            if text[at] == '{' and text[at - 1] != '\\':
                depth += 1
            elif text[at] == '}' and text[at - 1] != '\\':
                depth -= 1
            at += 1
        yield match.end(), at - 1


def apply_captions(text):
    pattern = r'\\begin\{(figure\*?|table\*?)\}(.*?)\\end\{\1\}'

    def shorten(match):
        block = match.group(0)
        labels = re.findall(r'\\label\{([^}]+)\}', block)
        spans = list(caption_spans(block))
        if not spans:  # Inherited float wrapper declaration.
            return block
        assert len(labels) == len(spans) == 1, labels
        assert labels[0] in CAPTIONS, labels[0]
        start, end = spans[0]
        return block[:start] + CAPTIONS[labels[0]] + block[end:]

    return re.sub(pattern, shorten, text, flags=re.S)


def retain_measurement_details(text):
    """Keep facts that previously appeared only in captions in the body."""
    if 'The training and internal evaluation subsets have episode outcomes' not in text:
        text = text.replace(
            'The development tasks are local data-analysis workloads spanning SQL,',
            r'The training and internal evaluation subsets have episode outcomes '
            r'\(U/D/C=79/40/0\) and \(21/10/0\), respectively.'
            '\n\nThe development tasks are local data-analysis workloads spanning SQL,', 1)
    if 'Controller preparation includes native inspection, deletion, and mapping' not in text:
        text = text.replace(
            'LRU also incurs shadow-prediction auditing in this earlier experiment.',
            'Controller preparation includes native inspection, deletion, and mapping; '
            'it is not a direct timing of score integration alone. Values are averaged '
            'over four batches per policy, and overlapping categories must not be added.\n\n'
            'LRU also incurs shadow-prediction auditing in this earlier experiment.', 1)
    return text


if __name__ == '__main__':
    asset = Path(__file__).resolve().parent
    manuscript = asset.parent / 'leasepath_isca2027_paper_v3_2026-11-11.tex'
    manuscript.write_text(retain_measurement_details(apply_captions(manuscript.read_text())))
    for path in (asset / 'generated').glob('*.tex'):
        path.write_text(apply_captions(path.read_text()))
    print(f'Applied {len(CAPTIONS)} concise figure/table captions; retained measurement details in the body.')
