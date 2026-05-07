"""Render a list of Articles into RSS XML using feedgen."""

from __future__ import annotations

from pathlib import Path

from feedgen.feed import FeedGenerator

from .extractors.ap import Article


def build(
    *,
    out_path: Path,
    feed_title: str,
    feed_description: str,
    site_url: str,
    self_url: str,
    articles: list[Article],
) -> None:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.id(self_url)
    fg.title(feed_title)
    fg.description(feed_description)
    fg.link(href=site_url, rel="alternate")
    fg.link(href=self_url, rel="self")
    fg.language("en")

    # feedgen requires entries oldest-first; it reverses on output.
    for art in reversed(articles):
        fe = fg.add_entry()
        fe.id(art.url)
        fe.title(art.title)
        fe.link(href=art.url)
        fe.published(art.published)
        fe.updated(art.published)
        if art.author:
            fe.author({"name": art.author})
        if art.summary:
            fe.description(art.summary)
        # Full article body in content:encoded — readers like NetNewsWire / Reeder render this.
        fe.content(art.body_html, type="CDATA")
        if art.image_url:
            fe.enclosure(art.image_url, 0, "image/jpeg")
        for tag in art.tags:
            fe.category({"term": tag})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(out_path), pretty=True)
