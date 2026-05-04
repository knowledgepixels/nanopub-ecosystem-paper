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
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

BASE = "https://registry.knowledgepixels.com"
DATA_DIR = Path(__file__).parent / "data"

# SHA-256 hashes of the canonical type IRIs (see NanopubLoader.java in the
# Nanopub Registry source: INTRO_TYPE = npx:declaredBy, ENDORSE_TYPE =
# npx:approvesOf).
INTRO_TYPE_HASH = "77757cabf6184c51c20b8b0fe5dc5e1365b7f628448335184ad54319a0affdfc"
ENDORSE_TYPE_HASH = "4a9a4dc2fa939033dd444d4f630fec3450eee305d98dd9945f110b2a6bb51317"

# Older nanopubs may use prov:generatedAtTime instead of dct/dcterms:created,
# and some use auto-assigned prefixes (ns1:created, etc.) for the dcterms
# namespace. Match any prefixed `:created` literal as well as the full IRI.
CREATED_RE = re.compile(
    r'(?:'
    r'\b\w+:created'
    r'|<http://purl\.org/dc/terms/created>'
    r'|\bprov:generatedAtTime'
    r')\s+"([^"]+)"\^\^xsd:dateTime'
)

# Locate the assertion graph block by name (the local URI ends with /assertion).
ASSERTION_BLOCK_RE = re.compile(r":assertion\s*\{(.*?)\}", re.DOTALL)
# Within the assertion block, find each `approvesOf` predicate followed by
# its first object.  The object is either an angle-bracketed full URI
# (which can contain dots) or a prefixed name / colon-prefixed local name
# (a non-whitespace token).  This intentionally captures only the first
# object — endorsement nanopublications in practice carry one target each,
# and one match is enough to classify the nanopub as a genuine endorsement.
APPROVES_OF_RE = re.compile(
    r'(?:\b\w+:approvesOf|<http://purl\.org/nanopub/x/approvesOf>)\s+'
    r'(<[^>]+>|\S+)'
)
# Trusty URI artifact codes are 45-character base64url-ish strings starting RA
# (RA + 43 hash chars).
ARTIFACT_CODE_RE = re.compile(r'\bRA[A-Za-z0-9_-]{43}\b')


def extract_approves_of(trig: str) -> list[str]:
    """Return the list of artifact codes referenced as approvesOf objects
    inside the nanopub's assertion graph."""
    out: list[str] = []
    seen: set[str] = set()
    for m_block in ASSERTION_BLOCK_RE.finditer(trig):
        block = m_block.group(1)
        for m in APPROVES_OF_RE.finditer(block):
            objects = m.group(1)
            for ac_match in ARTIFACT_CODE_RE.finditer(objects):
                ac = ac_match.group(0)
                if ac not in seen:
                    seen.add(ac)
                    out.append(ac)
    return out

ENDPOINTS = {
    "registry.json": "/.json",
    "list.json": "/list.json",
    "agents.json": "/agents.json",
    "pubkeys.json": "/pubkeys.json",
    "trustPaths.txt": "/debug/trustPaths",
}


def fetch(path: str, accept: str = "*/*", retries: int = 3) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"Accept": accept})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1}/{retries} for {path}: {e}")


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


def fetch_event_timestamps() -> dict[str, list[dict]]:
    """Pull dct:created for every introduction and endorsement nanopub.

    Two passes: first collect nanopub IDs by enumerating
    /list/<pubkey>/<typeHash>.json for both core types across all loaded
    pubkeys; then fetch each nanopub once via /np/<id> in TriG and regex out
    the dct:created timestamp from the publication-info graph.
    """
    accounts = json.loads((DATA_DIR / "list.json").read_text())
    pubkeys = sorted(
        {a["pubkey"] for a in accounts if a.get("status") == "loaded" and a["pubkey"] != "$"}
    )

    def list_ids(pk: str, type_hash: str) -> list[tuple[str, str, bool]]:
        try:
            rows = json.loads(fetch(f"/list/{pk}/{type_hash}.json"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise
        return [(pk, r["np"], bool(r.get("invalidated", False))) for r in rows]

    intros: list[tuple[str, str, bool]] = []
    endors: list[tuple[str, str, bool]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        intro_futs = {ex.submit(list_ids, pk, INTRO_TYPE_HASH): pk for pk in pubkeys}
        endor_futs = {ex.submit(list_ids, pk, ENDORSE_TYPE_HASH): pk for pk in pubkeys}
        for f in as_completed(intro_futs):
            intros.extend(f.result())
        for f in as_completed(endor_futs):
            endors.extend(f.result())
    print(f"  collected {len(intros)} intros and {len(endors)} endorsements")

    cache: dict[str, tuple[str | None, list[str]]] = {}

    def facts_for(np_id: str) -> tuple[str | None, list[str]]:
        if np_id in cache:
            return cache[np_id]
        try:
            body = fetch(f"/np/{np_id}", accept="application/trig").decode("utf-8", "replace")
        except Exception as e:
            print(f"  warning: fetch failed for {np_id}: {e}")
            cache[np_id] = (None, [])
            return cache[np_id]
        m = CREATED_RE.search(body)
        ts = m.group(1) if m else None
        approves = extract_approves_of(body)
        cache[np_id] = (ts, approves)
        return cache[np_id]

    out: dict[str, list[dict]] = {"introductions": [], "endorsements": []}
    work = [("introductions", intros), ("endorsements", endors)]
    for label, items in work:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(facts_for, np): (pk, np, inv) for pk, np, inv in items}
            for i, f in enumerate(as_completed(futs), 1):
                pk, np, inv = futs[f]
                ts, approves = f.result()
                rec = {"pubkey": pk, "np": np, "created": ts, "invalidated": inv}
                if label == "endorsements":
                    rec["approves"] = approves
                out[label].append(rec)
                if i % 200 == 0 or i == len(items):
                    print(f"  {label} timestamps: {i}/{len(items)}")
    return out


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
    events = fetch_event_timestamps()
    (DATA_DIR / "event_timestamps.json").write_text(json.dumps(events, indent=2) + "\n")
    print(f"  event_timestamps.json   "
          f"{len(events['introductions'])} intros, "
          f"{len(events['endorsements'])} endorsements")
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
