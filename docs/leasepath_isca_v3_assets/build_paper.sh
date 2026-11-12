#!/usr/bin/env bash
set -euo pipefail
paper_asset_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
paper_doc_dir="$(dirname -- "$paper_asset_dir")"
cd -- "$paper_doc_dir"
mkdir -p "$paper_asset_dir/build"
export TEXINPUTS="$paper_doc_dir/leasepath_isca_v2_assets/vendor/acmart//:${TEXINPUTS:-}"
paper_basename=leasepath_isca2027_paper_v3_2026-11-11
for paper_pass in 1 2; do
  xelatex -halt-on-error -interaction=nonstopmode \
    -output-directory="$paper_asset_dir/build" "$paper_basename.tex" \
    > "$paper_asset_dir/build/compile-pass${paper_pass}.stdout" 2>&1
done
if rg -q 'Overfull|undefined references|Reference .* undefined|Missing character:|^! ' \
  "$paper_asset_dir/build/$paper_basename.log"; then
  rg -n 'Overfull|undefined references|Reference .* undefined|Missing character:|^! ' \
    "$paper_asset_dir/build/$paper_basename.log"
  exit 1
fi
python -B "$paper_asset_dir/check_paper_layout.py"
cp -- "$paper_asset_dir/build/$paper_basename.pdf" "$paper_doc_dir/$paper_basename.pdf"
printf '%s\n' "$paper_doc_dir/$paper_basename.pdf"
