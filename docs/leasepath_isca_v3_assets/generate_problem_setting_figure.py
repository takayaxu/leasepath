"""Draw Figure 1 as a problem-setting schematic, without measured quantities.

The source is vector geometry and text; no experimental data are used or changed.
"""
from pathlib import Path
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ASSET = Path(__file__).resolve().parent
OLD_INTRO = '''Figure~\\ref{fig:agent-loop} summarizes this setting: one task
repeatedly alternates between model calls and tools, while the time to the
next legal prefix demand is estimated during each idle interval.'''
PREVIOUS_INTRO = '''Figure~\\ref{fig:agent-loop} illustrates the problem: successive
agent calls can reuse a growing prefix, but idle sessions compete for
finite cache capacity before their next demand is known. Retention saves
later prefill work at the cost of occupying capacity during the wait.'''
NEW_INTRO = '''Figure~\\ref{fig:agent-loop} connects a coding conversation to the
cache problem. A request to add a timeout leads to a file read, a patch,
and a final response; each model input preserves earlier messages and
appends the latest tool feedback. With identical prefix tokens and a
compatible execution configuration, retained KV can avoid recomputing
that history. Across sessions, however, idle prefixes compete for finite
capacity before their next demand is known.'''
CAPTION = (
    r'From conversation history to KV retention. '
    r'\textbf{(a)} A coding agent adds a timeout to \texttt{connect()}: '
    r'it reads the file, applies a patch, and responds after tool confirmation. '
    r'Later model inputs preserve earlier message spans and append tool feedback. '
    r'\textbf{(b)} The colored spans H1--H3 map to growing KV prefixes. '
    r'With identical prefix tokens and compatible execution settings, retaining '
    r'this KV avoids repeated prefill but occupies capacity during tool waits. '
    r'\textbf{(c)} Idle sessions A--C fill the shared cache when D needs space. '
    r'Unknown next-demand times create a tradeoff between retaining reusable '
    r'KV and freeing capacity. All elements are schematic.'
)
FIGURE = r'''\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth,keepaspectratio,alt={Three-panel problem-setting schematic. A coding conversation grows from a user request to read-file feedback and then patch feedback. Identical message spans H1, H2, and H3 recur in successive model inputs and map to growing KV prefixes. Idle sessions fill a shared cache when a new session needs space.}]{leasepath_isca_v3_assets/figures/figure1_problem_setting.pdf}
\caption{''' + CAPTION + r'''}
\Description{Panel a gives an illustrative coding conversation: the user requests a timeout argument, the assistant reads client.py and receives its source, then applies a patch and receives confirmation before responding. Successive model inputs repeat identical message spans H1, H2, and H3. Panel b maps those spans to reusable, growing KV prefixes across tool waits. Panel c shows idle sessions A, B, and C filling a shared cache when session D requests space. Unknown next demand creates a tradeoff between occupied capacity and future recomputation. The example and all lengths are schematic, not measured trace records.}
\label{fig:agent-loop}
\end{figure*}'''


def revise_problem_figure(text):
    """Apply the same introductory edit to v3 and to regeneration from v2."""
    if OLD_INTRO in text:
        text = text.replace(OLD_INTRO, NEW_INTRO, 1)
    elif PREVIOUS_INTRO in text:
        text = text.replace(PREVIOUS_INTRO, NEW_INTRO, 1)
    else:
        assert NEW_INTRO in text, 'Figure 1 introductory passage changed'
    pattern = r'\\begin\{figure\*\}(?:(?!\\end\{figure\*\}).)*\\label\{fig:agent-loop\}\s*\\end\{figure\*\}'
    text, count = re.subn(pattern, lambda match: FIGURE, text, flags=re.S)
    assert count == 1, f'Expected one Figure 1, found {count}'
    return text


def main():
    plt.rcParams.update({
        'font.family': 'DejaVu Sans', 'font.size': 13,
        'pdf.fonttype': 42, 'svg.fonttype': 'none',
    })
    ink, muted, line = '#172536', '#566373', '#CCD4DC'
    blue, teal, amber = '#2467A6', '#228678', '#B77E28'
    pale_blue, paper = '#EEF4FA', '#F7F9FB'
    fig = plt.figure(figsize=(11.7, 6.95), facecolor='white')
    ax = fig.add_axes((.005, .01, .99, .98))
    ax.set(xlim=(0, 11.7), ylim=(0, 6.95))
    ax.axis('off')

    def text(x, y, label, size=13, color=ink, weight='normal', **kwargs):
        return ax.text(x, y, label, fontsize=size, color=color,
                       fontweight=weight, va='center', **kwargs)

    def arrow(start, end, color=muted, lw=1.25, style='-', scale=12):
        patch = FancyArrowPatch(start, end, arrowstyle='-|>',
                                mutation_scale=scale, linewidth=lw,
                                linestyle=style, color=color,
                                shrinkA=0, shrinkB=0)
        ax.add_patch(patch)

    def box(x, y, width, height, label, color, fill=paper, size=13):
        ax.add_patch(FancyBboxPatch((x, y), width, height,
                                   boxstyle='round,pad=0,rounding_size=.045',
                                   linewidth=1.15, edgecolor=color,
                                   facecolor=fill))
        text(x + width / 2, y + height / 2, label, size,
             color=color, ha='center', weight='medium')

    # Concrete conversation first: later inputs repeat exactly the earlier spans.
    # H2 includes the preceding assistant/tool exchange, not just the tool result.
    # These invented snippets illustrate serialization; they are not trace data.
    text(.05, 6.72, '(a)  A coding conversation: later inputs carry the earlier history',
         15, weight='bold')
    text(11.56, 6.72, 'Illustrative example', 11.5, color=muted, ha='right')
    columns = [.10, 4.16, 8.22]
    history_colors = [blue, '#6496C0', '#A1BED8']
    history_fills = ['#EEF4FA', '#F2F6FA', '#F7F9FC']
    messages = [
        ('H1', 5.42, .64, ['User: Add timeout=30', 'to connect().']),
        ('H2', 4.64, .78, ['Assistant: read_file("client.py")',
                           'Tool: def connect(host): ...']),
        ('H3', 3.86, .78, ['Assistant: apply_patch(...)',
                           'Tool: Timeout argument added.']),
    ]
    for i, x in enumerate(columns):
        text(x, 6.32, f'Model call {i + 1}: input', 13, weight='bold')
        for j, (label, y, height, rows) in enumerate(messages[:i + 1]):
            color = history_colors[j]
            ax.add_patch(Rectangle((x, y), 3.36, height,
                                    facecolor=history_fills[j], edgecolor=line,
                                    linewidth=.75))
            ax.add_patch(Rectangle((x, y), .055, height,
                                    facecolor=color, edgecolor='none'))
            # Span labels provide a second cue in addition to color.
            text(x + 3.22, y + height - .13, label, 10.5,
                 color=blue if j == 2 else color, weight='bold', ha='right')
            for row, position in zip(rows, [y + height - .25, y + .17]):
                text(x + .15, position, row, 12.0)
    text(.25, 5.08, 'Output: read_file("client.py")', 12.0, color=muted)
    text(.25, 4.78, 'Tool returns the source.', 11.5, color=muted)
    arrow((3.50, 5.03), (4.03, 5.03), color=blue, scale=12)
    text(4.31, 4.30, 'Output: apply_patch(...)', 12.0, color=muted)
    text(4.31, 4.00, 'Tool confirms the edit.', 11.5, color=muted)
    arrow((7.56, 4.25), (8.09, 4.25), color=blue, scale=12)
    text(8.37, 3.59, 'Output: "Done; timeout added."', 12.0, color=muted)
    text(.10, 3.68, 'Same H label / color = identical message span',
         12, color=muted)
    ax.plot([.05, 11.57], [3.43, 3.43], color=line, lw=.9)

    text(.05, 3.20, '(b)  One session: history maps to KV prefixes', 14.3, weight='bold')
    text(7.55, 3.20, '(c)  Many sessions: finite capacity', 14.3, weight='bold')
    ax.plot([7.20, 7.20], [.16, 3.03], color=line, lw=.9)

    # A single session, left to right. Tools execute outside the LLM.
    calls = [.10, 2.67, 5.24]
    for i, x in enumerate(calls, 1):
        box(x, 2.37, 1.43, .53, f'Model call {i}', blue, pale_blue)
        arrow((x + .715, 2.31), (x + .715, 2.05), color=line, scale=9)
    for x in (1.60, 4.17):
        arrow((x, 2.635), (x + .98, 2.635), lw=1.15)
        text(x + .49, 2.94, 'tool runs', 12, color=muted, ha='center')
        text(x + .49, 2.32, 'KV waits', 11.5, color=muted, ha='center')

    # Stable colors indicate the same token-history segments at later calls.
    # Every segment belongs to the same session; width is illustrative only.
    histories = [(.10, [1.12]), (2.46, [1.12, .57]),
                 (4.81, [1.12, .57, .57])]
    colors = [blue, '#6496C0', '#A1BED8']
    for x, widths in histories:
        at = x
        for j, width in enumerate(widths):
            ax.add_patch(Rectangle((at, 1.59), width, .39,
                                    facecolor=colors[j], edgecolor='white', lw=1.1))
            text(at + width / 2, 1.785, f'H{j + 1}',
                 12, color='white' if j < 2 else ink,
                 ha='center')
            at += width
    text(.10, 1.24, 'KV for H1', 12, color=muted)
    text(2.46, 1.24, 'Reuse H1; append H2', 12, color=muted)
    text(4.81, 1.24, 'Reuse H1 + H2', 12, color=muted)

    # A single visual conclusion replaces the old histogram and policy diagram.
    ax.plot([.10, 6.93], [.92, .92], color=line, lw=.85)
    text(.10, .61, 'Retain the prefix', 13, color=blue, weight='bold')
    arrow((2.18, .61), (2.72, .61), color=blue)
    text(2.90, .61, 'save prefill when the session resumes', 12.8)

    # A full shared cache plus a fourth session's demand creates the decision.
    text(7.60, 2.91, 'Shared KV cache (full)', 12.5, color=muted)
    cache_x, cache_y, group_width = 7.60, 2.12, 1.00
    for i, (session, color) in enumerate(zip('ABC', (blue, teal, amber))):
        start = cache_x + i * group_width
        for j in range(3):
            ax.add_patch(Rectangle((start + j * group_width / 3, cache_y),
                                    group_width / 3, .48,
                                    facecolor=color, edgecolor='white', lw=.85))
        text(start + group_width / 2, 2.36, session, 13.5,
             color='white', weight='bold', ha='center')
    ax.add_patch(Rectangle((cache_x - .015, cache_y - .015),
                            3 * group_width + .03, .51,
                            facecolor='none', edgecolor=muted, lw=.9))
    box(10.99, 2.12, .50, .48, 'D', ink, 'white', 13.5)
    arrow((10.93, 2.36), (10.66, 2.36), color=ink, scale=11)
    text(10.85, 1.86, 'needs space', 11.5, color=muted, ha='center')
    text(7.60, 1.64, 'A / B / C: next demand unknown', 12, color=muted)
    text(7.60, 1.14, 'Which idle KV should stay?', 14, weight='bold')
    text(7.60, .65, 'Keep', 12.5, color=blue, weight='bold')
    text(8.40, .65, 'occupy capacity during the wait', 12)
    text(7.60, .22, 'Evict', 12.5, color=muted, weight='bold')
    text(8.40, .22, 'recompute if the prefix is reused', 12)

    destination = ASSET / 'figures/figure1_problem_setting'
    for extension, kwargs in [('pdf', {}), ('svg', {}), ('png', {'dpi': 220})]:
        fig.savefig(destination.with_suffix('.' + extension),
                    facecolor='white', pad_inches=.025, **kwargs)
    plt.close(fig)
    print(destination.with_suffix('.pdf'))


if __name__ == '__main__':
    main()
