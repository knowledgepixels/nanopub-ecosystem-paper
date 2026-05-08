#!/usr/bin/env python3
"""Generate simulation-study figures.

Source data: analysis/network_simulation_results/Stats checkpoints.xlsx
(round-robin = experiment 1, type-sharded = experiment 2). Values are
inlined here so the script runs from a clean checkout.

Output:
  analysis/figures/simulation-results.svg
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

FIGS = Path(__file__).parent / "figures"
FIGS.mkdir(exist_ok=True)

PHASES = [1, 2, 3, 4]
NAN = float("nan")

# Cluster-wide ingestion throughput (nanopublications per second).
# Round-robin: every Registry replicates every nanopublication, so the
# per-Registry processing rate equals the cluster rate.
rr_cluster = [17.30, 16.71, 14.43, 12.52]
rr_per_registry = list(rr_cluster)
# Type-sharded: each Registry covers half the type space, so the
# per-Registry rate is ~17 np/s while cluster throughput is ~1.5x that.
# Phases 3 and 4 are pending and rendered as missing data.
ts_per_registry = [17.83, 16.55, 14.58, 13.08]
ts_cluster = [26.50, 24.59, 21.60, 19.43]

# Average CPU usage (% of one core) per component during ingestion,
# averaged across the four instances of each component.
# Pulled from analysis/network_simulation_results/Stats checkpoints.xlsx,
# sheets "experiment 1" (round-robin) and "experiment 2" (type-sharded).
rr_cpu_registry = [20.34, 20.32, 19.62, 18.25]
rr_cpu_query    = [9.91, 9.22, 8.83, 7.83]
rr_cpu_rdf4j    = [46.13, 41.54, 48.33, 51.25]
rr_cpu_mongo    = [58.04, 72.69, 87.00, 92.40]
ts_cpu_registry = [21.70, 22.12, 19.95, 20.48]
ts_cpu_query    = [8.63, 9.11, 8.42, 7.36]
ts_cpu_rdf4j    = [31.20, 37.23, 35.98, 25.40]
ts_cpu_mongo    = [65.62, 70.03, 76.95, 89.55]

# Average RAM reserved per component (GiB) during ingestion, averaged
# across the four instances of each component.
rr_ram_registry = [1.99, 2.03, 1.58, 1.64]
rr_ram_query    = [0.61, 0.78, 0.35, 0.35]
rr_ram_rdf4j    = [6.39, 4.24, 4.76, 4.89]
rr_ram_mongo    = [1.44, 1.78, 2.14, 2.46]
ts_ram_registry = [1.60, 1.61, 1.76, 0.59]
ts_ram_query    = [0.69, 0.82, 0.34, 0.63]
ts_ram_rdf4j    = [5.37, 6.86, 3.14, 5.40]
ts_ram_mongo    = [1.44, 1.52, 1.80, 1.77]

# Query workload: 20 minutes (1200 s), 24 concurrent clients.
# `ok` is the count of successful queries; failures and timeouts are
# zero in both runs and not plotted.
QUERY_SECONDS = 20 * 60
rr_query_ok       = [5_713_992, 7_066_372, 4_995_692, 6_969_774]
rr_query_lat_us   = [4272, 3251, 5049, 3325]
ts_query_ok       = [8_521_250, 8_215_237, 8_197_059, 8_180_749]
ts_query_lat_us   = [2132, 2345, 2302, 2329]

rr_qps      = [n / QUERY_SECONDS for n in rr_query_ok]   # queries / s
ts_qps      = [n / QUERY_SECONDS for n in ts_query_ok]
rr_lat_ms   = [u / 1000.0 for u in rr_query_lat_us]      # microseconds → ms
ts_lat_ms   = [u / 1000.0 for u in ts_query_lat_us]


def _style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.6, alpha=0.6)


def plot_results():
    """Combined figure: throughput, CPU, and RAM in three side-by-side panels.

    Panels (b) and (c) sit in the top of the right area; the shared legend
    sits below them, and (a) takes the full figure height on the left, so
    that (b) + (c) + legend together align with (a).
    """
    plt.rcParams.update({
        "font.size":       11,
        "axes.titlesize":  11,
        "axes.labelsize":  11,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
    })

    fig = plt.figure(figsize=(11.0, 4.0))
    # Explicit positions: (a) is split into two stacked subpanels on the
    # left sharing the phase axis — (a-i) ingestion throughput on top,
    # (a-ii) query throughput below with a twin axis for mean latency.
    # (b) and (c) sit on the upper right; the empty band below them is
    # reserved for the shared legend so that (b) + (c) + legend together
    # align with (a)'s total height.
    ax_ing = fig.add_axes((0.07, 0.55, 0.40, 0.37))
    ax_qry = fig.add_axes((0.07, 0.14, 0.40, 0.37))
    ax_cpu = fig.add_axes((0.60, 0.30, 0.16, 0.62))
    ax_ram = fig.add_axes((0.82, 0.30, 0.16, 0.62))

    x_phase = np.array(PHASES)

    # ----- (a-i) Ingestion throughput -----
    ax_ing.plot(x_phase, rr_per_registry, marker="o", color="#1f77b4",
                label="Round-robin per-Registry (= cluster)")
    ax_ing.plot(x_phase, ts_per_registry, marker="s", color="#2ca02c",
                label="Type-sharded per-Registry")
    ax_ing.plot(x_phase, ts_cluster, marker="^", color="#2ca02c", linestyle="--",
                label="Type-sharded cluster")
    ax_ing.set_xticks(PHASES)
    ax_ing.set_xticklabels([])
    ax_ing.set_ylabel("Ingestion (np/s)")
    ax_ing.set_ylim(0, 30)
    ax_ing.set_title("(a) Throughput")
    ax_ing.legend(loc="lower left", frameon=False, fontsize=8)
    _style(ax_ing)

    # ----- (a-ii) Query throughput + mean latency -----
    ax_qry.plot(x_phase, rr_qps, marker="o", color="#1f77b4",
                label="Round-robin")
    ax_qry.plot(x_phase, ts_qps, marker="s", color="#2ca02c",
                label="Type-sharded")
    ax_qry.set_xticks(PHASES)
    ax_qry.set_xlabel("Phase (each adds 100k nanopublications)")
    ax_qry.set_ylabel("Queries/s")
    ax_qry.set_ylim(0, 9500)
    _style(ax_qry)

    ax_lat = ax_qry.twinx()
    ax_lat.plot(x_phase, rr_lat_ms, marker="o", color="#1f77b4",
                linestyle=":", linewidth=1.0, alpha=0.55, markersize=4,
                label="Round-robin latency")
    ax_lat.plot(x_phase, ts_lat_ms, marker="s", color="#2ca02c",
                linestyle=":", linewidth=1.0, alpha=0.55, markersize=4,
                label="Type-sharded latency")
    ax_lat.set_ylabel("Mean latency (ms)", fontsize=9)
    ax_lat.set_ylim(0, 7)
    ax_lat.spines["top"].set_visible(False)

    # Two-column legend at lower-left: column 1 = solid (throughput),
    # column 2 = dotted (mean latency, right axis). matplotlib fills
    # column-major top-to-bottom, so order handles as [col1-top,
    # col1-bottom, col2-top, col2-bottom].
    from matplotlib.lines import Line2D
    qry_handles = [
        Line2D([0], [0], color="#1f77b4", marker="o",
               label="Round-robin queries/s"),
        Line2D([0], [0], color="#2ca02c", marker="s",
               label="Type-sharded queries/s"),
        Line2D([0], [0], color="#1f77b4", marker="o", linestyle=":",
               linewidth=1.0, alpha=0.55, markersize=4,
               label="Round-robin latency"),
        Line2D([0], [0], color="#2ca02c", marker="s", linestyle=":",
               linewidth=1.0, alpha=0.55, markersize=4,
               label="Type-sharded latency"),
    ]
    ax_qry.legend(handles=qry_handles, loc="lower left", ncol=2,
                  frameon=False, fontsize=8, columnspacing=1.2,
                  handlelength=1.6)

    # ----- Stacked-bar helpers, shared by (b) and (c) -----
    groups = ["Nanopub\nRegistry", "Nanopub\nQuery"]
    x = np.arange(len(groups))
    width = 0.36

    def avg(s): return sum(s) / len(s)

    c_app   = ["#1f77b4", "#2ca02c"]   # Registry app, Query app
    c_store = ["#d62728", "#9467bd"]   # MongoDB,      RDF4J
    HATCH   = "//"

    def stacked(ax, app_vals, store_vals, offset, hatched):
        for i, gx in enumerate(x):
            kw = dict(width=width, edgecolor="white" if hatched else "none")
            if hatched:
                kw["hatch"] = HATCH
            ax.bar(gx + offset, app_vals[i],   color=c_app[i],   **kw)
            ax.bar(gx + offset, store_vals[i], color=c_store[i],
                   bottom=app_vals[i], **kw)

    # ----- (b) CPU during ingestion -----
    stacked(ax_cpu,
            [avg(rr_cpu_registry), avg(rr_cpu_query)],
            [avg(rr_cpu_mongo),    avg(rr_cpu_rdf4j)],
            -width / 2, hatched=False)
    stacked(ax_cpu,
            [avg(ts_cpu_registry), avg(ts_cpu_query)],
            [avg(ts_cpu_mongo),    avg(ts_cpu_rdf4j)],
            +width / 2, hatched=True)
    ax_cpu.set_xticks(x)
    ax_cpu.set_xticklabels(groups, fontsize=9)
    ax_cpu.set_ylabel("CPU (% of one core)")
    ax_cpu.set_ylim(0, 110)
    ax_cpu.set_title("(b) CPU during ingestion")
    _style(ax_cpu)

    # ----- (c) RAM during ingestion -----
    stacked(ax_ram,
            [avg(rr_ram_registry), avg(rr_ram_query)],
            [avg(rr_ram_mongo),    avg(rr_ram_rdf4j)],
            -width / 2, hatched=False)
    stacked(ax_ram,
            [avg(ts_ram_registry), avg(ts_ram_query)],
            [avg(ts_ram_mongo),    avg(ts_ram_rdf4j)],
            +width / 2, hatched=True)
    ax_ram.set_xticks(x)
    ax_ram.set_xticklabels(groups, fontsize=9)
    ax_ram.set_ylabel("RAM (GiB)")
    ax_ram.set_ylim(0, 7)
    ax_ram.set_title("(c) RAM during ingestion")
    _style(ax_ram)

    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor=c_app[0],   label="Registry app"),
        Patch(facecolor=c_store[0], label="MongoDB"),
        Patch(facecolor=c_app[1],   label="Query app"),
        Patch(facecolor=c_store[1], label="RDF4J"),
        Patch(facecolor="#cccccc",  edgecolor="none",  label="Round-robin"),
        Patch(facecolor="#cccccc",  edgecolor="white", hatch=HATCH, label="Type-sharded"),
    ]

    # Legend sits below (b) and (c) (after their xtick labels), centred
    # horizontally across them.
    pos_b = ax_cpu.get_position()
    pos_c = ax_ram.get_position()
    cx = (pos_b.x0 + pos_c.x1) / 2
    fig.legend(handles=handles, loc="center",
               bbox_to_anchor=(cx, 0.10), bbox_transform=fig.transFigure,
               ncol=3, frameon=True, framealpha=0.92,
               handlelength=1.6, handleheight=1.0, columnspacing=1.4,
               borderpad=0.4, borderaxespad=0.0)
    fig.savefig(FIGS / "simulation-results.svg")
    plt.close(fig)


if __name__ == "__main__":
    plot_results()
    print(f"Wrote SVGs to {FIGS}")
