# njboards — NJ planning / zoning board intelligence

`njboards` crawls New Jersey municipal websites for **planning board and zoning
board of adjustment** records, pulls the municipality's **zoning / land
development code from eCode360**, and turns all of it into a per-town profile
that answers the questions you have before filing a site plan application:

| Question | Where the answer comes from |
|---|---|
| Does the town post agendas, minutes, resolutions, **review letters**, **site plans / exhibits**, application forms, checklists? | discovered document inventory |
| Who are the board engineer / planner / attorney? | roll call in minutes, signature blocks in review letters, resolutions |
| Which professionals (engineers, planners, attorneys, architects, traffic experts) **regularly testify** there? | sworn-witness and credential patterns in minutes and resolutions |
| What comments do the board professionals make again and again? | numbered comments in review letters, categorized; board-professional remarks in minutes |
| Which **checklist waivers are refused** (and which are routinely granted)? | waiver sentences in review letters, minutes and resolutions, classified by disposition and topic, cross-referenced to the town's checklist item numbers |
| What conditions of approval are standard? | numbered conditions in resolutions |
| What does the ordinance require (checklist, waiver rules, parking, landscaping, lighting, signs, stormwater, bulk tables, fees)? | eCode360 chapters, indexed by topic, with attachments (Schedule A checklists etc.) |

Everything is stored locally (SQLite + files under `data/`) and can be re-run
incrementally. The crawler identifies itself, honors `robots.txt`, rate-limits
per host (1.5 s default) and caches responses.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
njboards --help
```

Two optional pieces matter in practice:

- **OCR.** Roughly half of the resolutions, applications and older minutes
  towns post are scanned images. Install `tesseract-ocr` (plus `poppler-utils`
  for `pdftoppm`) and run `njboards extract --ocr` or set `NJBOARDS_OCR=1`.
  Without it those documents extract as empty text.
- **Headless browser.** eCode360 (the code host and, for some towns, the
  minutes/resolutions portal) sits behind a Cloudflare JavaScript challenge
  that refuses every plain HTTP client. `pip install playwright && playwright
  install chromium` enables the fallback; `njboards` switches to it
  automatically when a site answers with a challenge page
  (`NJBOARDS_BROWSER=0` disables it).

## Quick start

```bash
njboards init                                 # loads data/municipalities.csv into data/njboards.db
njboards run cranford-union                   # one town: discover → fetch → extract → parse → eCode360 → report
njboards run --county Union --limit 150       # every seeded town in a county, max 150 downloads each
njboards report --all                         # regenerate every profile + data/reports/summary.csv
```

Outputs per town land in `data/reports/<slug>/`:

- `profile.md` – readable profile (see `examples/example-township-profile.md`)
- `profile.json` – the same data, machine readable
- `discovery.json` – boards, platform, counts, crawl errors

and `data/reports/summary.csv` compares all towns side by side (board
professionals, regular applicant-side professionals, top comment categories,
waiver topics denied/granted, number of site plan applications...).

Raw files are kept in `data/raw/<slug>/<board>/<doc_type>/`, extracted text in
`data/text/`, per-document parse results in `data/parsed/`, and code chapters
in `data/code/<slug>/` (one text file per chapter plus `code_index.json`).

## Weekly digest

`njboards weekly` refreshes every town (known board pages, agendas in the
next 35 days, newest minutes / resolutions / review letters / application
packets) and writes `data/digests/<date>.md`: per county and town, what is
coming up (date, board, application, applicant, what is proposed, types) and
what was decided, with the applicant's professionals (engineer, attorney,
planner, traffic engineer, architect) wherever a document names them.
`scripts/weekly.sh` is the scheduled-job wrapper; `--skip-fresh 5` resumes an
interrupted run. The weekly crawl re-fetches board pages cached more than
`--max-age` hours ago (default 12) so that last week's cached listings cannot
hide this week's agendas; the one-week default cache still applies to the
other commands.

## Adding towns

`data/municipalities.csv` ships with ~90 larger municipalities as a seed. The
website column is a best guess and should be verified; the `notes` column
says so. To add or correct one:

```bash
njboards add-town "Borough of Somewhere" --county Bergen --website https://www.somewherenj.org/ \
    --ecode https://ecode360.com/SO1234 --planning-url https://www.somewherenj.org/planning-board
```

Manual `--planning-url` / `--zoning-url` values are used as crawl seeds and
override discovery when a site's navigation is hard to follow.

To get all 564 municipalities:

```bash
njboards import-towns                   # names, counties, types from Wikipedia
njboards import-towns --find-websites   # also tries to locate each official site (slow; verify!)
```

The eCode360 link is picked up automatically when the town's site links its
code (most do). Otherwise pass it: `njboards ecode <slug> --url https://ecode360.com/XX1234`.
Municode / ClerkBase codes are recorded but not pulled yet.

## Pipeline, step by step

| Command | What it does |
|---|---|
| `discover` | Crawls from the homepage (max 150 pages) looking for planning / zoning / land use board pages, "agendas & minutes", "pending applications", forms and archives. Uses platform adapters for **CivicPlus AgendaCenter**, **Municode Meetings / CivicClerk** (public OData API) and **Granicus** listings; everything else is handled generically. Classifies each link as agenda / minutes / resolution / review_letter / site_plan / application / checklist / fee_schedule / notice / ordinance and tags its board and meeting date. |
| `fetch` | Downloads documents (PDF, DOC/DOCX, HTML); follows one-hop "view document" landing pages. `--types minutes,review_letter` and `--limit N` keep it bounded. |
| `extract` | PDF → text with pdfplumber, falling back to `pdftotext`; flags scanned PDFs that need OCR. |
| `parse` | Document-type-specific parsers (below). Results are JSON per document. |
| `ecode` | Table of contents → zoning / land use / site plan / subdivision / stormwater / signs / parking chapters → sections indexed by topic, checklist items, waiver provisions, attachments. |
| `analyze` | Aggregates everything into `profile.json`. No network. |
| `report` | Markdown profile + cross-town CSV. |
| `run` | All of the above. |

### What the parsers extract

- **Agendas / minutes** – meeting date and board; application items split by
  application number / Block-Lot / "Applicant:" headings; applicant,
  address, zone, application type (preliminary & final major site plan,
  minor site plan, c/d variances, subdivision, conditional use...), outcome
  (approved / approved with conditions / denied / carried / withdrawn /
  deemed (in)complete / memorialized), vote; professionals with credentials
  (P.E., P.P., AICP, Esq., AIA, PLS, LLA, PTOE, CME...), role, firm, side
  (board vs. applicant) and whether they were sworn / testified; remarks
  attributed to the board engineer / planner, categorized; waiver sentences.
- **Review letters** – RE line, author and author type (engineer, planner,
  landscape architect, traffic, fire official...), completeness
  recommendation, each numbered comment with category (stormwater, grading,
  parking, traffic, lighting, landscaping, signage, utilities, soil erosion,
  environmental, fire safety, refuse, architecture, zoning/bulk,
  completeness, outside agencies, fees/escrow, easements/title, affordable
  housing, operations, pedestrian, plan corrections...), flags for
  "needs testimony", "variance / design waiver required", "outside agency",
  and a normalized key used to find the same comment across letters.
- **Resolutions** – decision, relief granted, numbered conditions of approval
  (categorized, with "standard boilerplate" flag), witnesses, waivers.
- **Checklists** – numbered items, which checklist (minor / preliminary /
  final site plan, subdivision, variance), topic, waivable (W / NW) marks.
- **Waivers (all documents)** – each waiver statement is classified as
  checklist vs. design waiver, with disposition `granted`, `denied`,
  `needed` ("a design waiver will be required"), `requested`, `deferred` or
  `mentioned`, item numbers (`item 14`, `§ 190-45`) and submission topic
  (EIS, traffic impact study, stormwater report, landscape plan, aerial,
  200-ft list...). Denied ones are listed with their evidence sentence and,
  when the town's checklist was parsed, the text of the checklist item.

## What the live runs showed

Tested on Cranford, Summit, Westfield (CivicPlus sites) and Montclair
(Granicus govAccess site with records on eCode360's document portal):

| Town | Found | Notes |
|---|---|---|
| Cranford | 282 agendas, 30 minutes, 17 review letters, 17 resolutions, 80 site plan exhibits, 22 applications; code CR1142 | Board engineer/planner letters are posted per application and revision ("Review No. 8"); half the PDFs needed OCR |
| Summit | 548 agendas, 283 minutes, 285 exhibits, 117 applications, checklists; code SU4097 | The ordinance's "Appendix E, Development Application Checklists" is an attachment, pulled and parsed |
| Westfield | 319 agendas, 199 minutes, 23 resolutions; code via guid link | No review letters posted |
| Montclair | 281 agendas, 265 minutes, 399 resolutions on ecode360.com/MO0769/documents | Nothing on the town site itself beyond forms |

Things that only showed up on real sites and are now handled: CivicPlus
AgendaCenter returns only the current year unless you search year by year;
its minutes links are bare icons (the type is in the URL path); board
categories are exposed through `changeYear(year, id)` JavaScript; roll-call
lines read "Name, Role, Name, Role" so the phrase *after* a name wins;
letter authors must come from the signature block, not the cc list;
application forms name the applicant's team and the waiver *procedure*, so
they are excluded from the testify and waiver tables.

## robots.txt

The crawler honours robots.txt everywhere. That matters on eCode360: its
robots.txt closes the print view, attachments (`/attachment/`, where
Schedule-A checklists and bulk tables often live) and the public documents
portal (`/<CODE>/documents/`, where some towns such as Montclair publish
minutes and resolutions) to all bots. `njboards` therefore reads the code
through the ordinary chapter and article pages, lists attachment and portal
links in the profile without downloading them, and tells you in
`discovery.json` when a town's records sit on that portal so you can pull
them by hand.

## Reading the results honestly

This is regex-and-heuristics text mining over inconsistent municipal
documents. Expect:

- **Recall gaps**: scanned minutes need OCR; some sites hide documents behind
  JavaScript widgets the crawler does not execute; some towns post only
  agendas. The "What the town posts online" table tells you what was actually
  found, and `discovery.json` lists crawl errors.
- **Noise in names**: the professional extractor is conservative but will
  occasionally pick up a non-professional or mis-assign board vs. applicant
  side. Treat the "regularly testify" table as leads to confirm, not facts.
- **Waiver dispositions are sentence-level**: "did not support the waiver",
  "must be submitted" and "denied" count as not granted; "no objection",
  "granted", "waived" count as granted. Read the evidence column before
  relying on a row.
- **Comment grouping** uses a normalized key (first significant words with
  numbers stripped). Counts are "times a near-identical comment appeared".

Everything parsed is a public record obtained from the municipality's own
website or the published code. For towns that do not post review letters or
resolutions, the gap itself is useful information: request them under OPRA
(N.J.S.A. 47:1A-1 et seq.) for comparable recent applications.

## Configuration

Environment variables (all optional): `NJBOARDS_DATA` (data dir),
`NJBOARDS_UA` (set a contact address in the user agent — please do),
`NJBOARDS_DELAY` (seconds between requests per host, default 1.5),
`NJBOARDS_MAX_PAGES` (crawl budget per site, 150), `NJBOARDS_MAX_DOCS`,
`NJBOARDS_MIN_YEAR` (ignore documents older than this, 2019),
`NJBOARDS_OCR` (1 to OCR scanned PDFs), `NJBOARDS_ROBOTS` (0 to ignore
robots.txt — don't), `NJBOARDS_CACHE_TTL` (hours, default one week).

## Development

```bash
pip install -e .[dev]
pytest -q
```

The test suite runs fully offline against fixtures in `tests/fixtures/`
(fictional minutes, an engineer's review letter, a resolution, a checklist,
municipal and eCode360 HTML) and exercises the whole pipeline end to end,
including PDF generation and extraction. `examples/` holds the report that
pipeline produces.

## Layout

```
njboards/
  cli.py          command line (click)
  config.py       settings / paths
  db.py           SQLite registry of municipalities, boards, documents
  http.py         polite fetcher: robots.txt, rate limit, retries, cache
  municipalities.py  seed CSV, Wikipedia import, website lookup
  classify.py     link → doc type / board / date
  discover.py     site crawler + online-code detection
  platforms/      CivicPlus, CivicClerk, Granicus, eCode360 document portal adapters
  browser.py      headless Chromium fallback for JavaScript-challenge sites
  fetch.py        downloads
  extract.py      PDF / DOCX / HTML → text (+ optional OCR)
  parse/          common taxonomy + agenda, minutes, review_letter, resolution, checklist, professionals
  ecode.py        eCode360 chapters → topic index, checklist items, waiver provisions
  analyze.py      per-town aggregation
  report.py       Markdown + summary CSV
  pipeline.py     glue
data/municipalities.csv   seed list (edit freely)
examples/                 sample output
tests/                    offline test suite + fixtures
```
