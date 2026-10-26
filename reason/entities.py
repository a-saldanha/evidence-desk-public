"""Layer 3a: entity resolution.

Surfaces come from three places: email headers (name + address), chat sender labels, and LLM mentions /
event participants. Resolution is deliberately conservative:
  * auto-merge only on hard evidence: a shared identifier (canonical email, phone) or the configured SELF aliases;
  * everything else that looks similar becomes a *proposal* with a score and a reason, for a human to accept
    or reject. Decisions are saved in decisions.json and re-applied on every run, so the human only answers once.
Saved-contact labels like "IIT Mandi PhD? ICIP Dhanunjaya" are common in chats; rare-token matching with
document-frequency weighting lets "Dhanunjay Sir" meet "Dhanunjaya" without merging everyone called "IIT".
"""
from __future__ import annotations
import json, math, re
from collections import Counter, defaultdict
from pathlib import Path
from rapidfuzz import fuzz
from ingest.models import stable_id
from ingest.normalize import canonical_email, is_relay

HONORIFICS = {"sir", "maam", "ma'am", "madam", "mam", "mr", "mrs", "ms", "dr", "prof", "professor", "bro", "ji",
              "the", "and", "of", "from", "friend", "mrs.", "mr.", "dr."}


def tokens(name: str) -> list[str]:
    name = (name or "").lower().replace("’", "'")
    if "@" in name:                      # an address used as a name: only the local part says anything about the person
        name = re.sub(r"[._\d]+", " ", name.split("@", 1)[0])
    return [t for t in re.findall(r"[a-z][a-z']+", name) if t not in HONORIFICS and len(t) > 1]


def clean_name(name: str) -> str:
    from ingest.normalize import clean_text
    return re.sub(r"\s+", " ", clean_text(name or "")).strip()


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def collect_surfaces(units: list[dict], mentions: list[dict], events) -> dict[str, dict]:
    """surface key -> {name, type, identifiers, units, sources}"""
    S: dict[str, dict] = {}
    def add(name, etype, ident=None, unit_id=None, origin=""):
        name = clean_name(name)
        if not name and not ident:
            return None
        key = (name.lower() if name else ident).strip()
        s = S.setdefault(key, {"name": name or ident, "type": etype, "identifiers": set(), "units": set(), "origins": set()})
        if ident:
            s["identifiers"].add(ident)
        if unit_id:
            s["units"].add(unit_id)
        s["origins"].add(origin)
        return key
    for u in units:
        if "system_message" in u.get("flags", []):
            continue                     # in group chats the "sender" of system lines is the group's name
        people = ([u["author"]] if u.get("author") else []) + u.get("recipients", [])
        for p in people:
            if p.get("address") and is_relay(p["address"]):
                continue
            add(p.get("display") or p.get("address"), "person",
                p.get("canonical") or (canonical_email(p["address"]) if p.get("address") else None), u["unit_id"],
                "chat_label" if u["unit_type"].startswith("chat") else "email_header")
    for m in mentions:
        for ident in m.get("identifiers") or [None]:
            ident = canonical_email(ident) if ident and "@" in ident else ident
            add(m["name"], m.get("type", "other"), ident, None, "llm_mention")
    for e in events:
        for p in e.participants:
            if p and p.upper() != "SELF":
                add(p, "person", None, None, "participant")
    for s in S.values():
        s["identifiers"] = {i for i in s["identifiers"] if i}
    return S


def resolve_entities(surfaces: dict[str, dict], self_cfg: dict, decisions_path: Path):
    decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    uf = UnionFind()
    keys = list(surfaces)
    for k in keys:
        uf.find(k)
    self_key = "__self__"
    uf.find(self_key)
    self_names = {n.lower() for n in [self_cfg.get("name", "")] + list(self_cfg.get("aliases", [])) if n}
    self_addrs = {canonical_email(a) for a in self_cfg.get("addresses", [])}

    # 1. hard merges
    by_ident = defaultdict(list)
    for k, s in surfaces.items():
        for i in s["identifiers"]:
            by_ident[i].append(k)
        if s["name"].lower() in self_names or s["identifiers"] & self_addrs:
            uf.union(k, self_key)
    for i, ks in by_ident.items():
        for k in ks[1:]:
            uf.union(ks[0], k)

    # 2. proposals from rare shared tokens
    df = Counter(t for k in keys for t in set(tokens(surfaces[k]["name"])))
    proposals = []
    index = defaultdict(list)
    for k in keys:
        if surfaces[k]["type"] in ("person", "organization"):
            for t in set(tokens(surfaces[k]["name"])):
                index[t[:5]].append((t, k))
    seen = set()
    for bucket in index.values():
        for i, (ta, a) in enumerate(bucket):
            for tb, b in bucket[i + 1:]:
                if a == b or uf.find(a) == uf.find(b) or (a, b) in seen or len(ta) < 4 or len(tb) < 4:
                    continue
                if surfaces[a]["type"] != surfaces[b]["type"]:
                    continue
                tok_sim = fuzz.ratio(ta, tb)
                if tok_sim < 85 or max(df[ta], df[tb]) > 6:
                    continue
                seen.add((a, b)); seen.add((b, a))
                A, B = set(tokens(surfaces[a]["name"])), set(tokens(surfaces[b]["name"]))
                idf = lambda t: math.log(1 + len(keys) / (1 + df[t]))
                shared = sum(max(idf(x), idf(y)) for x in A for y in B if fuzz.ratio(x, y) >= 85)
                union = sum(idf(t) for t in A | B)
                overlap = min(shared / union, 1.0) if union else 0
                name_sim = fuzz.token_set_ratio(" ".join(A), " ".join(B))
                score = round(0.6 * overlap + 0.4 * tok_sim / 100, 3)
                if score < 0.5:
                    continue
                pair = "|".join(sorted([a, b]))
                proposals.append({"a": surfaces[a]["name"], "b": surfaces[b]["name"], "key": pair, "score": score,
                                  "reason": f"shared name token '{ta}'~'{tb}' (similarity {tok_sim:.0f}); "
                                            f"full-name similarity {name_sim:.0f}",
                                  "status": decisions.get(pair, "pending")})
    for p in proposals:
        if p["status"] == "accepted":
            a, b = p["key"].split("|", 1)
            uf.union(a, b)

    # 3. build clusters
    clusters = defaultdict(list)
    for k in keys:
        clusters[uf.find(k)].append(k)
    entities, surface_to_entity = [], {}
    self_root = uf.find(self_key)
    for root, ks in clusters.items():
        is_self = root == self_root
        names = [surfaces[k]["name"] for k in ks]
        best = max(names, key=lambda n: (sum(1 for x in names if x == n), -len(re.findall(r"\d|@", n)), len(n)))
        if is_self and self_cfg.get("name"):
            best = self_cfg["name"]
        eid = "self" if is_self else stable_id(*sorted(ks))
        types = Counter(surfaces[k]["type"] for k in ks)
        entities.append({"entity_id": eid, "type": types.most_common(1)[0][0], "name": best, "is_self": is_self,
                         "aliases": sorted(set(names)), "identifiers": sorted(set().union(*[surfaces[k]["identifiers"] for k in ks]))})
        for k in ks:
            surface_to_entity[k] = eid
    if not any(e["is_self"] for e in entities):
        entities.append({"entity_id": "self", "type": "person", "name": self_cfg.get("name", "SELF"), "is_self": True,
                         "aliases": list(self_names), "identifiers": sorted(self_addrs)})
    surface_to_entity["self"] = "self"
    return entities, surface_to_entity, proposals


def save_decision(decisions_path: Path, pair_key: str, status: str):
    d = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    d[pair_key] = status
    decisions_path.write_text(json.dumps(d, indent=1))
