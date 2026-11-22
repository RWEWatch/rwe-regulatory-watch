"""Source health: counts consecutive failures to raise alerts on major sources."""
from __future__ import annotations

import json
from pathlib import Path


def update(path: Path, statuses: list[dict], save: bool) -> dict[str, int]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    for s in statuses:
        if s["status"] == "error":
            data[s["name"]] = data.get(s["name"], 0) + 1
        else:
            data.pop(s["name"], None)
    if save:
        path.write_text(json.dumps(data, indent=1, sort_keys=True), "utf-8")
    return data


def alerts(health: dict[str, int], statuses: list[dict], min_runs: int = 2) -> list[str]:
    tier1 = {s["name"] for s in statuses if s["tier"] == 1}
    return [f"{name} — unreachable for {n} consecutive runs"
            for name, n in sorted(health.items()) if n >= min_runs and name in tier1]
