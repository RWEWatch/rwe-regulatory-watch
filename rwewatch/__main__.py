"""Entry point.

    python -m rwewatch                 full run: collect, analyse, email, save, build site
    python -m rwewatch --check         test every source and print the coverage table
    python -m rwewatch --no-email --no-save --fresh   full test edition, nothing saved
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import collectors, dedupe, gnews, health, llm, report, site, verify, watchlist
from .store import SeenStore

ROOT = Path(__file__).resolve().parent.parent
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
PUBLICATION_TYPES = {"pubmed", "europepmc", "crossref", "arxiv", "clinicaltrials"}
HEURISTIC = {"watch": 9, "official": 6, "publication": 5, "registry": 4, "preprint": 4, "press": 3}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fmt_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def week_label(a: dt.date, b: dt.date) -> str:
    if a.month == b.month:
        return f"week of {a.day}–{fmt_date(b)}"
    return f"week of {a.day} {MONTHS[a.month - 1]} – {fmt_date(b)}"


def load_yaml(name: str):
    return yaml.safe_load((ROOT / "config" / name).read_text("utf-8"))


def previous_titles(today: dt.date) -> str:
    files = sorted(p for p in (ROOT / "archives").glob("*.md") if p.stem < today.isoformat())
    if not files:
        return "(none — first edition)"
    titles = re.findall(r"^### (.+)$", files[-1].read_text("utf-8"), re.M)
    return "\n".join(f"- {t}" for t in titles) or "(no titles)"


def short_err(e: Exception) -> str:
    code = re.search(r"\b(4\d\d|5\d\d)\b", str(e))
    return type(e).__name__ + (f" {code.group(1)}" if code else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="RWE & Regulatory Science Watch")
    ap.add_argument("--check", action="store_true", help="test sources and print coverage only")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="do not update memory, health or archives")
    ap.add_argument("--no-archive", action="store_true", help="update memory without archiving")
    ap.add_argument("--no-ai", action="store_true", help="skip AI stages (used for initialisation)")
    ap.add_argument("--init", action="store_true",
                    help="initialisation: memorise news pages only (dated sources stay unseen)")
    ap.add_argument("--fresh", action="store_true", help="ignore memory for dated sources (test edition)")
    ap.add_argument("--no-site", action="store_true")
    ap.add_argument("--days", type=int)
    ap.add_argument("--only", help="comma-separated source ids (debug)")
    args = ap.parse_args()

    settings = load_yaml("settings.yaml")
    sources = load_yaml("sources.yaml")["sources"]
    journals = load_yaml("journals.yaml")
    watch_entries = (load_yaml("watchlist.yaml") or {}).get("items", []) or []
    for j in journals.get("groups", []):
        sources.append({**j, "type": "crossref"})
    if args.only:
        wanted = set(args.only.split(","))
        sources = [s for s in sources if s["id"] in wanted]

    tz = ZoneInfo(settings.get("timezone", "Europe/Paris"))
    today = dt.datetime.now(tz).date()
    days = args.days or settings.get("window_days", 7)
    date_to = today - dt.timedelta(days=1)
    date_from = today - dt.timedelta(days=days)
    save = not (args.no_save or args.check)

    repo_url = None
    if os.environ.get("GITHUB_REPOSITORY"):
        repo_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}"
    settings.setdefault("tool", {})["repository_url"] = settings.get("tool", {}).get("repository_url") or repo_url
    site_url = os.environ.get("SITE_URL") or settings.get("site", {}).get("url") or None
    if not settings.get("site", {}).get("enabled", True):
        site_url = None

    ctx = {
        "timeout": settings["http"]["timeout"],
        "window_days": days,
        "date_from": date_from,
        "date_to": date_to,
        "contact_email": os.environ.get("CONTACT_EMAIL", ""),
        "ncbi_api_key": os.environ.get("NCBI_API_KEY", ""),
    }
    kw_rx = re.compile("|".join(f"(?:{k})" for k in settings["keywords"]), re.I)
    ex_rx = re.compile("|".join(f"(?:{k})" for k in settings["exclude"]), re.I) if settings.get("exclude") else None

    store = SeenStore(ROOT / "data" / "seen.json", settings["state"]["retention_days"])

    # ------------------------------------------------------------- 1. collect
    def run(src):
        try:
            return src, collectors.COLLECTORS[src["type"]](src, ctx), None, False
        except Exception as e:  # noqa: BLE001
            err = short_err(e)
            if src["type"] in ("rss", "page") and src.get("fallback", True):
                try:
                    return src, collectors.gnews_fallback(src, ctx), err, True
                except Exception:  # noqa: BLE001
                    pass
            return src, [], err, False

    log(f"Window: {date_from} → {date_to} — {len(sources)} sources")
    with ThreadPoolExecutor(settings["http"]["workers"]) as pool:
        results = list(pool.map(run, sources))

    statuses, candidates, seen_keys = [], [], set()
    for src, items, err, fell_back in results:
        st = {"name": src["name"], "region": src.get("region", ""), "tier": src.get("tier", 2),
              "new": 0, "status": "ok", "error": err or ""}
        if err and not fell_back:
            st["status"] = "error"
            statuses.append(st)
            log(f"  ✗ {src['id']}: {err}")
            continue

        uniq = list({it["key"]: it for it in items}.values())
        eff_type = "gnews" if fell_back else src["type"]
        store_id = src["id"] + ("__fallback" if fell_back else "")
        bootstrap = eff_type == "page" and not store.is_initialised(src["id"])
        match_rx = re.compile(src["match"], re.I) if src.get("match") and eff_type != "crossref" else None
        new = []
        for it in uniq:
            fresh = args.fresh and eff_type != "page"
            if not fresh and (store.is_seen(store_id, it["key"]) or store.seen_anywhere(it["key"])):
                continue
            if it["date"] and eff_type != "page":
                d = dt.date.fromisoformat(it["date"])
                if not (date_from <= d <= date_to):
                    continue
            elif it["date"] is None and eff_type != "page" and not store.is_initialised(store_id) and not fresh:
                continue
            if bootstrap:
                continue
            text = f"{it['title']} {it['snippet']}"
            if ex_rx and ex_rx.search(text):
                continue
            if match_rx:
                if not match_rx.search(text):
                    continue
            elif eff_type not in PUBLICATION_TYPES and (src.get("filter", "keywords") != "none" or fell_back) \
                    and not kw_rx.search(text):
                continue
            if it["key"] in seen_keys:
                continue
            seen_keys.add(it["key"])
            new.append(it)

        # In --init mode only news pages are memorised: dated sources (publications, feeds,
        # press) must stay "unseen" so that the first real edition still contains them.
        if save and (not args.init or eff_type == "page"):
            store.add(store_id, [it["key"] for it in uniq], today)
        st["new"] = len(new)
        st["status"] = "fallback" if fell_back else ("initialised" if bootstrap else "ok")
        statuses.append(st)
        candidates.extend(new)
        log(f"  ✓ {src['id']}: {len(items)} found, {len(new)} new"
            f"{' (initialised)' if bootstrap else ''}{' (fallback: ' + err + ')' if fell_back else ''}")

    # ------------------------------------------------------------ 2. watchlist
    n_flag = watchlist.flag_matches(candidates, watch_entries)
    changes, wstat = watchlist.check_pages(watch_entries, ROOT / "data" / "watch_state.json", today, save, log)
    candidates.extend(changes)
    statuses.extend(wstat)
    log(f"Watchlist: {n_flag} matching item(s), {len(changes)} tracked page change(s)")

    hstate = health.update(ROOT / "data" / "health.json", statuses, save)
    alerts = health.alerts(hstate, statuses, settings.get("health", {}).get("alert_after_runs", 2))

    if args.check:
        print(report.coverage(statuses, full=True, alerts=alerts))
        return 0

    # --------------------------------------------------------- 3. dedupe/rank
    before = len(candidates)
    candidates = dedupe.cluster(candidates, settings["selection"].get("similarity", 0.6))
    log(f"Dedupe: {before} → {len(candidates)} items")
    sel = settings["selection"]
    candidates.sort(key=lambda it: (dedupe.KIND_RANK.get(it["kind"], 9), it["tier"],
                                    -(dt.date.fromisoformat(it["date"]).toordinal() if it["date"] else 0)))
    candidates = candidates[: sel["max_triage_items"]]

    # --------------------------------------------------------------- 4. triage
    engine = llm.Engine(settings["llm"],
                        os.environ.get("GEMINI_API_KEY"), os.environ.get("ANTHROPIC_API_KEY"))
    use_ai = engine.available and not args.no_ai
    triage_tpl = (ROOT / "config" / "prompts" / "triage.md").read_text("utf-8")
    if use_ai and candidates:
        log(f"Triage of {len(candidates)} items…")
        n = llm.triage(engine, candidates, triage_tpl, sel["triage_batch"], log)
        log(f"  {n} items scored")
    for it in candidates:
        if "score" not in it:
            it["score"] = HEURISTIC.get(it["kind"], 3) + (1 if it["tier"] == 1 else 0) + (2 if it.get("watch") else 0)
    selected = [it for it in candidates if it["score"] >= sel["min_score"]
                or (it.get("watch") and it["score"] >= sel.get("watch_min_score", 3))]
    selected.sort(key=lambda it: -it["score"])
    selected = selected[: sel["max_items"]]
    log(f"Selected {len(selected)} items for the report")

    # ---------------------------------------------------- 5. links & context
    press = [it for it in selected if it["kind"] == "press"]
    if press and not args.no_ai:
        n = gnews.decode_items(press, settings["enrich"].get("decode_max", 40))
        log(f"Google News links decoded: {n}/{len(press)}")
    for it in selected:
        if not it.get("date"):
            it["detected"] = today.isoformat()
        # a press/fallback item whose link resolves to an official site IS the primary source
        if it["kind"] == "press" and collectors.is_official(it["url"]):
            it["kind"] = "official"
            it.pop("primary_search", None)
    press = [it for it in selected if it["kind"] == "press"]
    for it in press:
        dom = it.get("agency_domain")
        if dom:
            q = f"site:{dom} {it['title'][:90]}"
            it["primary_search"] = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)

    to_enrich = [it for it in selected if it["kind"] in ("official", "watch", "press")
                 and "news.google.com" not in it["url"]][: settings["enrich"]["max_pages"]]

    def enrich(it):
        try:
            if it["kind"] == "press":
                txt, links = collectors.fetch_with_links(it["url"], settings["enrich"]["chars_per_page"])
                if links:
                    it["primary_links"] = links
            else:
                txt = collectors.fetch_text(it["url"], settings["enrich"]["chars_per_page"])
            if len(txt) > len(it.get("snippet", "")):
                it["text"] = txt
        except Exception:  # noqa: BLE001
            pass

    if not args.no_ai:
        with ThreadPoolExecutor(settings["http"]["workers"]) as pool:
            list(pool.map(enrich, to_enrich))

    # ---------------------------------------------------------------- 6. write
    wl = week_label(date_from, date_to)
    title = f"{settings['title']} — {wl}"
    body = None
    if use_ai and selected:
        tpl = (ROOT / "config" / "prompts" / "report.md").read_text("utf-8")
        listing = "\n\n".join(llm.item_block(i, it, full=True) for i, it in enumerate(selected, 1))
        for k, v in {"{title}": settings["title"], "{week_label}": wl,
                     "{recipient}": settings.get("publisher", {}).get("name", ""),
                     "{date_from}": fmt_date(date_from), "{date_to}": fmt_date(date_to),
                     "{previous_titles}": previous_titles(today), "{items}": listing}.items():
            tpl = tpl.replace(k, v)
        try:
            log("Writing the report…")
            body = engine.run(tpl, "report")
        except Exception as e:  # noqa: BLE001
            log(f"Report writing failed: {e}")
            body = report.scored_digest(selected, llm.friendly(e))
    if body is None:
        body = report.raw_digest(selected)

    # tidy the model's output: duplicated labels, LaTeX maths, stray "Established fact:" prefixes
    body = re.sub(r"(Analytical inference:\s*){2,}", "Analytical inference: ", body)
    body = re.sub(r"\*\*Analytical inference:\*\*\s*Analytical inference:", "**Analytical inference:**", body)
    for a, b in [(r"\geq", "≥"), (r"\ge", "≥"), (r"\leq", "≤"), (r"\le", "≤"),
                 (r"\times", "×"), (r"\%", "%"), (r"\pm", "±"), ("{,}", ",")]:
        body = body.replace(a, b)
    body = re.sub(r"\$([^$\n]{1,40})\$",
                  lambda m: m.group(1).strip()
                  if re.search(r"[≥≤×±^_\\{}=<>]", m.group(1)) or re.fullmatch(r"\s*[\d.,]+\s*", m.group(1))
                  else m.group(0), body)
    body = re.sub(r"(\*\*Summary:\*\*\s*)Established fact:\s*", r"\1", body)
    # remove any "Editorial opportunities" section the model might add anyway
    body = re.sub(r"\n#+\s*Editorial opportunities.*?(?=\n## |\Z)", "\n", body, flags=re.S | re.I)
    body, ok_links, removed = verify.check_links(body, verify.allowed_urls(selected))
    log(f"Link check: {ok_links} verified, {removed} removed")

    # ------------------------------------------------------------ 7. assemble
    meta = (f"Period covered: {fmt_date(date_from)} → {fmt_date(date_to)} · "
            f"{len(candidates)} candidates · {len(selected)} analysed · generated {fmt_date(today)}")
    notice = report.disclosure(settings, engine.used_models, (ok_links, removed), site_url,
                               settings["llm"].get("provider", "gemini"))
    label = report.ai_label(settings)
    coverage_md = report.coverage(statuses, full=True, alerts=alerts)
    cov_block = f"{coverage_md}\n\n" if settings.get("report", {}).get("include_coverage", False) else ""
    full_md = f"# {title}\n\n*{meta}*\n\n{label}\n\n{body.strip()}\n\n{cov_block}{notice}\n"
    email_md = f"{label}\n\n{body.strip()}\n\n{cov_block}{notice}\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"{coverage_md}\n")
    if alerts:
        log("SOURCE HEALTH ALERT: " + "; ".join(alerts))

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "watch.md").write_text(full_md, "utf-8")
    (out / "coverage.md").write_text(coverage_md, "utf-8")
    html = report.to_html(title, email_md, meta)
    (out / "watch.html").write_text(html, "utf-8")

    if save:
        store.prune(today)
        store.save()
        if not args.no_archive:
            (ROOT / "archives").mkdir(exist_ok=True)
            (ROOT / "archives" / f"{today.isoformat()}.md").write_text(full_md, "utf-8")

    if not args.no_site:
        about = (ROOT / "config" / "about.md").read_text("utf-8").replace(
            "{publisher}", settings.get("publisher", {}).get("name", "")).replace(
            "{repository}", settings["tool"].get("repository_url") or "(repository URL)")
        n = site.build(ROOT, ROOT / "_site", settings, site_url, about)
        log(f"Site built: {n} edition(s)")

    if not args.no_email:
        from .mailer import send_mail
        send_mail(title, email_md, html, (f"rwe-watch-{today.isoformat()}.md", full_md.encode("utf-8")))
        log("Email sent.")
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
