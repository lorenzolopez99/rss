"""Entry point: read feeds.yml, scrape each feed, render XML to docs/."""

from __future__ import annotations

import argparse
import os
import sys
from html import escape
from pathlib import Path

import yaml

from . import extractors, feed_builder, state

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "feeds.yml"
DOCS = ROOT / "docs"
STATE_DIR = ROOT / "state"


def site_base_url() -> str:
    """The public base URL where docs/ is served. Override via env so feeds carry a real `self` link."""
    return os.environ.get("SITE_BASE_URL", "https://example.github.io/rss").rstrip("/")


def run_one(cfg: dict, base_url: str) -> int:
    slug = cfg["slug"]
    print(f"\n→ {slug}: {cfg['hub_url']}")
    extractor = extractors.get(cfg["extractor"])
    state_path = STATE_DIR / f"{slug}.json"
    cached = state.load(state_path)
    known = {a.url for a in cached}

    max_items = cfg.get("max_items", 30)
    fresh = extractor.fetch_feed(
        cfg["hub_url"],
        max_items=max_items,
        known_urls=known,
    )
    print(f"  + {len(fresh)} new article(s); {len(cached)} cached")

    merged = state.merge(cached, fresh, max_items)
    state.save(state_path, merged)

    feed_builder.build(
        out_path=DOCS / f"{slug}.xml",
        feed_title=cfg["title"],
        feed_description=cfg["description"],
        site_url=cfg["hub_url"],
        self_url=f"{base_url}/{slug}.xml",
        articles=merged[:max_items],  # publish the freshest N; cache holds more
    )
    print(f"  → wrote docs/{slug}.xml ({min(len(merged), max_items)} items)")
    return len(fresh)


def write_index(feeds: list[dict], base_url: str) -> None:
    rows = []
    for f in feeds:
        slug = f["slug"]
        rows.append(
            f'<li><a href="{slug}.xml">{escape(f["title"])}</a> '
            f'— <span class="muted">{escape(f["description"])}</span></li>'
        )
    html = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Personal RSS feeds</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 42rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .muted {{ color: #666; }}
  code {{ background: #f3f3f3; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin: 0.5rem 0; }}
</style>
<h1>Personal RSS feeds</h1>
<p class="muted">Subscribe in your RSS reader by adding the feed URL below.</p>
<ul>
{chr(10).join(rows)}
</ul>
<p class="muted">Base URL: <code>{escape(base_url)}</code></p>
"""
    (DOCS / "index.html").write_text(html)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Run a single feed slug")
    args = parser.parse_args()

    if not CONFIG.exists():
        print(f"missing {CONFIG}", file=sys.stderr)
        return 1
    config = yaml.safe_load(CONFIG.read_text())
    feeds = config.get("feeds", [])
    if args.only:
        feeds = [f for f in feeds if f["slug"] == args.only]
        if not feeds:
            print(f"no feed with slug '{args.only}'", file=sys.stderr)
            return 1

    base_url = site_base_url()
    DOCS.mkdir(exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)

    total_new = 0
    for cfg in feeds:
        try:
            total_new += run_one(cfg, base_url)
        except Exception as exc:  # one bad feed shouldn't kill the rest
            print(f"  ! {cfg.get('slug')} failed: {exc}", file=sys.stderr)

    write_index(config.get("feeds", []), base_url)
    print(f"\nDone. {total_new} new article(s) across {len(feeds)} feed(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
