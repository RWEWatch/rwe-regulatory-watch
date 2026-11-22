"""Best-effort decoding of Google News article links into the publisher's real URL.

Google News RSS returns opaque links (news.google.com/rss/articles/CBMi...). The
real URL can be obtained through the same internal endpoint the Google News web
page uses. This is undocumented: if anything fails, the original link is kept.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from bs4 import BeautifulSoup

from .collectors import SESSION

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def _article_id(url: str) -> str | None:
    path = urllib.parse.urlsplit(url).path
    m = re.search(r"/(?:rss/)?articles/([^/?]+)", path)
    return m.group(1) if m else None


def decode(url: str, timeout: int = 20) -> str | None:
    if "news.google.com" not in url:
        return url
    art_id = _article_id(url)
    if not art_id:
        return None
    try:
        r = SESSION.get(f"https://news.google.com/rss/articles/{art_id}", timeout=timeout)
        r.raise_for_status()
        node = BeautifulSoup(r.text, "html.parser").select_one("c-wiz > div[jscontroller]")
        if node is None:
            return None
        sig, ts = node.get("data-n-a-sg"), node.get("data-n-a-ts")
        if not sig or not ts:
            return None
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
                 'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                 f'"{art_id}",{ts},"{sig}"]')
        payload = [[["Fbv4je", inner, None, "generic"]]]
        r = SESSION.post(BATCH_URL, timeout=timeout,
                         headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                         data=f"f.req={urllib.parse.quote(json.dumps(payload))}")
        r.raise_for_status()
        chunk = r.text.split("\n\n")[1]
        parsed = json.loads(chunk)[:-2]
        real = json.loads(parsed[0][2])[1]
        return real if isinstance(real, str) and real.startswith("http") else None
    except Exception:  # noqa: BLE001
        return None


def decode_items(items: list[dict], max_items: int = 50) -> int:
    """Replace Google News links by real publisher URLs (in place). Returns the count decoded."""
    n = 0
    for it in items:
        if n >= max_items:
            break
        if "news.google.com" not in it["url"]:
            continue
        real = decode(it["url"])
        if real and real != it["url"]:
            it["gnews_url"] = it["url"]
            it["url"] = real
            n += 1
        time.sleep(1.0)
    return n
