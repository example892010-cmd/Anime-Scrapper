# Hindi Anime Zone Metadata Scraper

A small Python 3.10+ command-line project that searches Hindi Anime Zone and reads **page metadata only**: anime page titles/URLs, season information, visible episode labels, audio-language labels, and `480p` / `720p` / `1080p` quality labels.

It uses `requests` and `BeautifulSoup4`, has no database, no paid API, and does not download or process video.

## Important link limitation

The scraper intentionally does **not** extract, return, or follow direct streaming or file-download URLs. Hindi Anime Zone's public DMCA page says the site indexes content from non-affiliated third parties and shares links found elsewhere. That statement is not evidence that a particular file host or title is authorised for distribution. The project therefore prints the verified anime page URL and visible quality labels only. Its `authorised_download_links` field remains empty unless the project's source code is deliberately changed after independent rights verification.

Adult-labelled catalogue entries and detail pages are filtered out.

## Files

- `test_search.py` — command-line search program and offline self-test runner.
- `scraper.py` — HTTP client, site search/catalogue handling, HTML parser, and terminal formatter.
- `config.py` — website URL, timeouts, crawl limits, and safety filters.
- `requirements.txt` — Python dependencies.
- `tests/test_scraper.py` — offline parser tests using a small synthetic HTML fixture.

## Installation

### Windows (PowerShell or Command Prompt)

```powershell
cd hindi_anime_zone_scraper
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

### macOS / Linux

```bash
cd hindi_anime_zone_scraper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the offline tests

```bash
python test_search.py --self-test
```

The self-tests do not contact the live site. They check title normalisation, quality/language parsing, episode/season extraction, empty authorised-link output, and adult-label filtering.

## Search the live website

```bash
python test_search.py "Solo Leveling"
```

Other examples:

```bash
python test_search.py "Attack on Titan"
python test_search.py "Solo Level" --max-results 5
python test_search.py "Naruto" --catalogue-pages 30
```

The program first checks `robots.txt`, then looks for a same-site GET search form in the homepage HTML. If the form is absent or does not return matching pages, it falls back to the site's public `/anime-catalogue/` pagination (which uses the `haz_catalog_page` parameter). It opens only same-site anime page URLs found in the HTML and checks their HTTP responses before displaying them.

## Output and interpretation

- **Page URL** is the anime detail page on Hindi Anime Zone, not a media file.
- **Available episodes** are episode headings actually visible on the detail page. A stated total from the page's Series Info section is shown separately; it is not used to invent episode links.
- **Available qualities** are labels actually found in the title/episode content. The program does not manufacture a quality that is not present.
- **Audio languages** are taken from visible Dub/Sub/Language labels and episode text.
- **Authorised download links** are not displayed because page text alone cannot prove distribution permission.

## Polite access behaviour

- Checks `robots.txt` before crawling.
- Uses a descriptive user-agent.
- Waits at least 1.25 seconds between requests by default.
- Uses request timeouts and limited retries, including back-off for rate-limit/server responses.
- Limits catalogue pages (`25` by default; change in `config.py` or use `--catalogue-pages`).
- Does not bypass logins, bot checks, CAPTCHA, or other access controls.
- Rejects search-form actions and discovered page URLs outside the site's main host.

If the site changes its HTML, blocks automated requests, or disallows the crawler in `robots.txt`, the program reports the problem instead of attempting to bypass it.

## Test status for this package

The offline parser tests can be run with `python test_search.py --self-test`. A live Python HTTP integration test against `Solo Leveling` was **not completed in the package-authoring environment** because its DNS/network layer could not resolve `hindianimeszone.com`. The site was inspected through a browser-accessible view: the public catalogue and detail-page layout show title links, Series Info, episode headings, language labels, and quality labels. Run the live command above on a machine with normal access to the site to verify its current search endpoint and page responses.
