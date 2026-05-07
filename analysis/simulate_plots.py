#!/usr/bin/env python3
"""Generate preliminary simulation-study figures from the partial run.

Source data: simulation-result-files-temp/Stats checkpoints.xlsx
(round-robin = experiment 1, type-sharded = experiment 2). Values are
inlined here so the script runs from a clean checkout. Re-export and
update the constants once phases 3 and 4 of the type-sharded run
complete, then rerun this script to regenerate the SVGs.

Outputs:
  analysis/figures/simulation-throughput.svg
  analysis/figures/simulation-latency.svg
  analysis/figures/simulation-resources-ingestion.svg
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
ts_per_registry = [17.83, 16.55, NAN, NAN]
ts_cluster = [26.50, 24.59, NAN, NAN]

# Average query response time during the 20-min, 24-client workload (ms).
rr_latency_ms = [4.27, 3.25, 5.05, 3.33]
ts_latency_ms = [2.13, 2.35, NAN, NAN]

# Average CPU usage (% of one core) per component during ingestion,
# averaged across the four instances of each component, round-robin run.
# Pulled from the "Stats checkpoints" workbook, sheet "experiment 1".
rr_cpu_registry = [20.34, 20.32, 19.62, 18.25]
rr_cpu_query = [9.91, 9.22, 8.83, 7.83]
rr_cpu_rdf4j = [46.13, 41.54, 48.32, 51.25]
rr_cpu_mongo = [58.04, 72.69, 87.00, 92.40]

# Average RAM reserved per component (GiB) during ingestion, averaged
# across the four instances of each component, round-robin run.
rr_ram_registry = [1.99, 2.03, 1.58, 1.64]
rr_ram_query = [0.61, 0.78, 0.35, 0.35]
rr_ram_rdf4j = [6.39, 4.24, 4.76, 4.89]
rr_ram_mongo = [1.44, 1.78, 2.14, 2.46]


def _style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.6, alpha=0.6)


def plot_throughput():
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    x = np.array(PHASES)
    ax.plot(x, rr_per_registry, marker="o", color="#1f77b4",
            label="Round-robin, per-Registry (= cluster)")
    ax.plot(x, ts_per_registry, marker="s", color="#2ca02c",
            label="Type-sharded, per-Registry")
    ax.plot(x, ts_cluster, marker="^", color="#2ca02c", linestyle="--",
            label="Type-sharded, cluster")
    ax.set_xticks(PHASES)
    ax.set_xlabel("Phase (each adds 100k nanopublications)")
    ax.set_ylabel("Ingestion throughput (np/s)")
    ax.set_ylim(0, 30)
    ax.legend(loc="lower left", frameon=False, fontsize=9)
    _style(ax)
    fig.tight_layout()
    fig.savefig(FIGS / "simulation-throughput.svg")
    plt.close(fig)


def plot_latency():
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    x = np.arange(len(PHASES))
    width = 0.36
    ax.bar(x - width / 2, rr_latency_ms, width, color="#1f77b4",
           label="Round-robin")
    ax.bar(x + width / 2,
           [v if not np.isnan(v) else 0 for v in ts_latency_ms],
           width, color="#2ca02c", label="Type-sharded")
    # Mark the missing type-sharded phases explicitly.
    for i, v in enumerate(ts_latency_ms):
        if np.isnan(v):
            ax.text(x[i] + width / 2, 0.1, "n/a", ha="center", va="bottom",
                    fontsize=8, color="#555")
    ax.set_xticks(x)
    ax.set_xticklabels([str(p) for p in PHASES])
    ax.set_xlabel("Phase (each adds 100k nanopublications)")
    ax.set_ylabel("Avg query latency (ms)")
    ax.set_ylim(0, 6)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    _style(ax)
    fig.tight_layout()
    fig.savefig(FIGS / "simulation-latency.svg")
    plt.close(fig)


def plot_resources_ingestion():
    fig, (ax_cpu, ax_ram) = plt.subplots(1, 2, figsize=(10.0, 3.6))
    x = np.array(PHASES)

    ax_cpu.plot(x, rr_cpu_mongo, marker="o", color="#d62728", label="MongoDB")
    ax_cpu.plot(x, rr_cpu_rdf4j, marker="s", color="#9467bd", label="RDF4J")
    ax_cpu.plot(x, rr_cpu_registry, marker="^", color="#1f77b4", label="Registry")
    ax_cpu.plot(x, rr_cpu_query, marker="v", color="#2ca02c", label="Query")
    ax_cpu.set_xticks(PHASES)
    ax_cpu.set_xlabel("Phase (each adds 100k nanopublications)")
    ax_cpu.set_ylabel("Avg CPU usage (% of one core)")
    ax_cpu.set_ylim(0, 100)
    ax_cpu.set_title("(a) CPU", fontsize=10)
    _style(ax_cpu)

    ax_ram.plot(x, rr_ram_rdf4j, marker="s", color="#9467bd", label="RDF4J")
    ax_ram.plot(x, rr_ram_mongo, marker="o", color="#d62728", label="MongoDB")
    ax_ram.plot(x, rr_ram_registry, marker="^", color="#1f77b4", label="Registry")
    ax_ram.plot(x, rr_ram_query, marker="v", color="#2ca02c", label="Query")
    ax_ram.set_xticks(PHASES)
    ax_ram.set_xlabel("Phase (each adds 100k nanopublications)")
    ax_ram.set_ylabel("Avg RAM reserved (GiB)")
    ax_ram.set_ylim(0, 7)
    ax_ram.set_title("(b) RAM", fontsize=10)
    _style(ax_ram)

    handles, labels = ax_cpu.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4,
               frameon=False, fontsize=9, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIGS / "simulation-resources-ingestion.svg")
    plt.close(fig)


if __name__ == "__main__":
    plot_throughput()
    plot_latency()
    plot_resources_ingestion()
    print(f"Wrote SVGs to {FIGS}")
