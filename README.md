# Nanopub Ecosystem Paper

LNCS-style paper for ISWC 2026 on the second-generation Nanopub ecosystem and the IDEBT trust algorithm. The manuscript is written in [dokieli](https://dokie.li/) — a single self-contained HTML file that doubles as the rendered article.

## Top-level layout

- **`index.html`** — the paper itself. Open in a browser to read; edit in dokieli to author.
- **`README.md`** — this file.
- **`.gitignore`** — local ignore rules.

## Folders

- **`media/`** — assets used to render `index.html`: LNCS-style CSS, fonts, and images (figures embedded in the paper).
- **`scripts/`** — `dokieli.js`, the bundled dokieli editor loaded by `index.html`.
- **`slides/`** — companion dokieli slide deck (`index.html` + `img/`) summarising the paper for talks.
- **`analysis/`** — reproducibility code and data behind §5.1 (live network snapshot) and §5.3 (network simulation):
  - `fetch_snapshot.py` — pulls a snapshot from the live Nanopub Registry.
  - `analyze.py` — computes the descriptive statistics and snapshot figure.
  - `compare_trustrank.py` — compares IDEBT scores against personalized PageRank.
  - `simulate_plots.py` — generates the simulation-study figure from the XLSX checkpoints.
  - `data/` — fetched snapshot (JSON + trust-path dump) consumed by the scripts above.
  - `figures/` — generated figures (SVG/PDF/PNG) embedded in the paper.
  - `network_simulation_results/` — raw simulation outputs (`Stats checkpoints.xlsx`, `plots.ipynb`) and the §4.2 Evaluation working doc.
