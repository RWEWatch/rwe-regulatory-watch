"""Memory of already-seen links (data/seen.json), versioned in the repository."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class SeenStore:
    def __init__(self, path: Path, retention_days: int = 240):
        self.path = path
        self.retention_days = retention_days
        try:
            self.data: dict[str, dict[str, str]] = json.loads(path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def is_initialised(self, source_id: str) -> bool:
        return source_id in self.data

    def is_seen(self, source_id: str, key: str) -> bool:
        return key in self.data.get(source_id, {})

    def seen_anywhere(self, key: str) -> bool:
        return any(key in d for d in self.data.values())

    def add(self, source_id: str, keys, today: dt.date) -> None:
        bucket = self.data.setdefault(source_id, {})
        for k in keys:
            bucket.setdefault(k, today.isoformat())

    def prune(self, today: dt.date) -> None:
        limit = (today - dt.timedelta(days=self.retention_days)).isoformat()
        for sid, bucket in self.data.items():
            self.data[sid] = {k: v for k, v in bucket.items() if v >= limit}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=0, sort_keys=True), "utf-8")
