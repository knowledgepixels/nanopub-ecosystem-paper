#!/usr/bin/env python3
"""Compare IDEBT trust scores against personalized PageRank (TrustRank).

Builds the raw endorsement graph at the pubkey level, runs personalized
PageRank from the IDEBT setting's first-hop seeds, and compares the
resulting TrustRank scores against IDEBT's per-pubkey ratio (summed across
agents claiming the same key).

Inputs (read from analysis/data/):
  list.json                  per-account IDEBT aggregates
  agents.json                per-agent IDEBT aggregates (used for diagnostics)
  pubkeys.json               full set of registry pubkey hashes
  trustPaths.txt             used to derive the seed (first-hop endorsements
                             from the root setting `$`)
  event_timestamps.json      intros + endorsements published by loaded
                             pubkeys (produced by fetch_snapshot.py)
  event_timestamps_extra.json    optional, written by --fetch-extended:
                             intros + endorsements from unloaded pubkeys,
                             so that endorsement edges and targets that
                             IDEBT pruned via MIN_RATIO are also visible.

Outputs:
  data/trustrank_vs_idebt.csv         one row per pubkey with both scores
  figures/trustrank_vs_idebt.pdf,.png log-log scatter, color-coded by IDEBT
                                      depth, with rank correlations
  stdout                              headline numbers (correlations,
                                      top-K overlap, coverage gaps)

Usage:
  python compare_trustrank.py                  # uses existing snapshot data
  python compare_trustrank.py --fetch-extended # also pulls intros/endorsements
                                               # from unloaded pubkeys (~700)
                                               # into data/event_timestamps_extra.json
  python compare_trustrank.py --alpha 0.85     # PageRank damping (default 0.85)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from scipy.stats import kendalltau, spearmanr

import fetch_snapshot as fs

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FIGS = ROOT / "figures"
FIGS.mkdir(exist_ok=True)

ART_RE = re.compile(r"RA[A-Za-z0-9_-]{43}")


# -----------------------------------------------------------------------------
# Optional supplemental fetch: pulls intros + endorsements from pubkeys that
# IDEBT did not load, so that targets/sources below MIN_RATIO become visible.

def fetch_extended() -> dict[str, list[dict]]:
    """Fetch intros + endorsements from pubkeys not present in list.json.

    Mirrors fetch_snapshot.fetch_event_timestamps but iterates over the
    complement of loaded pubkeys, so the resulting file complements (not
    replaces) event_timestamps.json. Endorsement records get an `approves`
    list; intros only need (pubkey, np) for the artifact -> pubkey map.
    """
    accounts = json.loads((DATA / "list.json").read_text())
    loaded = {a["pubkey"] for a in accounts if a.get("status") == "loaded"}
    all_pks = set(json.loads((DATA / "pubkeys.json").read_text()))
    targets = sorted(all_pks - loaded - {"$"})
    print(f"  fetching events from {len(targets)} unloaded pubkeys")

    def list_ids(pk: str, type_hash: str) -> list[tuple[str, str, bool]]:
        try:
            rows = json.loads(fs.fetch(f"/list/{pk}/{type_hash}.json"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise
        return [(pk, r["np"], bool(r.get("invalidated", False))) for r in rows]

    intros: list[tuple[str, str, bool]] = []
    endors: list[tuple[str, str, bool]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        i_futs = [ex.submit(list_ids, pk, fs.INTRO_TYPE_HASH) for pk in targets]
        e_futs = [ex.submit(list_ids, pk, fs.ENDORSE_TYPE_HASH) for pk in targets]
        for f in as_completed(i_futs):
            intros.extend(f.result())
        for f in as_completed(e_futs):
            endors.extend(f.result())
    print(f"  found {len(intros)} extra intros, {len(endors)} extra endorsements")

    # Endorsements need TriG parsing to recover approves[]; intros do not.
    out: dict[str, list[dict]] = {
        "introductions": [
            {"pubkey": pk, "np": np, "invalidated": inv}
            for pk, np, inv in intros
        ],
        "endorsements": [],
    }
    if endors:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {
                ex.submit(
                    lambda u: fs.fetch(f"/np/{u}", accept="application/trig").decode("utf-8", "replace"),
                    np,
                ): (pk, np, inv)
                for pk, np, inv in endors
            }
            for i, f in enumerate(as_completed(futs), 1):
                pk, np, inv = futs[f]
                try:
                    body = f.result()
                    approves = fs.extract_approves_of(body)
                except Exception as e:
                    print(f"  warning: fetch failed for {np}: {e}")
                    approves = []
                out["endorsements"].append(
                    {"pubkey": pk, "np": np, "invalidated": inv, "approves": approves}
                )
                if i % 100 == 0 or i == len(endors):
                    print(f"  extra endorsements: {i}/{len(endors)}")
    return out


# -----------------------------------------------------------------------------
# Graph construction

def load_events() -> dict[str, list[dict]]:
    base = json.loads((DATA / "event_timestamps.json").read_text())
    extra_path = DATA / "event_timestamps_extra.json"
    if extra_path.exists():
        extra = json.loads(extra_path.read_text())
        seen_intros = {x["np"] for x in base["introductions"]}
        seen_endors = {x["np"] for x in base["endorsements"]}
        added_i = added_e = 0
        for x in extra.get("introductions", []):
            if x["np"] not in seen_intros:
                base["introductions"].append(x)
                seen_intros.add(x["np"])
                added_i += 1
        for x in extra.get("endorsements", []):
            if x["np"] not in seen_endors:
                base["endorsements"].append(x)
                seen_endors.add(x["np"])
                added_e += 1
        print(f"  merged extras: +{added_i} intros, +{added_e} endorsements")
    return base


def build_graph(events: dict[str, list[dict]]) -> tuple[nx.DiGraph, dict[str, int], int]:
    """Pubkey-level directed multigraph collapsed to a weighted DiGraph.

    Nodes: pubkey hashes that appear in any intro or endorsement.
    Edges: for each non-invalidated endorsement signed by `kx` that approves
    intros published by `{ky_1, ..., ky_n}`, an edge kx -> ky_i with weight =
    number of distinct endorsement nanopubs (capped per pair to avoid
    re-endorsement spam dominating the graph).
    """
    intro_pubkey: dict[str, str] = {}
    for x in events["introductions"]:
        if x.get("invalidated"):
            continue
        m = ART_RE.search(x["np"])
        if m:
            intro_pubkey[m.group(0)] = x["pubkey"]

    G = nx.DiGraph()
    unresolved = 0
    for e in events["endorsements"]:
        if e.get("invalidated"):
            continue
        kx = e["pubkey"]
        for ac in e.get("approves", []):
            ky = intro_pubkey.get(ac)
            if ky is None:
                unresolved += 1
                continue
            if kx == ky:
                # Self-endorsement carries no information for either algorithm.
                continue
            if G.has_edge(kx, ky):
                G[kx][ky]["weight"] += 1.0
            else:
                G.add_edge(kx, ky, weight=1.0)
    return G, intro_pubkey, unresolved


def derive_seed() -> set[str]:
    """First-hop pubkeys reached directly from the root `$` in trustPaths.txt."""
    seed = set()
    for line in (DATA / "trustPaths.txt").read_text().splitlines():
        line = line.strip()
        if not line.startswith("$ > "):
            continue
        rest = line[4:].replace(" ~ ", " > ")
        first = rest.split(" > ", 1)[0]
        if "|" in first:
            seed.add(first.split("|", 1)[1])
    return seed


# -----------------------------------------------------------------------------
# Comparison

def idebt_per_pubkey() -> dict[str, dict]:
    """Aggregate list.json rows to one record per pubkey."""
    out: dict[str, dict] = {}
    for a in json.loads((DATA / "list.json").read_text()):
        pk = a["pubkey"]
        if pk == "$":
            continue
        rec = out.setdefault(
            pk,
            {"ratio": 0.0, "min_depth": None, "agents": 0, "loaded": False},
        )
        if a.get("status") == "loaded":
            rec["loaded"] = True
            rec["ratio"] += float(a.get("ratio", 0.0))
            d = a.get("depth")
            if d is not None:
                rec["min_depth"] = d if rec["min_depth"] is None else min(rec["min_depth"], d)
        rec["agents"] += 1
    return out


def topk_overlap(idebt: dict[str, float], tr: dict[str, float], k: int) -> float:
    a = sorted(idebt, key=lambda p: idebt[p], reverse=True)[:k]
    b = sorted(tr, key=lambda p: tr[p], reverse=True)[:k]
    return len(set(a) & set(b)) / k


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.85,
                    help="PageRank damping (default 0.85)")
    ap.add_argument("--fetch-extended", action="store_true",
                    help="Fetch intros/endorsements from unloaded pubkeys")
    args = ap.parse_args()

    if args.fetch_extended:
        extra_path = DATA / "event_timestamps_extra.json"
        print(f"Fetching extended events -> {extra_path.name}")
        extra = fetch_extended()
        extra_path.write_text(json.dumps(extra, indent=2) + "\n")

    print("Loading events ...")
    events = load_events()

    print("Building endorsement graph ...")
    G, intro_pubkey, unresolved = build_graph(events)
    print(f"  nodes: {G.number_of_nodes()}, edges: {G.number_of_edges()}, "
          f"unresolved approves: {unresolved}")

    seed = derive_seed()
    # Many seed pubkeys carry no observable endorsement traffic — they are
    # trust anchors via the setting, not via any endorsement nanopub.  Add
    # them as nodes so they get their share of the restart mass even with
    # no out-edges; PageRank treats edge-less seeds as dangling and
    # redistributes via the personalization vector.
    new_seeds = seed - set(G.nodes)
    G.add_nodes_from(new_seeds)
    print(f"  seed: {len(seed)} first-hop pubkeys "
          f"({len(seed) - len(new_seeds)} already in graph, "
          f"{len(new_seeds)} added as anchor nodes)")

    personalization = {n: 0.0 for n in G.nodes}
    for s in seed:
        personalization[s] = 1.0 / len(seed)
    print(f"Running personalized PageRank (alpha={args.alpha}) ...")
    tr_scores = nx.pagerank(
        G, alpha=args.alpha, personalization=personalization, weight="weight",
    )

    idebt = idebt_per_pubkey()

    # In-degree per pubkey for the spam-resistance / fan-out check.  Two
    # variants: distinct endorsing pubkeys (in-degree on the simple graph)
    # and the total endorsement-nanopub count (sum of edge weights).
    indeg = {n: int(G.in_degree(n)) for n in G.nodes}
    indeg_w = {n: float(G.in_degree(n, weight="weight")) for n in G.nodes}

    # Restrict the comparison to pubkeys IDEBT loaded (positive ratio); this
    # is the apples-to-apples set.  Coverage stats below report on the rest.
    loaded = {pk: r for pk, r in idebt.items() if r["loaded"] and r["ratio"] > 0}
    common = sorted(set(loaded) & set(tr_scores))
    if not common:
        raise SystemExit("no overlap between IDEBT-loaded pubkeys and graph nodes")
    idebt_vec = [loaded[pk]["ratio"] for pk in common]
    tr_vec = [tr_scores[pk] for pk in common]
    rho, _ = spearmanr(idebt_vec, tr_vec)
    tau, _ = kendalltau(idebt_vec, tr_vec)

    idebt_only_score = {pk: r["ratio"] for pk, r in loaded.items()}
    overlap_10 = topk_overlap(idebt_only_score, tr_scores, 10)
    overlap_50 = topk_overlap(idebt_only_score, tr_scores, 50)

    tr_nonzero = {pk for pk, s in tr_scores.items() if s > 1e-12}
    tr_only = tr_nonzero - set(loaded)
    idebt_only = set(loaded) - tr_nonzero

    print("\n=== Results ===")
    print(f"  pubkeys compared (loaded by IDEBT, present in graph): {len(common)}")
    print(f"  Spearman rho:                                         {rho:+.4f}")
    print(f"  Kendall tau:                                          {tau:+.4f}")
    print(f"  top-10 overlap:                                       {overlap_10:.2f}")
    print(f"  top-50 overlap:                                       {overlap_50:.2f}")
    print(f"  IDEBT-loaded but TrustRank ~ 0:                       {len(idebt_only)}")
    print(f"  TrustRank > 0 but not loaded by IDEBT:                {len(tr_only)}")
    print(f"  graph edges with no path back to seed:                "
          f"{G.number_of_edges() - sum(1 for _, _, w in G.edges(data='weight') if w)}")

    # Spam-resistance check: how strongly does each algorithm track in-degree?
    # IDEBT (Lemma 1) should be roughly invariant to fan-in; TrustRank's
    # eigenvector logic should track it closely.
    common_indeg = [indeg.get(pk, 0) for pk in common]
    rho_id_in, _ = spearmanr(common_indeg, idebt_vec)
    rho_tr_in, _ = spearmanr(common_indeg, tr_vec)
    print("\n=== Fan-out / in-degree dependence ===")
    print(f"  Spearman(in-degree, IDEBT ratio):     {rho_id_in:+.4f}")
    print(f"  Spearman(in-degree, TrustRank score): {rho_tr_in:+.4f}")

    # Top-50 disagreement analysis: keys exclusive to each top-50 list, then
    # report the in-degree distribution of each group.
    top_id = set(sorted(common, key=lambda p: idebt[p]["ratio"], reverse=True)[:50])
    top_tr = set(sorted(common, key=lambda p: tr_scores[p], reverse=True)[:50])
    groups = {
        "both top-50":            top_id & top_tr,
        "IDEBT only (TR drops)":  top_id - top_tr,
        "TrustRank only (TR adds)": top_tr - top_id,
    }
    print("  Top-50 disagreement, in-degree distribution:")
    print(f"    {'group':25s}  {'n':>3s}  {'median':>6s}  {'mean':>6s}  {'max':>4s}")
    for label, g in groups.items():
        if not g:
            print(f"    {label:25s}  {len(g):>3d}  (empty)")
            continue
        vs = sorted(indeg[pk] for pk in g)
        med = vs[len(vs) // 2]
        mean = sum(vs) / len(vs)
        print(f"    {label:25s}  {len(g):>3d}  {med:>6d}  {mean:>6.1f}  {max(vs):>4d}")

    # CSV: union of pubkeys that IDEBT touched OR TrustRank reached.
    rows_keys = sorted(set(idebt) | tr_nonzero)
    csv_path = DATA / "trustrank_vs_idebt.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pubkey", "idebt_ratio", "idebt_loaded", "idebt_depth",
                    "idebt_agents", "trustrank", "in_degree", "endorsement_count"])
        for pk in rows_keys:
            r = idebt.get(pk, {"ratio": 0.0, "loaded": False, "min_depth": "",
                               "agents": 0})
            w.writerow([
                pk,
                f"{r['ratio']:.6e}",
                int(r["loaded"]),
                "" if r["min_depth"] is None else r["min_depth"],
                r["agents"],
                f"{tr_scores.get(pk, 0.0):.6e}",
                indeg.get(pk, 0),
                f"{indeg_w.get(pk, 0.0):.0f}",
            ])
    print(f"\nWrote {csv_path}")

    # Scatter (log-log).  Color by IDEBT depth so the depth-bound vs.
    # global-eigenvector contrast is visible at a glance.
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    depths = [loaded[pk]["min_depth"] or 0 for pk in common]
    sc = ax.scatter(idebt_vec, tr_vec, c=depths, cmap="viridis",
                    s=14, alpha=0.7, edgecolors="none")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("IDEBT ratio (per pubkey, summed across agents)")
    ax.set_ylabel(f"TrustRank score (alpha={args.alpha})")
    ax.set_title(f"Spearman rho = {rho:+.3f}  Kendall tau = {tau:+.3f}  "
                 f"(n={len(common)})", fontsize=10)
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("IDEBT depth")
    fig.tight_layout()
    fig.savefig(FIGS / "trustrank_vs_idebt.pdf")
    fig.savefig(FIGS / "trustrank_vs_idebt.png", dpi=180)
    print(f"Wrote {FIGS / 'trustrank_vs_idebt.pdf'} and .png")


if __name__ == "__main__":
    main()
