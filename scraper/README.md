# The polite scraper (FlyRank Internship · W5 · A9)

A small scraping pipeline: **fetch → extract → normalize → validate → store → report.**
It downloads the first 3 catalogue pages of Books to Scrape, visits all 60 book pages, and writes
clean, schema-checked JSON. It skips broken pages instead of crashing and ends each run with a report.

## Target classification

- **Site:** https://books.toscrape.com
- **Why it is allowed:** it is part of toscrape.com, a sandbox that says it exists so people can practise
  scraping on it. That statement on their own site is my permission.
- **How much:** only the first 3 catalogue pages (60 books).
- **Data collected:** title, price, availability, star rating, description, plus the source page and fetch time.
- **robots.txt check:** <!-- TODO: request https://books.toscrape.com/robots.txt once and write what happened
  (status code and what it said). If it is missing, write "no robots file found". -->
- I will not reuse this code on another site without checking its rules and terms first.

## Run it (under 5 minutes)

Python lane, Python 3.10+.

```bash
git clone <your-repo-url> && cd <your-repo>/scraper
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python src/main.py
```

Outputs: `output/books.json`, `output/errors.json`, `output/run-report.json`.
The first run fetches ~63 pages (about 40 seconds because of the polite delay). Reruns read from `cache/`.

Test the failure handling by adding one made-up URL (only your own side breaks, the real site is not hammered):

```bash
python src/main.py --extra-url https://books.toscrape.com/catalogue/not-a-real-book_0/index.html
```

Run the tests (no network needed): `python -m pytest`

## Record schema

| Field | Type | Notes |
|---|---|---|
| title | string | required |
| product_url | string | absolute `https://` URL, the record's identity |
| price_text | string | raw, e.g. `£51.77` |
| price_gbp | number | clean value parsed from `price_text` |
| availability_text | string | raw, e.g. `In stock (22 available)` |
| rating_text | string | one of One…Five |
| description | string or null | the only optional field; `null` when the page has none |
| source_page | string | catalogue page the link came from (provenance) |
| fetched_at | string | UTC time the page was first downloaded (provenance) |

Defined with Pydantic in `src/models.py`. A record that fails goes to `output/errors.json` with the reasons.

## Politeness rules

- Honest `User-Agent` naming the project and linking to this repo.
- 10 second timeout on every request.
- At least 0.6 seconds between real requests (cached pages need no delay).
- Status code checked first: only `200` is parsed. Only `200` responses are cached.
- One retry for timeouts and 5xx. No retry for 404 or 403.
- Development reads from `cache/` so the site is asked once, not fifty times.

## Proof: a real run report

<!-- TODO: paste your own output/run-report.json here after a real run. -->

## Why no browser

The data is already in the HTML the server sends, so a browser would only add cost.

## Limitation

<!-- TODO: one honest limitation, in your own words. Example candidates: the selectors depend on the
site's current HTML layout; the single retry has no backoff or Retry-After handling; cached pages are
never refreshed until you delete cache/. -->

## Ethics note

<!-- TODO: write this in your own words. Points to cover: use an official API when one exists; never
bypass logins, paywalls or blocks; collect only what you need. -->
