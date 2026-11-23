"""Anti-hallucination check: every link in the AI-written report must point to a
URL that was actually collected. Unknown links are removed and counted."""
from __future__ import annotations

import re

from .collectors import norm_url

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE = re.compile(r"(?<![(\[<])\bhttps?://[^\s)>\]]+")


def allowed_urls(items: list[dict]) -> set[str]:
    urls = set()
    for it in items:
        for u in (it.get("url"), it.get("gnews_url"), it.get("primary_search")):
            if u:
                urls.add(norm_url(u))
        for a in it.get("also", []):
            urls.add(norm_url(a["url"]))
        for u in it.get("primary_links", []):
            urls.add(norm_url(u))
    return urls


def check_links(markdown_text: str, allowed: set[str]) -> tuple[str, int, int]:
    """Returns (clean_text, n_links_ok, n_links_removed)."""
    ok = removed = 0

    def repl(m: re.Match) -> str:
        nonlocal ok, removed
        if norm_url(m.group(2)) in allowed:
            ok += 1
            return m.group(0)
        removed += 1
        return f"{m.group(1)} *(link removed: not among collected sources)*"

    text = LINK.sub(repl, markdown_text)

    def bare(m: re.Match) -> str:
        nonlocal removed
        url = m.group(0).rstrip(".,;")
        if norm_url(url) in allowed:
            return f"[link]({url})"
        removed += 1
        return "*(link removed)*"

    # bare URLs outside markdown links
    parts = re.split(r"(\[[^\]]+\]\([^)]+\))", text)
    text = "".join(p if p.startswith("[") else BARE.sub(bare, p) for p in parts)
    return text, ok, removed
