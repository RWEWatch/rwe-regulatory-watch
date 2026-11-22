"""LLM calls: Gemini (free tier, default) or Anthropic Claude (paid, optional).

Two stages:
  1. triage()  — fast model scores every candidate item 0-10 (JSON output);
  2. write()   — stronger model writes the report on the best items only.
"""
from __future__ import annotations

import json
import re
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


# ----------------------------------------------------------------- providers

def gemini(prompt: str, api_key: str, models: list[str], max_tokens: int,
           json_mode: bool = False, temperature: float = 0.3) -> tuple[str, str]:
    """Try models in order; move on when a model is missing, overloaded or out of quota."""
    errors = []
    cfg = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
    for model in models:
        for attempt in range(3):
            try:
                r = requests.post(GEMINI_URL.format(model=model), json=body, timeout=900,
                                  headers={"x-goog-api-key": api_key, "content-type": "application/json"})
            except requests.RequestException as e:
                errors.append(f"{model}: {type(e).__name__}")
                time.sleep(15)
                continue
            if r.status_code == 200:
                data = r.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = cand.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                if text.strip():
                    return text, model
                errors.append(f"{model}: empty response ({cand.get('finishReason') or data.get('promptFeedback')})")
                break
            errors.append(f"{model}: HTTP {r.status_code} {r.text[:120]}")
            if r.status_code in (429, 500, 503) and attempt < 2:
                time.sleep(45 * (attempt + 1))  # per-minute quota: wait and retry
                continue
            break  # 400/403/404: model unavailable -> next model
    raise LLMError("Gemini unavailable — " + " | ".join(errors[-4:]))


def anthropic(prompt: str, api_key: str, model: str, max_tokens: int) -> tuple[str, str]:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    last = None
    for attempt in range(4):
        r = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=900)
        if r.status_code == 200:
            data = r.json()
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), model
        last = f"HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code not in (429, 500, 502, 503, 529):
            break
        time.sleep(20 * (attempt + 1))
    raise LLMError(f"Anthropic API unavailable: {last}")


class Engine:
    def __init__(self, cfg: dict, gemini_key: str | None, anthropic_key: str | None):
        self.cfg = cfg
        self.provider = cfg.get("provider", "gemini")
        self.key = gemini_key if self.provider == "gemini" else anthropic_key
        self.used_models: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self.cfg.get("enabled", True) and self.key)

    def run(self, prompt: str, stage: str, json_mode: bool = False) -> str:
        if self.provider == "gemini":
            models = self.cfg["gemini_triage_models"] if stage == "triage" else self.cfg["gemini_models"]
            max_tok = self.cfg["triage_max_tokens"] if stage == "triage" else self.cfg["max_tokens"]
            text, model = gemini(prompt, self.key, models, max_tok, json_mode=json_mode,
                                 temperature=0.1 if stage == "triage" else 0.3)
        else:
            text, model = anthropic(prompt, self.key, self.cfg["anthropic_model"],
                                    min(self.cfg["max_tokens"], 16000))
        self.used_models.add(model)
        return text


# ---------------------------------------------------------------- formatting

def item_block(i: int, it: dict, full: bool) -> str:
    excerpt = it.get("text") or it.get("snippet") or "—"
    excerpt = excerpt[: (2500 if full else 350)]
    lines = [f"[{i}] {it['title']}",
             f"    Source: {it['source']} | Region: {it['region']} | tier {it['tier']} | type: {it['kind']}"
             + (f" | Publisher: {it['publisher']}" if it.get("publisher") else "")
             + f" | date: {it.get('date') or 'unknown'}"]
    if it.get("watch"):
        lines.append(f"    WATCHLIST MATCH: {it['watch']}")
    if full:
        lines.append(f"    Link: {it['url']}")
        for a in it.get("also", [])[:4]:
            lines.append(f"    Also reported by: {a.get('publisher') or a['source']} — {a['url']}")
        if it.get("primary_search"):
            lines.append(f"    Primary-source search link: {it['primary_search']}")
        if it.get("triage_reason"):
            lines.append(f"    Triage: score {it.get('score')} — {it['triage_reason']}")
    lines.append(f"    Excerpt: {excerpt}")
    return "\n".join(lines)


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


# -------------------------------------------------------------------- stages

def triage(engine: Engine, items: list[dict], template: str, batch_size: int, log) -> int:
    """Adds 'score', 'triage_reason', 'topic', 'agency_domain' to items. Returns #scored."""
    scored = 0
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        listing = "\n".join(item_block(i, it, full=False) for i, it in enumerate(batch, 1))
        prompt = template.replace("{items}", listing)
        try:
            data = parse_json(engine.run(prompt, "triage", json_mode=True))
        except Exception as e:  # noqa: BLE001
            log(f"  triage batch {start // batch_size + 1} failed: {str(e)[:160]}")
            continue
        for row in data if isinstance(data, list) else data.get("items", []):
            try:
                it = batch[int(row["id"]) - 1]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            it["score"] = float(row.get("score", 0))
            it["triage_reason"] = str(row.get("reason", ""))[:200]
            it["topic"] = str(row.get("topic", ""))
            dom = str(row.get("agency_domain") or "").strip().lower()
            if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom):
                it["agency_domain"] = dom
            scored += 1
        time.sleep(4)  # stay well under per-minute limits
    return scored
