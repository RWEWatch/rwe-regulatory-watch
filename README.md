# RWE & Regulatory Science Watch

An automated **weekly watch on real-world evidence (RWE), regulatory science, HTA and evidence methodology**, delivered every Wednesday around 13:00 (Paris) by email and published as a public archive with an RSS feed.

> **AI-generated content.** Each edition is produced by an AI system (Google Gemini, free API tier) from public sources, without human editorial review before publication, and is labelled as such in line with Article 50(4) of the EU AI Act. Always verify items against their linked sources.

**Cost: €0.** It runs on GitHub Actions, uses the free tier of the Gemini API and sends email through Gmail.

---

## What it covers

| Area | Sources |
|---|---|
| **Regulators — 4 continents** | EMA, HMA, DARWIN EU, European Commission, MHRA, Swissmedic, ANSM, BfArM, AIFA, AEMPS, CBG-MEB · FDA (Federal Register, press, RWE page, Sentinel), Health Canada, ANVISA, COFEPRIS · PMDA, MHLW, NMPA/CDE, MFDS, TGA, HSA, CDSCO · African Medicines Agency, SAHPRA, SFDA, EDA — plus smaller agencies via multilingual press monitoring |
| **HTA bodies** | EU HTA / JCA, NICE, HAS, IQWiG, G-BA, Zorginstituut, SMC, NCPE, CDA-AMC, ICER, PBAC, INAHTA/HTAi |
| **International** | WHO (news, IRIS), ICH, ICMRA, CIOMS, IMDRF, OECD, PAHO |
| **EU law & policy** | EUR-Lex (legislation, Commission proposals, Official Journal), EU/US policy monitoring |
| **Registries** | HMA-EMA RWD catalogues / EU PAS Register, ClinicalTrials.gov (registry-based and RWE-design studies) |
| **Literature** | PubMed (3 targeted queries), Europe PMC (preprints + full-text methods search), Crossref *online-first* feeds of 24 key journals, arXiv (stat.ME / stat.AP) |
| **RWE ecosystem** | Duke-Margolis, Reagan-Udall, OHDSI, EHDEN, TransCelerate, CTTI, ISPOR, ISPE |

## How it works

```
GitHub Actions — every Wednesday ~13:00 Paris
 1. Collect      ~90 sources in parallel (feeds, news pages, APIs)
                 · blocked sites fall back automatically to Google News for their domain
 2. Keep new     memory of links already seen (data/seen.json) + 7-day window
 3. Watchlist    flag items about tracked dossiers; detect changes on tracked pages
 4. Merge        same DOI or near-identical titles → one item ("also reported by")
 5. Triage (AI)  fast model scores every item 0–10 with a reason          ← stage 1
 6. Context      full abstracts / page text; Google News links resolved to the publisher
 7. Write (AI)   stronger model writes the report on the best items only   ← stage 2
 8. Verify       every link must be a collected source; others are removed
 9. Publish      email (compact) · archive (full) · public site + RSS (GitHub Pages)
```

Each edition contains: **Key takeaways · Tracked items · thematic sections · To watch · About this report (AI notice)**. The source coverage table is shown on each GitHub Actions run page (Summary) and kept out of the shared report (set `report.include_coverage: true` to include it).

---

## Setup (≈ 25 minutes)

You need: a Gmail account, a GitHub account, and a free Gemini API key.

### 1. Gmail app password
1. On the Gmail account, enable **2-Step Verification**: myaccount.google.com → Security.
2. Open **myaccount.google.com/apppasswords**, create one named `rwe-watch`, and copy the 16-letter code **without spaces**.

### 2. Gemini API key (free)
Go to **aistudio.google.com**, click **Get API key → Create API key**, and copy the key (`AIza…`). Do **not** enable billing.

### 3. Repository
1. On GitHub, create a **new repository**, e.g. `rwe-regulatory-watch`.
   - **Public** is required for the free public site (GitHub Pages on the Free plan).
   - Private also works, but without the public site.
2. Click **uploading an existing file** and drag **all** the contents of this folder, including the hidden `.github` folder (Mac: Cmd+Shift+. in Finder; Windows: View → Hidden items). Then click **Commit changes**.
3. Check that `.github/workflows/watch.yml` is present. If it is not, use **Add file → Create new file**, name it `.github/workflows/watch.yml` and paste its content.

### 4. Secrets
Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your `AIza…` key |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | the Gmail address |
| `SMTP_PASSWORD` | the 16-letter app password, no spaces |
| `MAIL_TO` | recipient address(es), comma-separated |
| `MAIL_FROM` | *(optional)* sender shown in emails, e.g. `RWE Regulatory Watch <your.address@gmail.com>` |
| `NCBI_API_KEY` | *(optional)* free NCBI key for more reliable PubMed access |

Secrets are never visible publicly, even in a public repository.

### 5. Permissions and public site
1. **Settings → Actions → General → Workflow permissions**: select **Read and write permissions**, then **Save**.
2. **Settings → Pages → Build and deployment → Source**: select **GitHub Actions**.

### 6. First runs
Go to **Actions → RWE Regulatory Watch → Run workflow** and run these modes in order, waiting for each to finish:

| Mode | What it does |
|---|---|
| `check` | tests every source; the coverage table appears on the run page |
| `init` | memorises the current state of news pages (no AI, no email) |
| `test` | builds a full edition without email or saving; download it under **Artifacts** at the bottom of the run page |
| `run` | the real thing: email + archive + public site |
| `rerun` | *(if needed)* redo this week's edition ignoring memory for publications/feeds — email + archive + site |

The public site is then available at `https://<your-github-username>.github.io/<repository-name>/`.

---

## Customisation

| File | Purpose |
|---|---|
| `config/settings.yaml` | title, publication name, coverage table on/off, AI models, selection thresholds, keywords, exclusions |
| `config/sources.yaml` | agencies, HTA bodies, press queries, PubMed / Europe PMC / arXiv / ClinicalTrials.gov queries |
| `config/journals.yaml` | journals followed through Crossref (by ISSN) and their relevance filters |
| `config/watchlist.yaml` | dossiers to track (regex) and pages to monitor for changes |
| `config/prompts/triage.md` | scoring rubric (stage 1) |
| `config/prompts/report.md` | editorial instructions and report format (stage 2) |
| `config/about.md` | "About & AI notice" page of the public site |

To switch to Anthropic Claude (paid), set `provider: "anthropic"` in `settings.yaml` and add the `ANTHROPIC_API_KEY` secret. The AI notice adapts automatically.

## Local run (optional)

```bash
pip install -r requirements.txt
python -m rwewatch --check                         # source status
python -m rwewatch --no-email --no-save --fresh    # full test edition in out/
python -m rwewatch --check --only ema,nmpa         # test a few sources
```

## Limits

- **Blocked or JavaScript-only sites** (FDA, AEMPS, CDA-AMC, ANVISA…): the tool falls back to Google News restricted to the site's domain. These items are flagged as secondary, with a *Find primary source* link when possible.
- **Site changes**: a source may break when an agency redesigns its website. It then appears in the coverage table, and an alert is raised after 2 consecutive failures.
- **Paywalled articles**: the analysis relies on abstracts.
- **Google News link resolution** uses an undocumented endpoint. If it fails, the Google News link is kept.
- **Free AI tier**: Google may change models or quotas. Several models are tried in order; if all fail, a raw list is sent instead.
- **AI output** may contain errors or over-interpretation. Every edition says so and should be checked before reuse.

## Licences

- **Software**: source-available, **no-derivatives** licence. You may view, download, run and share unmodified copies; you may not distribute modified versions. See [LICENSE.md](LICENSE.md).
- **Reports**: [CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/). Share freely, unmodified, with attribution.

© 2026 RWE Regulatory Watch
