#!/usr/bin/env python3
"""Compute descriptive statistics and figures over a Registry snapshot.

Inputs (read from analysis/data/, produced by fetch_snapshot.py):
  registry.json       top-level metadata
  list.json           per-account aggregates
  agents.json         per-agent aggregates
  trustPaths.txt      newline-separated trust paths

Outputs:
  analysis/data/stats.json         computed numbers used in the paper
  analysis/figures/depth-mass.svg  two-panel: per-path depth and ratio mass per depth
  analysis/figures/fanout.svg      endorsement fan-out distribution per signer-key
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FIGS = ROOT / "figures"
FIGS.mkdir(exist_ok=True)


def load():
    snap = json.loads((DATA / "snapshot.json").read_text())
    accounts = json.loads((DATA / "list.json").read_text())
    agents = json.loads((DATA / "agents.json").read_text())
    perkey_counts = json.loads((DATA / "perkey_counts.json").read_text())
    # /debug/trustPaths uses ' > ' between chain hops and ' ~ ' before the
    # final hop of an "extended" path (DebugPage.getTrustPathsTxt).  Both are
    # real chain hops as far as IDEBT is concerned; the ' ~ ' marker only
    # records that the final endpoint is already reached primarily by another
    # path.  Tokenise on either separator so depth and edge counts are correct.
    paths = []
    extended_flags = []
    for line in (DATA / "trustPaths.txt").read_text().splitlines():
        if not line.strip():
            continue
        is_extended = " ~ " in line
        hops = line.replace(" ~ ", " > ").split(" > ")
        paths.append(hops)
        extended_flags.append(is_extended)
    return snap, accounts, agents, perkey_counts, paths, extended_flags


def compute(snap, accounts, agents, perkey_counts, paths, extended_flags):
    loaded = [a for a in accounts if a["status"] == "loaded" and a["agent"] != "$"]
    contested = [a for a in accounts if a["status"] == "contested"]
    skipped = [a for a in accounts if a["status"] == "skipped"]

    real_agents = [a for a in agents if a["agent"] != "$"]
    accounts_per_agent = Counter(a["accountCount"] for a in real_agents)

    primary_count = sum(1 for f in extended_flags if not f)
    extended_count = sum(1 for f in extended_flags if f)

    depth_counts = Counter(len(p) - 1 for p in paths)
    depth_mass = defaultdict(float)
    for a in accounts:
        if "ratio" in a:
            depth_mass[a["depth"]] += a["ratio"]

    edges = set()
    for p in paths:
        for i in range(1, len(p)):
            edges.add((p[i - 1], p[i]))

    fanout = Counter()
    for parent, _ in edges:
        fanout[parent] += 1
    fanout_dist = Counter(fanout.values())

    pathcount_dist = Counter(a["pathCount"] for a in loaded)

    quota_dist = Counter(a["quota"] for a in loaded)

    ratios = sorted([a["ratio"] for a in loaded], reverse=True)
    total_ratio = sum(ratios)
    above_1e4 = sum(1 for r in ratios if r >= 1e-4)
    above_1e3 = sum(1 for r in ratios if r >= 1e-3)
    top_agent = max(real_agents, key=lambda a: a["totalRatio"])

    # Nanopubs per pubkey and per agent (summed across that agent's pubkeys).
    pubkey_to_agent = {a["pubkey"]: a["agent"] for a in loaded}
    perkey_sorted = sorted(
        [(pk, n) for pk, n in perkey_counts.items() if n > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    agent_counts: dict[str, int] = defaultdict(int)
    for pk, n in perkey_counts.items():
        ag = pubkey_to_agent.get(pk, "?")
        agent_counts[ag] += n
    peragent_sorted = sorted(
        [(a, n) for a, n in agent_counts.items() if n > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    total_nanopubs = sum(n for _, n in perkey_sorted)

    def share(sorted_pairs, k):
        return sum(n for _, n in sorted_pairs[:k]) / total_nanopubs

    nanopub_concentration = {
        "total_counted": total_nanopubs,
        "n_pubkeys_with_content": len(perkey_sorted),
        "n_agents_with_content": len(peragent_sorted),
        "top1_pubkey_share": share(perkey_sorted, 1),
        "top10_pubkey_share": share(perkey_sorted, 10),
        "top1_agent_share": share(peragent_sorted, 1),
        "top10_agent_share": share(peragent_sorted, 10),
        "top1_pubkey_count": perkey_sorted[0][1] if perkey_sorted else 0,
        "top1_agent": peragent_sorted[0] if peragent_sorted else None,
    }

    return {
        "snapshot": snap,
        "counts": {
            "agents": len(real_agents),
            "accounts": len(accounts) - 1,
            "loaded": len(loaded),
            "contested": len(contested),
            "skipped": len(skipped),
            "nanopubs": snap["nanopub_count"],
            "trust_paths": len(paths) - 1,
            "primary_paths": primary_count - 1,
            "extended_paths": extended_count,
            "trust_edges": len(edges),
        },
        "accounts_per_agent": dict(sorted(accounts_per_agent.items())),
        "depth_counts": dict(sorted(depth_counts.items())),
        "depth_mass": dict(sorted(depth_mass.items())),
        "fanout_dist": dict(sorted(fanout_dist.items())),
        "pathcount_dist": dict(sorted(pathcount_dist.items())),
        "quota_dist_top": dict(sorted(quota_dist.most_common(8))),
        "quota_distinct": len(quota_dist),
        "ratio_total_loaded": total_ratio,
        "ratio_above_1e_minus_4": above_1e4,
        "ratio_above_1e_minus_3": above_1e3,
        "top_agent": {"id": top_agent["agent"], "totalRatio": top_agent["totalRatio"]},
        "nanopub_concentration": nanopub_concentration,
        "_perkey_sorted": perkey_sorted,
        "_peragent_sorted": peragent_sorted,
    }


def plot_depth_and_mass(stats):
    depths = sorted(stats["depth_counts"].keys())
    counts = [stats["depth_counts"][d] for d in depths]
    mass_depths = sorted(stats["depth_mass"].keys())
    mass = [stats["depth_mass"][d] for d in mass_depths]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.6))

    ax1.bar(depths, counts, color="#3b6fb6", width=0.7)
    for d, c in zip(depths, counts):
        ax1.text(d, c, str(c), ha="center", va="bottom", fontsize=8)
    ax1.set_xlabel("path depth (hops from root)")
    ax1.set_ylabel("trust paths")
    ax1.set_title("(a) per-path depth distribution", fontsize=10)
    ax1.set_xticks(depths)
    ax1.set_ylim(0, max(counts) * 1.18)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    ax2.bar(mass_depths, mass, color="#b6573b", width=0.7)
    ax2.set_yscale("log")
    ax2.set_xlabel("depth")
    ax2.set_ylabel("aggregated ratio mass (log)")
    ax2.set_title("(b) ratio mass per depth", fontsize=10)
    ax2.set_xticks(mass_depths)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    for d, m in zip(mass_depths, mass):
        ax2.text(d, m, f"{m:.2g}", ha="center", va="bottom", fontsize=8)

    fig.tight_layout()
    out = FIGS / "depth-mass.svg"
    fig.savefig(out, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out


def plot_publication_volume(stats):
    perkey = stats["_perkey_sorted"]
    peragent = stats["_peragent_sorted"]

    pk_ranks = list(range(1, len(perkey) + 1))
    pk_counts = [n for _, n in perkey]
    ag_ranks = list(range(1, len(peragent) + 1))
    ag_counts = [n for _, n in peragent]

    fig, ax = plt.subplots(figsize=(5.0, 2.8))
    ax.plot(pk_ranks, pk_counts, color="#3b6fb6", lw=1.4, label=f"per pubkey ({len(perkey)})")
    ax.plot(ag_ranks, ag_counts, color="#b6573b", lw=1.4, label=f"per agent ({len(peragent)})")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("rank (ordered by descending count)")
    ax.set_ylabel("nanopublications")
    ax.set_title("publication volume per signer-key and per agent", fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, which="both", linestyle=":", alpha=0.4)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    out = FIGS / "publication-volume.svg"
    fig.savefig(out, format="svg", bbox_inches="tight")
    plt.close(fig)
    # Remove the obsolete fanout figure if still present.
    old = FIGS / "fanout.svg"
    if old.exists():
        old.unlink()
    return out


def main():
    snap, accounts, agents, perkey_counts, paths, extended_flags = load()
    stats = compute(snap, accounts, agents, perkey_counts, paths, extended_flags)
    plot_depth_and_mass(stats)
    plot_publication_volume(stats)
    # Strip private (sortable list) fields before persisting stats.
    persist = {k: v for k, v in stats.items() if not k.startswith("_")}
    (DATA / "stats.json").write_text(json.dumps(persist, indent=2) + "\n")
    print(json.dumps(persist, indent=2))


if __name__ == "__main__":
    main()
