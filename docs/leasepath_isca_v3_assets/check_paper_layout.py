"""Check the compiled draft's margins and TeX overflow before publishing it.

This is a layout preflight, not a venue-format certification. PDF font boxes
can extend slightly beyond ink: allow 0.75 bp horizontally and 2 bp vertically.
Red ACM review line numbers, running headers, and centered page numbers are
intentionally outside the body. Visual review is still required after edits.
"""
from pathlib import Path
import json
import re

import pymupdf


ASSET = Path(__file__).resolve().parent
BASENAME = 'leasepath_isca2027_paper_v3_2026-11-11'


def check(pdf, log_path):
    log = log_path.read_text()
    errors = re.findall(
        r'^.*(?:Overfull \\[hv]box|undefined references|Reference .* undefined|'
        r'Missing character:|^! ).*$', log, re.M)

    def dimension(name):
        found = re.search(r'\* \\' + name + r'=(-?[\d.]+)pt', log)
        if not found:
            raise ValueError(f'Cannot find TeX geometry: {name}')
        return float(found[1]) * 72 / 72.27

    # Use the actual class geometry, with TeX points converted to PDF points.
    left = 72 + dimension('hoffset') + dimension('oddsidemargin')
    right = left + dimension('textwidth')
    top = (72 + dimension('voffset') + dimension('topmargin')
           + dimension('headheight') + dimension('headsep'))
    bottom = top + dimension('textheight')
    violations = []
    line_count = 0
    doc = pymupdf.open(pdf)
    for page_number, page in enumerate(doc, 1):
        if '??' in page.get_text():
            errors.append(f'Unresolved reference on page {page_number}')
        for block in page.get_text('dict')['blocks']:
            if block['type'] != 0:
                continue
            for line in block['lines']:
                spans = [s for s in line['spans'] if s['color'] != 0xFF0000]
                if not spans:
                    continue
                text = ''.join(s['text'] for s in spans).strip()
                if not text:
                    continue
                x0 = min(s['bbox'][0] for s in spans)
                y0 = min(s['bbox'][1] for s in spans)
                x1 = max(s['bbox'][2] for s in spans)
                y1 = max(s['bbox'][3] for s in spans)
                if y1 < top - 4:  # ACM running header.
                    continue
                if (text.isdigit() and y0 > bottom + 3
                        and abs((x0 + x1) / 2 - page.rect.width / 2) < 12):
                    continue  # ACM centered page number.
                line_count += 1
                if (x0 < left - .75 or x1 > right + .75
                        or y0 < top - 2 or y1 > bottom + 2):
                    violations.append(dict(page=page_number,
                        bbox=[round(v, 3) for v in (x0, y0, x1, y1)],
                        text=text[:180]))
    report = dict(status='passed' if not errors and not violations else 'failed',
        pages=len(doc), body_lines_checked=line_count,
        body_bounds_bp=[round(v, 3) for v in (left, top, right, bottom)],
        tolerance_bp=dict(horizontal=.75, vertical=2),
        excluded=['ACM red review line numbers', 'running headers', 'page numbers'],
        latex_errors=errors, boundary_violations=violations,
        visual_review_still_required=True)
    (ASSET / 'build/layout-checks.json').write_text(json.dumps(report, indent=2)+'\n')
    print(f"Layout {report['status']}: {len(doc)} pages, {line_count} body lines, "
          f'{len(errors)} TeX errors, {len(violations)} boundary violations.')
    if report['status'] != 'passed':
        print(json.dumps(dict(latex_errors=errors, boundary_violations=violations),indent=2))
        raise SystemExit(1)


if __name__ == '__main__':
    check(ASSET / f'build/{BASENAME}.pdf', ASSET / f'build/{BASENAME}.log')
