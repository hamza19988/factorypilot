"""Equipment-manual retrieval.

Primary: the Evolus knowledge library (building block #1) when configured.
Fallback: a local BM25 index over data/manuals/*.md so the demo and tests run offline.
Every hit carries a source + page so the diagnosis can cite it and the guardrail can verify citations.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from .config import settings
from .evolus import evolus

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an the and or of to in on at for is are be by with from as it this that must when if than into "
            "use used per any all can not no its their before after during every each".split())


def _tok(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in _STOP or len(t) < 2:
            continue
        for suf in ("ions", "ion", "ing", "ers", "er", "es", "ed", "s", "e"):  # tiny stemmer
            if len(t) > len(suf) + 3 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


@dataclass
class Chunk:
    source: str
    machine_key: str
    page: str
    title: str
    text: str
    tokens: list[str]


@lru_cache(maxsize=1)
def _index() -> tuple[list[Chunk], dict[str, float], float]:
    chunks: list[Chunk] = []
    for path in sorted((settings.data_dir / "manuals").glob("*.md")):
        doc = path.stem
        key = doc.replace("_Manual", "").replace("_", " ").lower()
        for m in re.finditer(r"^## (p\.\d+) — (.+?)\n(.*?)(?=^## |\Z)", path.read_text(), re.S | re.M):
            page, title, body = m.group(1), m.group(2).strip(), " ".join(m.group(3).split())
            chunks.append(Chunk(f"{doc}.pdf {page}", key, page, title, body, _tok(f"{title} {title} {body}")))
    df = Counter(t for c in chunks for t in set(c.tokens))
    n = len(chunks) or 1
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    avg = sum(len(c.tokens) for c in chunks) / n
    return chunks, idf, avg


def _machine_key(machine: str) -> str:
    m = machine.lower()
    for k, v in {"conveyor": "conveyor c2", "press": "press p1", "cnc": "cnc m4", "packag": "packaging l3"}.items():
        if k in m:
            return v
    return m


def search_local(query: str, machine: str = "", top_k: int = 4) -> list[dict]:
    chunks, idf, avg = _index()
    q = _tok(query)
    mk = _machine_key(machine) if machine else ""
    scored = []
    for c in chunks:
        tf = Counter(c.tokens)
        s = 0.0
        for t in q:
            if t in tf:
                f = tf[t]
                s += idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(c.tokens) / avg))
        if mk and c.machine_key == mk:
            s *= 1.6
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    best = top[0][0] if top else 1
    return [{"source": c.source, "title": c.title, "text": c.text, "score": round(s / best, 2),
             "provider": "local BM25 index"} for s, c in top]


def search(query: str, machine: str = "", top_k: int = 4) -> tuple[list[dict], str]:
    """Return (hits, provider). Falls back to the local index if Evolus is not configured or errors."""
    if settings.evolus_library_live:
        try:
            hits = evolus.search_manuals(f"{machine} {query}".strip(), top_k=top_k)
            if hits:
                return hits, "Evolus knowledge library"
        except Exception as e:  # noqa: BLE001 - keep the agent alive; the trace shows the failure
            return search_local(query, machine, top_k), f"local BM25 index (Evolus error: {type(e).__name__})"
    return search_local(query, machine, top_k), "local BM25 index"


def all_sources() -> list[dict]:
    chunks, _, _ = _index()
    return [{"source": c.source, "title": c.title} for c in chunks]
