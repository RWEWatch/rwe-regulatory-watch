"""Collectors: each function takes a source definition and a context and returns items.

An item is a plain dict (see make_item). Collectors never decide what is "new":
that is done in the pipeline using the memory store and the date window.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
})


def http_get(url: str, timeout: int = 30, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=timeout, **kw)
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------- helpers

def clean(text: str | None, limit: int = 1500) -> str:
    if not text:
        return ""
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    value = value.strip()
    try:
        return email.utils.parsedate_to_datetime(value).date()
    except Exception:  # noqa: BLE001
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def norm_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/",
                                    urllib.parse.urlencode(q), ""))


KIND_BY_TYPE = {"gnews": "press", "pubmed": "publication", "europepmc": "publication",
                "crossref": "publication", "arxiv": "preprint", "clinicaltrials": "registry"}


def make_item(src: dict, title: str, url: str, date: dt.date | None = None,
              snippet: str = "", kind: str | None = None, doi: str | None = None, **extra) -> dict:
    doi = (doi or "").strip().lower() or None
    return {
        "source_id": src["id"],
        "source": src["name"],
        "region": src.get("region", ""),
        "tier": src.get("tier", 2),
        "kind": kind or KIND_BY_TYPE.get(src["type"], "official"),
        "title": clean(title, 400),
        "url": url,
        "key": f"doi:{doi}" if doi else norm_url(url),
        "doi": doi,
        "date": date.isoformat() if date else None,
        "snippet": clean(snippet),
        **extra,
    }


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


# ------------------------------------------------------------------ RSS / Atom

def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom without external dependencies."""
    root = ET.fromstring(xml_bytes)
    _strip_ns(root)
    out = []
    entries = root.findall(".//item") or root.findall(".//entry")
    for e in entries:
        title = clean(e.findtext("title") or "", 400)
        link = (e.findtext("link") or "").strip()
        if not link:
            for lk in e.findall("link"):
                if lk.get("rel", "alternate") == "alternate" and lk.get("href"):
                    link = lk.get("href")
                    break
        if not link:
            link = (e.findtext("guid") or e.findtext("id") or "").strip()
        date = (e.findtext("pubDate") or e.findtext("published") or e.findtext("updated")
                or e.findtext("date") or "")
        desc = e.findtext("description") or e.findtext("summary") or e.findtext("content") or ""
        publisher = (e.findtext("source") or "").strip()
        if title and link.startswith("http"):
            out.append({"title": title, "url": link, "date": parse_date(date), "snippet": desc,
                        "publisher": publisher})
    return out


def collect_rss(src: dict, ctx: dict) -> list[dict]:
    r = http_get(src["url"], src.get("timeout", ctx["timeout"]))
    return [make_item(src, e["title"], e["url"], e["date"], e["snippet"]) for e in parse_feed(r.content)]


def collect_gnews(src: dict, ctx: dict) -> list[dict]:
    hl = src.get("hl", "en-US")
    gl = src.get("gl", "US")
    lang = hl.split("-")[0]
    q = f'{src["query"]} when:{ctx["window_days"]}d'
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
           + f"&hl={hl}&gl={gl}&ceid={gl}:{lang}")
    r = http_get(url, ctx["timeout"])
    items = []
    for e in parse_feed(r.content):
        title, publisher = e["title"], e.get("publisher") or ""
        if " - " in title:  # Google News titles end with " - Publisher"
            head, tail = title.rsplit(" - ", 1)
            if not publisher or tail.strip() == publisher:
                title, publisher = head, publisher or tail.strip()
        snippet = e["snippet"]
        if snippet and clean(snippet).startswith(clean(title)[:40]):
            snippet = ""  # the description only repeats the title
        items.append(make_item(src, title, e["url"], e["date"], snippet, publisher=publisher))
    return items


def gnews_fallback(src: dict, ctx: dict) -> list[dict]:
    """Used when an rss/page source blocks the script: Google News restricted to its domain."""
    domain = urllib.parse.urlsplit(src["url"]).netloc.removeprefix("www.")
    fb = {**src, "type": "gnews", "query": f"site:{domain}", "hl": "en-US", "gl": "US"}
    return collect_gnews(fb, ctx)


# ----------------------------------------------------------------------- pages

def collect_page(src: dict, ctx: dict) -> list[dict]:
    """Extract all item links from a 'news' page. Novelty is decided by comparison
    with the links already seen (memory store)."""
    r = http_get(src["url"], src.get("timeout", ctx["timeout"]))
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    include = re.compile(src["include"]) if src.get("include") else None
    seen, items = set(), []
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(r.url, a["href"]).split("#")[0]
        if not href.startswith("http") or href in seen:
            continue
        if include and not include.search(href):
            continue
        title = a.get_text(" ", strip=True) or a.get("title") or ""
        if len(title) < 12:  # navigation links
            continue
        seen.add(href)
        parent = a.find_parent(["li", "article", "tr", "div"])
        context = parent.get_text(" ", strip=True)[:500] if parent else ""
        date = None
        m = re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2} [A-Z][a-z]+ \d{4}|[A-Z][a-z]+ \d{1,2}, \d{4}", context)
        if m:
            date = parse_date(m.group(0))
        items.append(make_item(src, title, href, date, context if context != title else ""))
    return items


# ------------------------------------------------------------ Federal Register

def collect_federal_register(src: dict, ctx: dict) -> list[dict]:
    params = [("conditions[publication_date][gte]", ctx["date_from"].isoformat()),
              ("conditions[publication_date][lte]", ctx["date_to"].isoformat()),
              ("per_page", "200"), ("order", "newest")]
    for a in src.get("agencies", []):
        params.append(("conditions[agencies][]", a))
    for f in ("title", "html_url", "publication_date", "abstract", "type", "comments_close_on"):
        params.append(("fields[]", f))
    r = http_get("https://www.federalregister.gov/api/v1/documents.json", ctx["timeout"], params=params)
    items = []
    for d in r.json().get("results", []):
        snippet = d.get("abstract") or ""
        if d.get("comments_close_on"):
            snippet += f" [Comments due {d['comments_close_on']}]"
        items.append(make_item(src, d["title"], d["html_url"], parse_date(d.get("publication_date")),
                               f"({d.get('type', '')}) {snippet}"))
    return items


# ---------------------------------------------------------------------- PubMed

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def collect_pubmed(src: dict, ctx: dict) -> list[dict]:
    base = {"db": "pubmed", "tool": "rwe-regulatory-watch", "email": ctx.get("contact_email", "")}
    if ctx.get("ncbi_api_key"):
        base["api_key"] = ctx["ncbi_api_key"]
    r = http_get(EUTILS + "esearch.fcgi", ctx["timeout"], params={
        **base, "term": src["query"], "retmode": "json", "retmax": src.get("retmax", 200),
        "datetype": "edat", "mindate": ctx["date_from"].strftime("%Y/%m/%d"),
        "maxdate": ctx["date_to"].strftime("%Y/%m/%d"), "sort": "relevance"})
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    time.sleep(0.4)
    r = http_get(EUTILS + "efetch.fcgi", 90, params={**base, "id": ",".join(ids), "retmode": "xml"})
    return [make_item(src, **a) for a in parse_pubmed_xml(r.content)]


def parse_pubmed_xml(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID")
        t = art.find(".//ArticleTitle")
        title = "".join(t.itertext()) if t is not None else ""
        abstract = " ".join("".join(x.itertext()) for x in art.findall(".//Abstract/AbstractText"))
        journal = art.findtext(".//Journal/Title") or art.findtext(".//MedlineTA") or ""
        types = [x.text for x in art.findall(".//PublicationType") if x.text]
        y = art.findtext(".//ArticleDate/Year") or art.findtext(".//PubDate/Year")
        m = art.findtext(".//ArticleDate/Month") or "1"
        d = art.findtext(".//ArticleDate/Day") or "1"
        try:
            date = dt.date(int(y), int(m), int(d)) if y and m.isdigit() else None
        except ValueError:
            date = None
        doi = next((i.text for i in art.findall(".//ArticleId") if i.get("IdType") == "doi"), None)
        out.append({
            "title": title,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "date": date,
            "snippet": f"{journal} — {', '.join(types[:3])}. {abstract}",
            "journal": journal,
            "doi": doi,
        })
    return out


# ------------------------------------------------------------------- Europe PMC

def collect_europepmc(src: dict, ctx: dict) -> list[dict]:
    """Europe PMC: PubMed + preprints (medRxiv, bioRxiv, SSRN, Research Square…)
    + open-access full text. FIRST_IDATE = date first indexed (best for weekly watch)."""
    q = (f'({src["query"]}) AND FIRST_IDATE:[{ctx["date_from"].isoformat()} '
         f'TO {ctx["date_to"].isoformat()}]')
    items, cursor = [], "*"
    for _ in range(src.get("max_pages", 3)):
        r = http_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", 60, params={
            "query": q, "format": "json", "resultType": "core", "pageSize": 200,
            "cursorMark": cursor, "synonym": "false"})
        data = r.json()
        for p in data.get("resultList", {}).get("result", []):
            doi = p.get("doi")
            src_code, pid = p.get("source", ""), p.get("id", "")
            url = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{src_code}/{pid}"
            journal = (p.get("journalInfo") or {}).get("journal", {}).get("title") \
                or (p.get("bookOrReportDetails") or {}).get("publisher") or ""
            is_preprint = src_code == "PPR"
            label = "Preprint (not peer reviewed)" if is_preprint else journal
            items.append(make_item(
                src, p.get("title", ""), url,
                parse_date(p.get("firstPublicationDate") or p.get("firstIndexDate")),
                f"{label}. {p.get('abstractText', '')}",
                kind="preprint" if is_preprint else "publication", doi=doi, journal=label))
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor or len(data.get("resultList", {}).get("result", [])) < 200:
            break
        cursor = nxt
    return items


# --------------------------------------------------------------------- Crossref

def collect_crossref(src: dict, ctx: dict) -> list[dict]:
    """Online-first articles of key journals (often days/weeks before PubMed)."""
    items = []
    rx = re.compile(src["match"], re.I) if src.get("match") else None
    for j in src["journals"]:
        try:
            r = http_get(f"https://api.crossref.org/journals/{j['issn']}/works", 45, params={
                "filter": f"from-created-date:{ctx['date_from'].isoformat()},"
                          f"until-created-date:{ctx['date_to'].isoformat()},type:journal-article",
                "rows": 200,
                "select": "DOI,title,abstract,container-title,created,URL,type",
                "mailto": ctx.get("contact_email") or "rwe-watch@example.org"})
        except Exception:  # noqa: BLE001
            continue
        for w in r.json().get("message", {}).get("items", []):
            title = " ".join(w.get("title") or [])
            if not title:
                continue
            abstract = clean(w.get("abstract", ""), 1500)
            jrx = re.compile(j["match"], re.I) if j.get("match") else rx
            if jrx and not jrx.search(f"{title} {abstract}"):
                continue
            created = (w.get("created") or {}).get("date-time", "")
            items.append(make_item(src, title, f"https://doi.org/{w['DOI']}", parse_date(created),
                                   f"{j['name']} (online first). {abstract}", doi=w["DOI"],
                                   journal=j["name"]))
        time.sleep(0.3)
    return items


# ------------------------------------------------------------------------ arXiv

def collect_arxiv(src: dict, ctx: dict) -> list[dict]:
    r = http_get("https://export.arxiv.org/api/query", 60, params={
        "search_query": src["query"], "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": src.get("max_results", 150)})
    items = []
    for e in parse_feed(r.content):
        if e["date"] and not (ctx["date_from"] <= e["date"] <= ctx["date_to"]):
            continue
        items.append(make_item(src, e["title"], e["url"], e["date"],
                               f"arXiv preprint (not peer reviewed). {e['snippet']}"))
    return items


# ------------------------------------------------------------ ClinicalTrials.gov

def collect_clinicaltrials(src: dict, ctx: dict) -> list[dict]:
    rng = f"AREA[StudyFirstPostDate]RANGE[{ctx['date_from'].isoformat()},{ctx['date_to'].isoformat()}]"
    r = http_get("https://clinicaltrials.gov/api/v2/studies", 60, params={
        "query.term": src["query"], "filter.advanced": rng, "pageSize": 100, "format": "json"})
    items = []
    for s in r.json().get("studies", []):
        p = s.get("protocolSection", {})
        ident = p.get("identificationModule", {})
        nct = ident.get("nctId")
        if not nct:
            continue
        status = p.get("statusModule", {})
        design = p.get("designModule", {})
        sponsor = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
        summary = p.get("descriptionModule", {}).get("briefSummary", "")
        items.append(make_item(
            src, ident.get("officialTitle") or ident.get("briefTitle", ""),
            f"https://clinicaltrials.gov/study/{nct}",
            parse_date((status.get("studyFirstPostDateStruct") or {}).get("date")),
            f"{nct} · {design.get('studyType', '')} · sponsor: {sponsor}. {summary}"))
    return items


COLLECTORS = {
    "rss": collect_rss,
    "gnews": collect_gnews,
    "page": collect_page,
    "federal_register": collect_federal_register,
    "pubmed": collect_pubmed,
    "europepmc": collect_europepmc,
    "crossref": collect_crossref,
    "arxiv": collect_arxiv,
    "clinicaltrials": collect_clinicaltrials,
}


# ------------------------------------------------------------------ enrichment

def fetch_text(url: str, limit: int, timeout: int = 25) -> str:
    """Main text of a page, to give the model real context."""
    if re.search(r"\.(pdf|xlsx?|docx?|zip)(\?|$)", url, re.I) or "news.google.com" in url:
        return ""
    r = http_get(url, timeout)
    if "html" not in r.headers.get("Content-Type", "html"):
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return clean(main.get_text(" "), limit)


def page_fingerprint(url: str, timeout: int = 30) -> str:
    """Stable text fingerprint of a page (watchlist change detection)."""
    import hashlib
    text = fetch_text(url, 200_000, timeout)
    text = re.sub(r"\d{1,2}:\d{2}|\b\d{4}-\d{2}-\d{2}T\S+", "", text)  # drop volatile timestamps
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["COLLECTORS", "make_item", "parse_feed", "parse_pubmed_xml", "fetch_text",
           "gnews_fallback", "page_fingerprint", "http_get", "clean", "json"]
