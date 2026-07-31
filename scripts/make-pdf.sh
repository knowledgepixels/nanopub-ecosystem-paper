#!/usr/bin/env bash
#
# Render index.html to a PDF using headless Chrome.
#
#   scripts/make-pdf.sh              # writes paper.pdf
#   scripts/make-pdf.sh out.pdf      # writes out.pdf
#
# A4 is forced here because lncs.css has its "size:A4" declaration commented
# out, so Chrome would otherwise fall back to Letter.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/index.html"
out="${1:-$repo/paper.pdf}"

chrome=""
for c in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$c" >/dev/null 2>&1; then chrome="$c"; break; fi
done
if [ -z "$chrome" ]; then
  echo "No Chrome/Chromium found; install one or print from the browser." >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# Link every top-level entry so relative asset paths resolve: figures live in
# both media/images and analysis/figures.
for entry in "$repo"/*; do
  name="$(basename "$entry")"
  [ "$name" = "index.html" ] || ln -s "$entry" "$work/$name"
done
python3 - "$src" "$work/index.html" <<'PY'
import re, sys

src, dst = sys.argv[1], sys.argv[2]
html = open(src, encoding='utf-8').read()

# dokieli prints each link's URL after it. Those URLs are single unbreakable
# tokens, so a justified line ending just before one gets stretched. Giving
# each URL a copy with zero-width spaces after its punctuation lets the line
# breaker split it at slashes, dots and dashes instead of mid-word, which is
# what word-break:break-all would do. lncs.css renders data-print-url when
# present. Author ORCIDs are skipped: they print via data-orcid instead.
ZWSP = '​'


def annotate(m):
    tag, url = m.group(0), m.group(1)
    if 'data-print-url' in tag or 'data-orcid' in tag:
        return tag
    if 'do-print-a-href-hide' in tag:
        return tag
    broken = re.sub(r'([/\-._?&=#])', r'\1' + ZWSP, url).rstrip(ZWSP)
    return tag[:2] + ' data-print-url="' + broken + '"' + tag[2:]


html = re.sub(r'<a [^>]*href="(https?://[^"]+)"[^>]*>', annotate, html)
html = html.replace('</head>', '<style media="print">@page{size:A4;}</style>\n</head>', 1)
open(dst, 'w', encoding='utf-8').write(html)
PY

# The stylesheet and dokieli itself are fetched from dokie.li, so this needs
# network access; virtual-time-budget gives those requests time to land.
"$chrome" --headless=new --disable-gpu --no-sandbox \
  --no-pdf-header-footer --run-all-compositor-stages-before-draw \
  --virtual-time-budget=30000 \
  --print-to-pdf="$out" "file://$work/index.html" 2>/dev/null

if command -v pdfinfo >/dev/null 2>&1; then
  pdfinfo "$out" | grep -E '^(Pages|Page size)'
fi
echo "wrote $out"
