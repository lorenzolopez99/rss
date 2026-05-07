"""Per-feed JSON cache so we don't re-scrape the same article on every run."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .extractors.ap import Article


def _serialize(art: Article) -> dict:
    d = asdict(art)
    d["published"] = art.published.astimezone(timezone.utc).isoformat()
    return d


def _deserialize(d: dict) -> Article:
    return Article(
        url=d["url"],
        title=d["title"],
        body_html=d["body_html"],
        image_url=d.get("image_url"),
        published=datetime.fromisoformat(d["published"]),
        author=d.get("author"),
        summary=d.get("summary"),
        tags=d.get("tags") or [],  # default for caches written before tags existed
    )


def load(path: Path) -> list[Article]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [_deserialize(item) for item in raw.get("articles", [])]


def save(path: Path, articles: list[Article]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "articles": [_serialize(a) for a in articles],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


# Hold ~10x feed length in cache so AP's hub-page churn doesn't push articles out
# of the dedup set and cause us to re-scrape them forever.
CACHE_MULTIPLIER = 10


def merge(existing: list[Article], fresh: list[Article], max_items: int) -> list[Article]:
    """Combine cached + new articles, dedupe by URL, sort newest first, truncate cache."""
    by_url: dict[str, Article] = {a.url: a for a in existing}
    for a in fresh:  # fresh wins on conflict (re-scrape gets newer body)
        by_url[a.url] = a
    merged = sorted(by_url.values(), key=lambda x: x.published, reverse=True)
    return merged[: max_items * CACHE_MULTIPLIER]
