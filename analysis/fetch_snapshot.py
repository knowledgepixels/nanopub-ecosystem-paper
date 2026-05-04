#!/usr/bin/env python3
"""Pull a snapshot of the live Nanopub Registry into analysis/data/.

Fetched endpoints (registry.knowledgepixels.com):
  /.json                  top-level registry metadata
  /list.json              per-account aggregates (depth, ratio, quota, status)
  /agents.json            per-agent aggregates
  /pubkeys.json           list of all known pubkey hashes
  /debug/trustPaths       newline-separated trust paths (debug endpoint)

The trust-state hash from /.json is recorded alongside the snapshot so that
any analysis is reproducible against a frozen view.
"""

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

BASE = "https://registry.knowledgepixels.com"
DATA_DIR = Path(__file__).parent / "data"

ENDPOINTS = {
    "registry.json": "/.json",
    "list.json": "/list.json",
    "agents.json": "/agents.json",
    "pubkeys.json": "/pubkeys.json",
    "trustPaths.txt": "/debug/trustPaths",
}


def fetch(path: str) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_perkey_counts() -> dict[str, int]:
    """Fetch per-pubkey nanopub counts.

    /list/<pubkey>.json returns one row per (pubkey, type) sub-list; the row
    with type "$" carries the total count for that pubkey in its
    `maxPosition` field.
    """
    accounts = json.loads((DATA_DIR / "list.json").read_text())
    pubkeys = sorted(
        {a["pubkey"] for a in accounts if a.get("status") == "loaded" and a["pubkey"] != "$"}
    )
    counts: dict[str, int] = {}

    def one(pk: str) -> tuple[str, int]:
        rows = json.loads(fetch(f"/list/{pk}.json"))
        total = 0
        for r in rows:
            if r.get("type") != "$":
                continue
            mp = r.get("maxPosition")
            if mp is None:
                continue
            v = mp.get("$numberLong", mp) if isinstance(mp, dict) else mp
            # maxPosition is the highest assigned index; total count = mp + 1.
            total = int(v) + 1
            break
        return pk, total

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(one, pk) for pk in pubkeys]
        for i, f in enumerate(as_completed(futures), 1):
            pk, total = f.result()
            counts[pk] = total
            if i % 50 == 0 or i == len(pubkeys):
                print(f"  perkey counts: {i}/{len(pubkeys)}")
    return counts


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    for name, path in ENDPOINTS.items():
        body = fetch(path)
        (DATA_DIR / name).write_bytes(body)
        print(f"  {name:20s} {len(body):>10d} bytes")
    perkey = fetch_perkey_counts()
    (DATA_DIR / "perkey_counts.json").write_text(json.dumps(perkey, indent=2) + "\n")
    print(f"  perkey_counts.json   {len(perkey)} pubkeys, "
          f"sum={sum(perkey.values())} nanopubs")
    meta = json.loads((DATA_DIR / "registry.json").read_text())
    snapshot = {
        "fetched_at": fetched_at,
        "registry_url": BASE,
        "trust_state_hash": meta.get("trustStateHash"),
        "trust_state_counter": meta.get("trustStateCounter"),
        "agent_count": meta.get("agentCount"),
        "account_count": meta.get("accountCount"),
        "nanopub_count": meta.get("nanopubCount"),
        "registry_version": meta.get("registryVersion"),
    }
    (DATA_DIR / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
