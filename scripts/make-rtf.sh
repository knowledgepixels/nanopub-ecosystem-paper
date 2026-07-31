#!/usr/bin/env bash
#
# Render index.html to an RTF document (for co-authors who want Word).
#
#   scripts/make-rtf.sh              # writes paper.rtf
#   scripts/make-rtf.sh out.rtf      # or any target path
#
# RTF cannot carry SVG, so the figures are rasterised to PNG first and the
# copy that pandoc reads points at those instead.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/index.html"
out="${1:-$repo/paper.rtf}"

for tool in pandoc rsvg-convert convert; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required but not installed." >&2; exit 1; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 - "$src" "$repo" "$work" <<'PY'
import os, re, subprocess, sys

src, repo, work = sys.argv[1], sys.argv[2], sys.argv[3]
html = open(src, encoding='utf-8').read()

# Rasterise every figure the document references and point the copy at the PNG.
seen = {}


WIDTH_PX = 1800
WIDTH_IN = 6  # pandoc sizes images from their DPI, so stamp one that fits a page


def rasterise(m):
    path = m.group(1)
    if path not in seen:
        name = 'fig%d.png' % (len(seen) + 1)
        source = os.path.join(repo, path)
        target = os.path.join(work, name)
        if path.lower().endswith('.svg'):
            subprocess.run(['rsvg-convert', '-w', str(WIDTH_PX), '-o', target,
                            source], check=True)
        else:
            subprocess.run(['cp', source, target], check=True)
        # Without a pHYs chunk pandoc assumes 72 dpi and declares the figure
        # 25 inches wide, which pushes it off the page.
        subprocess.run(['convert', target, '-units', 'PixelsPerInch',
                        '-density', str(WIDTH_PX // WIDTH_IN), target], check=True)
        seen[path] = name
    return m.group(0).replace(path, seen[path])


html = re.sub(r'<img[^>]*src="([^"]+)"', rasterise, html)

# dokieli's editor payload and the editing timestamp are not part of the paper.
html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
html = re.sub(r'<dl id="document-modified".*?</dl>', '', html, flags=re.S)
# Drop <title>: pandoc would render it as a heading above the h1, duplicating
# the title (and dragging the "[DRAFT]" suffix in with it).
html = re.sub(r'<title>.*?</title>', '', html, flags=re.S)

open(os.path.join(work, 'paper.html'), 'w', encoding='utf-8').write(html)
print('figures embedded: %d' % len(seen))
PY

pandoc -f html -t rtf -s \
  --resource-path="$work" \
  -o "$out" "$work/paper.html"

echo "wrote $out ($(du -h "$out" | cut -f1))"
