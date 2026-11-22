"""Merge items that describe the same thing (same DOI, or near-identical titles)."""
from __future__ import annotations

import re
import unicodedata

KIND_RANK = {"watch": 0, "official": 1, "publication": 2, "registry": 3, "preprint": 4, "press": 5}
STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "under", "over", "new",
        "des", "les", "une", "pour", "dans", "sur", "del", "los", "las", "para", "com"}


def _tokens(title: str) -> set[str]:
    t = unicodedata.normalize("NFKD", title.lower())
    words = re.findall(r"[a-z0-9぀-鿿가-힯]{3,}", t)
    return {w for w in words if w not in STOP}


def _similar(a: set[str], b: set[str], threshold: float) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def cluster(items: list[dict], threshold: float = 0.6) -> list[dict]:
    """Return one representative per cluster; other members are listed in rep['also']."""
    items = sorted(items, key=lambda it: (KIND_RANK.get(it["kind"], 9), it["tier"]))
    reps: list[dict] = []
    by_doi: dict[str, dict] = {}
    toks: list[set[str]] = []
    for it in items:
        if it.get("doi") and it["doi"] in by_doi:
            _attach(by_doi[it["doi"]], it)
            continue
        t = _tokens(it["title"])
        match = None
        if len(t) >= 4:
            for rep, rt in zip(reps, toks):
                if _similar(t, rt, threshold):
                    match = rep
                    break
        if match:
            _attach(match, it)
            continue
        it.setdefault("also", [])
        reps.append(it)
        toks.append(t)
        if it.get("doi"):
            by_doi[it["doi"]] = it
    return reps


def _attach(rep: dict, other: dict) -> None:
    rep.setdefault("also", [])
    if other["url"] != rep["url"] and all(a["url"] != other["url"] for a in rep["also"]):
        rep["also"].append({"source": other["source"], "url": other["url"],
                            "publisher": other.get("publisher", "")})
    if other.get("watch") and not rep.get("watch"):
        rep["watch"] = other["watch"]
    if len(other.get("snippet", "")) > len(rep.get("snippet", "")) and rep["kind"] == "press":
        rep["snippet"] = other["snippet"]
