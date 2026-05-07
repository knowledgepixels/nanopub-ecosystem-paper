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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
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
    events = json.loads((DATA / "event_timestamps.json").read_text())
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
    return snap, accounts, agents, perkey_counts, events, paths, extended_flags


def parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    # ISO 8601 with optional fractional seconds and Z or +HH:MM offset.
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def compute(snap, accounts, agents, perkey_counts, events, paths, extended_flags):
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

    intro_all = sorted(t for t in (parse_ts(x["created"]) for x in events["introductions"]) if t)
    endor_all = sorted(t for t in (parse_ts(x["created"]) for x in events["endorsements"]) if t)

    # Dedupe intros to the earliest timestamp per pubkey: the moment the
    # identity binding first appeared in the network. Subsequent intros for
    # the same pubkey are re-issuances (updated metadata, etc.) and would
    # otherwise inflate the growth curve.
    intro_first_per_pk: dict[str, datetime] = {}
    for x in events["introductions"]:
        t = parse_ts(x["created"])
        if not t:
            continue
        cur = intro_first_per_pk.get(x["pubkey"])
        if cur is None or t < cur:
            intro_first_per_pk[x["pubkey"]] = t
    intro_ts = sorted(intro_first_per_pk.values())

    # The registry's per-(pubkey, type) endorsement list contains every
    # nanopub that the loader has tagged with the npx:approvesOf type, but a
    # known gap in the loader (RegistryDB.loadNanopubVerified, "TODO Check if
    # nanopub really has the type?") can let unrelated nanopubs slip in for
    # bootstrap-only pubkeys.  To restrict to genuine trust-edge endorsements
    # we (a) drop nanopubs explicitly invalidated, and (b) require the
    # assertion to contain at least one npx:approvesOf triple whose object is
    # one of the known introduction nanopubs.
    intro_artifact_codes = {x["np"] for x in events["introductions"]}
    endor_ts = sorted(
        t for x in events["endorsements"]
        if not x.get("invalidated")
        and any(ac in intro_artifact_codes for ac in x.get("approves", []))
        for t in [parse_ts(x["created"])]
        if t
    )
    endor_genuine = sum(
        1 for x in events["endorsements"]
        if any(ac in intro_artifact_codes for ac in x.get("approves", []))
    )

    timeline = {
        "introductions_total": len(events["introductions"]),
        "introductions_with_ts": len(intro_all),
        "introductions_first_per_pubkey": len(intro_ts),
        "endorsements_total": len(events["endorsements"]),
        "endorsements_with_ts": len(endor_all),
        "endorsements_invalidated": sum(1 for x in events["endorsements"] if x.get("invalidated")),
        "endorsements_genuine": endor_genuine,
        "endorsements_active": len(endor_ts),
        "intro_first": intro_ts[0].isoformat() if intro_ts else None,
        "intro_last": intro_ts[-1].isoformat() if intro_ts else None,
        "endor_first": endor_ts[0].isoformat() if endor_ts else None,
        "endor_last": endor_ts[-1].isoformat() if endor_ts else None,
        "endorsing_keys": len({x["pubkey"] for x in events["endorsements"]}),
    }

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
        "timeline": timeline,
        "_perkey_sorted": perkey_sorted,
        "_peragent_sorted": peragent_sorted,
        "_intro_ts": [t.isoformat() for t in intro_ts],
        "_endor_ts": [t.isoformat() for t in endor_ts],
    }


def plot_snapshot(stats):
    depths = sorted(stats["depth_counts"].keys())
    counts = [stats["depth_counts"][d] for d in depths]
    intro_ts = [datetime.fromisoformat(s) for s in stats["_intro_ts"]]
    endor_ts = [datetime.fromisoformat(s) for s in stats["_endor_ts"]]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.0, 2.8))

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

    if intro_ts:
        ax2.plot(intro_ts, range(1, len(intro_ts) + 1), color="#3b6fb6",
                 lw=1.6, label=f"introductions ({len(intro_ts)})")
    if endor_ts:
        ax2.plot(endor_ts, range(1, len(endor_ts) + 1), color="#b6573b",
                 lw=1.6, label=f"endorsements ({len(endor_ts)})")
    ax2.set_xlabel("creation time (UTC)")
    ax2.set_ylabel("cumulative count")
    ax2.set_title("(b) cumulative growth", fontsize=10)
    ax2.legend(frameon=False, fontsize=8, loc="upper left")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.tight_layout()
    out = FIGS / "snapshot.svg"
    fig.savefig(out, format="svg", bbox_inches="tight")
    plt.close(fig)
    for stale in (FIGS / "depth-mass.svg", FIGS / "network-growth.svg"):
        if stale.exists():
            stale.unlink()
    return out


def main():
    snap, accounts, agents, perkey_counts, events, paths, extended_flags = load()
    stats = compute(snap, accounts, agents, perkey_counts, events, paths, extended_flags)
    plot_snapshot(stats)
    for stale in (FIGS / "publication-volume.svg", FIGS / "fanout.svg"):
        if stale.exists():
            stale.unlink()
    # Strip private (sortable list) fields before persisting stats.
    persist = {k: v for k, v in stats.items() if not k.startswith("_")}
    (DATA / "stats.json").write_text(json.dumps(persist, indent=2) + "\n")
    print(json.dumps(persist, indent=2))


if __name__ == "__main__":
    main()
