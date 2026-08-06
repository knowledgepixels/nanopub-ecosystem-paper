#!/usr/bin/env bash
#
# Render index.html to an RTF document (for co-authors who want Word).
#
#   scripts/make-rtf.sh              # writes paper.rtf
#   scripts/make-rtf.sh out.rtf      # or any target path
#
# RTF cannot carry SVG, so the figures are rasterised to PNG first and the
# copy that pandoc reads points at those instead.  Chrome does the rasterising
# rather than rsvg-convert, because the architecture figure embeds its font as
# a base64 @font-face rule that only a browser resolves.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/index.html"
out="${1:-$repo/paper.rtf}"

for tool in pandoc convert; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required but not installed." >&2; exit 1; }
done

chrome=""
for c in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$c" >/dev/null 2>&1; then chrome="$c"; break; fi
done
if [ -z "$chrome" ]; then
  echo "No Chrome/Chromium found; needed to rasterise the figures." >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 - "$src" "$repo" "$work" "$chrome" <<'PY'
import os, re, subprocess, sys

src, repo, work, chrome = sys.argv[1:5]
html = open(src, encoding='utf-8').read()

WIDTH_PX = 1800
WIDTH_IN = 6  # pandoc sizes images from their DPI, so stamp one that fits a page


def svg_height(svg):
    """Height in px the SVG takes when scaled to WIDTH_PX, from its aspect ratio."""
    m = re.search(r'viewBox="\s*[\d.eE+-]+[,\s]+[\d.eE+-]+[,\s]+'
                  r'([\d.eE+-]+)[,\s]+([\d.eE+-]+)', svg)
    if not m:
        return WIDTH_PX  # square is a safer guess than cropping to nothing
    w, h = float(m.group(1)), float(m.group(2))
    return max(1, round(WIDTH_PX * h / w))


def rasterise_svg(source, target):
    # Inline the SVG into a page rather than pointing Chrome at the file: a
    # standalone SVG renders at its intrinsic size, and an <img> would block
    # the embedded font.
    svg = open(source, encoding='utf-8').read()
    svg = re.sub(r'^\s*<\?xml.*?\?>', '', svg, flags=re.S)
    svg = re.sub(r'^\s*<!DOCTYPE.*?>', '', svg, flags=re.S)
    page = os.path.join(work, 'shot.html')
    open(page, 'w', encoding='utf-8').write(
        '<!doctype html><meta charset="utf-8"><style>'
        'html,body{margin:0;padding:0;background:#fff}'
        'svg{display:block;width:%dpx;height:auto}</style>\n%s' % (WIDTH_PX, svg))
    subprocess.run([chrome, '--headless=new', '--disable-gpu', '--no-sandbox',
                    '--hide-scrollbars', '--force-device-scale-factor=1',
                    '--default-background-color=FFFFFFFF',
                    '--virtual-time-budget=10000',
                    '--window-size=%d,%d' % (WIDTH_PX, svg_height(svg)),
                    '--screenshot=' + target, 'file://' + page],
                   check=True, capture_output=True)


# Rasterise every figure the document references and point the copy at the PNG.
seen = {}


def rasterise(m):
    path = m.group(1)
    if path not in seen:
        name = 'fig%d.png' % (len(seen) + 1)
        source = os.path.join(repo, path)
        target = os.path.join(work, name)
        if path.lower().endswith('.svg'):
            rasterise_svg(source, target)
        else:
            subprocess.run(['cp', source, target], check=True)
        # Without a pHYs chunk pandoc assumes 72 dpi and declares the figure
        # 25 inches wide, which pushes it off the page.
        subprocess.run(['convert', target, '-units', 'PixelsPerInch',
                        '-density', str(WIDTH_PX // WIDTH_IN), target], check=True)
        seen[path] = name
    return m.group(0).replace(path, seen[path])


html = re.sub(r'<img[^>]*src="([^"]+)"', rasterise, html)

# "Fig. 1." and friends come from CSS counters in lncs.css, which pandoc never
# sees, so write the labels into the markup instead.  Mirrors the counter rules:
# listings and tables run their own sequences.
counts = {'Fig.': 0, 'Listing': 0}


def label_figure(m):
    figure, attrs = m.group(0), m.group(1)
    if 'equation' in attrs:
        return figure  # numbered in the margin, not in a caption
    kind = 'Listing' if 'listing' in attrs else 'Fig.'
    counts[kind] += 1
    return figure.replace('<figcaption>',
                          '<figcaption>%s %d. ' % (kind, counts[kind]), 1)


html = re.sub(r'<figure\b([^>]*)>.*?</figure>', label_figure, html, flags=re.S)

tables = [0]


def label_table(m):
    tables[0] += 1
    return '%sTable %d. ' % (m.group(0), tables[0])


html = re.sub(r'<caption\b[^>]*>', label_table, html)

# dokieli's editor payload and the editing timestamp are not part of the paper.
html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
html = re.sub(r'<dl id="document-modified".*?</dl>', '', html, flags=re.S)
# Drop <title>: pandoc would render it as a heading above the h1, duplicating
# the title (and dragging the "[DRAFT]" suffix in with it).
html = re.sub(r'<title>.*?</title>', '', html, flags=re.S)

open(os.path.join(work, 'paper.html'), 'w', encoding='utf-8').write(html)
print('figures embedded: %d (%d labelled)' % (len(seen), counts['Fig.']))
PY

pandoc -f html -t rtf -s \
  --resource-path="$work" \
  -o "$out" "$work/paper.html"

echo "wrote $out ($(du -h "$out" | cut -f1))"
