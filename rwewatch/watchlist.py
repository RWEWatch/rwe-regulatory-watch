"""Watchlist: documents/dossiers you want to track over time.

Two mechanisms (both optional per entry):
  - keywords: any collected item matching the regex is flagged "WATCHLIST MATCH";
  - url: the page is fingerprinted each run; a change produces a 'watch' item.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from . import collectors


def flag_matches(items: list[dict], entries: list[dict]) -> int:
    compiled = [(e["name"], re.compile(e["keywords"], re.I)) for e in entries if e.get("keywords")]
    n = 0
    for it in items:
        text = f"{it['title']} {it.get('snippet', '')}"
        for name, rx in compiled:
            if rx.search(text):
                it["watch"] = name
                n += 1
                break
    return n


def check_pages(entries: list[dict], state_path: Path, today: dt.date, save: bool, log) -> tuple[list[dict], list[dict]]:
    """Returns (change_items, statuses)."""
    try:
        state = json.loads(state_path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    src = {"id": "watchlist", "name": "Watchlist", "region": "Watchlist", "tier": 1, "type": "page"}
    changes, statuses = [], []
    for e in entries:
        url = e.get("url")
        if not url:
            continue
        st = {"name": f"Watchlist — {e['name']}", "region": "Watchlist", "tier": 1, "new": 0,
              "status": "ok", "error": ""}
        try:
            fp = collectors.page_fingerprint(url)
        except Exception as ex:  # noqa: BLE001
            st.update(status="error", error=type(ex).__name__)
            statuses.append(st)
            continue
        prev = state.get(url)
        if prev is None:
            st["status"] = "initialised"
        elif prev["fp"] != fp:
            changes.append(collectors.make_item(
                src, f"Tracked page updated: {e['name']}", url, today,
                f"The monitored page changed since {prev['date']}. {e.get('note', '')}",
                kind="watch", watch=e["name"]))
            st["new"] = 1
        state[url] = {"fp": fp, "date": today.isoformat() if prev is None or prev["fp"] != fp else prev["date"]}
        statuses.append(st)
    if save:
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True), "utf-8")
    log(f"  watchlist: {len(changes)} tracked page(s) changed")
    return changes, statuses
