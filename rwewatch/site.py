"""Static public archive (GitHub Pages): one page per edition, Markdown download,
RSS feed and an 'About' page with the AI disclosure and licence."""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import re
import shutil
from pathlib import Path

import markdown as md

from .report import CSS, AI_META, to_html


def _title(text: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", text, re.M)
    return m.group(1).strip() if m else fallback


def _summary(text: str) -> str:
    m = re.search(r"## Key takeaways\s*\n(.+?)(?:\n## |\Z)", text, re.S)
    return (m.group(1).strip() if m else "")[:1500]


def build(root: Path, out: Path, settings: dict, site_url: str | None, about_md: str) -> int:
    archives = sorted((root / "archives").glob("*.md"), reverse=True)
    if out.exists():
        shutil.rmtree(out)
    (out / "editions").mkdir(parents=True)
    site_title = settings["title"]
    nav = ("<nav><a href='{base}index.html'>All editions</a> · <a href='{base}feed.xml'>RSS</a> · "
           "<a href='{base}about.html'>About &amp; AI notice</a></nav>")

    entries = []
    for p in archives:
        text = p.read_text("utf-8")
        title = _title(text, p.stem)
        body = re.sub(r"^# .+\n", "", text, count=1)
        (out / "editions" / f"{p.stem}.md").write_text(text, "utf-8")
        page = to_html(title, body + f"\n\n[Download this edition (Markdown)]({p.stem}.md)",
                       f"Edition of {p.stem}", nav.format(base="../"))
        (out / "editions" / f"{p.stem}.html").write_text(page, "utf-8")
        entries.append((p.stem, title, _summary(text)))

    # index
    rows = "\n".join(f"- [{t}](editions/{d}.html) · [Markdown](editions/{d}.md)" for d, t, _ in entries)
    latest = ""
    if entries:
        d, t, s = entries[0]
        latest = f"## Latest edition\n\n**[{t}](editions/{d}.html)**\n\n{s}\n\n"
    index_md = (
        "> **AI-generated content.** Each edition is produced automatically by an AI system "
        "(Google Gemini, free API tier) from public sources, without human editorial review "
        "before publication. Verify items against their linked sources. "
        "[Read the full notice](about.html).\n\n"
        f"{settings.get('description', '')}\n\n{latest}## All editions\n\n{rows or '_No edition yet._'}\n\n"
        "Subscribe with any RSS reader: [feed.xml](feed.xml)\n\n"
        "Reports: [CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/) — share and download "
        "freely, unmodified, with attribution."
    )
    (out / "index.html").write_text(to_html(site_title, index_md, "Weekly watch", nav.format(base="")), "utf-8")
    (out / "about.html").write_text(to_html(f"About — {site_title}", about_md, "", nav.format(base="")), "utf-8")

    # RSS
    base = (site_url or "").rstrip("/") + "/"
    items_xml = []
    for d, t, s in entries[:30]:
        pub = email.utils.format_datetime(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc))
        desc = html.escape(md.markdown(s) + "<p><em>AI-generated content — see the notice in each edition.</em></p>")
        items_xml.append(
            f"<item><title>{html.escape(t)}</title><link>{base}editions/{d}.html</link>"
            f"<guid>{base}editions/{d}.html</guid><pubDate>{pub}</pubDate>"
            f"<description>{desc}</description></item>")
    rss = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f"<title>{html.escape(site_title)}</title><link>{base}</link>"
           f"<description>{html.escape(settings.get('description', ''))} (AI-generated)</description>"
           f"<language>en</language>{''.join(items_xml)}</channel></rss>")
    (out / "feed.xml").write_text(rss, "utf-8")
    (out / ".nojekyll").write_text("", "utf-8")
    return len(entries)


__all__ = ["build", "CSS", "AI_META"]
