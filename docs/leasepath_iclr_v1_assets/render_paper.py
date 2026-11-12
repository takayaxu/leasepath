#!/usr/bin/env python3
"""Build the v1 reading PDF and LaTeX source from the editable Markdown.

Requires pandoc, xelatex, and the TeX packages installed on this host.
No model calls, dataset access, training, or performance experiments occur.
Run from any directory: python /absolute/path/to/render_paper.py
"""

from pathlib import Path
import shutil
import subprocess


ASSETS = Path(__file__).resolve().parent
DOCS = ASSETS.parent
STEM = 'leasepath_iclr_paper_v1_2026-11-10'
BUILD = ASSETS / 'build'


def main():
    BUILD.mkdir(exist_ok=True)
    for executable in ('pandoc', 'xelatex'):
        if shutil.which(executable) is None:
            raise SystemExit(f'Missing dependency: {executable}')
    header = BUILD / 'reading-layout.tex'
    header.write_text(
        '\\usepackage{xurl}\n'
        '\\usepackage{needspace}\n'
        '\\urlstyle{tt}\n'
        '\\setlength{\\emergencystretch}{3em}\n'
        '\\usepackage{fvextra}\n'
        '\\DefineVerbatimEnvironment{verbatim}{Verbatim}'
        '{breaklines=true,fontsize=\\small}\n'
        '\\usepackage{fancyhdr}\n'
        '\\pagestyle{fancy}\n'
        '\\fancyhf{}\n'
        '\\fancyhead[L]{\\small LeasePath --- Working draft v1}\n'
        '\\fancyhead[R]{\\small November 10, 2026}\n'
        '\\fancyfoot[C]{\\thepage}\n'
        '\\setlength{\\headheight}{14pt}\n',
        encoding='utf-8',
    )
    subprocess.run(
        [
            'pandoc', str(DOCS / f'{STEM}.md'), '--standalone',
            '--from=markdown', '--to=latex', '--pdf-engine=xelatex',
            '--lua-filter', str(ASSETS / 'pdf_layout.lua'),
            '--include-in-header', str(header),
            '-V', 'mainfont=DejaVu Serif',
            '-V', 'sansfont=DejaVu Sans',
            '-V', 'monofont=DejaVu Sans Mono',
            '-V', 'linestretch=1.03',
            '-o', str(DOCS / f'{STEM}.tex'),
        ], cwd=DOCS, check=True,
    )
    for pass_id in (1, 2):
        result = subprocess.run(
            ['xelatex', '-interaction=nonstopmode', '-halt-on-error',
             f'-output-directory={BUILD}', str(DOCS / f'{STEM}.tex')],
            cwd=DOCS, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        (BUILD / f'xelatex-pass-{pass_id}.txt').write_text(
            result.stdout, encoding='utf-8'
        )
        if result.returncode:
            raise SystemExit(result.stdout[-8000:])
    shutil.copy2(BUILD / f'{STEM}.pdf', DOCS / f'{STEM}.pdf')
    print(DOCS / f'{STEM}.pdf')
    print(DOCS / f'{STEM}.tex')


if __name__ == '__main__':
    main()
