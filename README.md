# Personal RSS

A self-hosted RSS factory: scrape sites that don't ship usable feeds, generate clean RSS XML with **full article text + images**, and serve it from GitHub Pages so any RSS reader on your phone can subscribe.

Built first for **Associated Press** (`apnews.com`). The extractor architecture is modular — drop in a new module under `src/extractors/` to support another publisher, then add a feed entry to `feeds.yml`.

## How it works

```
feeds.yml ──► src/main.py ──► extractors/<name>.py ──► state/<slug>.json
                                                  │
                                                  └──► docs/<slug>.xml  ← served by GitHub Pages
```

A GitHub Actions workflow runs every 30 minutes, regenerates the XML, commits the result back to the repo, and publishes `docs/` to Pages.

## One-time setup

1. **Create a GitHub repo and push this directory.**

   ```bash
   gh repo create my-rss --public --source=. --remote=origin --push
   ```

2. **Enable GitHub Pages** for the repo: Settings → Pages → Source = "GitHub Actions".

3. **Allow Actions to commit back**: Settings → Actions → General → Workflow permissions → "Read and write permissions".

4. Push to `main`. The first workflow run scrapes AP, generates `docs/ap-top.xml`, and publishes it. Watch it under the Actions tab.

5. Your feed lives at `https://<your-username>.github.io/<repo-name>/ap-top.xml`.

## Subscribing on your phone

Any RSS reader that supports `content:encoded` will render the full article body and images. Recommended:

- **NetNewsWire** (iOS, free, open source) — paste the feed URL into Add Feed.
- **Reeder**, **Unread**, **Feedly**, **Inoreader** — all work too.

## Adding more feeds

Edit `feeds.yml` and append:

```yaml
- slug: ap-politics
  title: "AP Politics"
  description: "Politics from the Associated Press."
  extractor: ap
  hub_url: "https://apnews.com/politics"
  max_items: 30
```

Commit and push — the next scheduled run picks it up and produces `docs/ap-politics.xml`.

To support a different publisher, copy `src/extractors/ap.py` to e.g. `src/extractors/reuters.py`, rewrite the two functions (`discover_article_urls`, `fetch_article`) for that site, register it in `src/extractors/__init__.py`, and reference it as `extractor: reuters` in `feeds.yml`.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
SITE_BASE_URL="https://example.github.io/rss" python -m src.main
# → writes docs/*.xml and updates state/*.json
```

`--only <slug>` runs a single feed, e.g. `python -m src.main --only ap-top`.

## Notes

- AP has no official public RSS for `apnews.com`; this scrapes the hub HTML and per-article JSON-LD. If AP changes their markup the extractor may need a tweak (`src/extractors/ap.py`).
- State JSON is committed so cached articles persist across runs (important for stable pubDates and dedup).
- Be polite: `delay_sec=0.4` between article fetches keeps the load light.
