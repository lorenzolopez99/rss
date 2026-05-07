"""Scraper for apnews.com hub pages and articles."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Comment, Tag

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
ARTICLE_HREF_RE = re.compile(r"^https?://apnews\.com/article/[^\"#?]+")

# Tags allowed in the rendered article body. Everything else is unwrapped.
ALLOWED_BODY_TAGS = {
    "p", "h2", "h3", "h4", "ul", "ol", "li",
    "blockquote", "figure", "figcaption", "img",
    "em", "strong", "b", "i", "a", "br",
}


@dataclass
class Article:
    url: str
    title: str
    body_html: str  # full content including embedded images
    image_url: str | None
    published: datetime  # timezone-aware UTC
    author: str | None
    summary: str | None
    tags: list[str]  # categorization terms (people, topics, sections)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def discover_article_urls(hub_url: str, session: requests.Session | None = None) -> list[str]:
    """Return de-duplicated article URLs found on the AP hub/topic page, preserving order."""
    sess = session or _session()
    resp = sess.get(hub_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(hub_url, href)
        # strip fragments / query
        full = full.split("#", 1)[0].split("?", 1)[0]
        if not ARTICLE_HREF_RE.match(full):
            continue
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)
    return urls


def _parse_jsonld(soup: BeautifulSoup) -> dict | None:
    """Return the first JSON-LD object that looks like a NewsArticle/Article."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if t == "NewsArticle" or t == "Article" or (isinstance(t, list) and any(x in t for x in ("NewsArticle", "Article"))):
                return item
    return None


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Python's fromisoformat handles "2026-05-07T17:53:19Z" only on 3.11+ if Z replaced.
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _clean_body(container: Tag, hero_url: str | None = None) -> str:
    """Whitelist allowed tags; rewrite img URLs to absolute; strip empty paragraphs.

    If `hero_url` is given, inject it as a leading <p><img></p>, but only after
    image-dedup runs — so if the same asset is also in the body, we keep just one.
    """
    # drop scripts/styles/aside/template blocks. <template> in particular hides
    # an image-carousel "Read More" button that BeautifulSoup happily pulls into
    # the visible text otherwise.
    for bad in container.find_all(["script", "style", "aside", "noscript", "template"]):
        bad.decompose()
    # strip HTML comments (e.g. `<!-- AP "Read More" embed -->`)
    for c in container.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # AP injects various non-article modules inside RichTextStoryBody. Drop them
    # by class-hint substring match:
    #   - PagePromo / Page-actions / Related / Advertisement: tiles for other articles
    #   - PageList*           : the "Related Stories" hub-peek list at article end
    #   - HtmlModule / HTMLModuleEnhancement: "Read More" mid-article embed
    #   - Newsletter          : signup boxes
    bad_hints = (
        "Related", "Newsletter", "Advertisement", "ad-", "Ad-",
        "Page-actions", "PagePromo", "PageList",
        "HtmlModule", "HTMLModule",
    )
    to_drop = []
    for el in container.find_all(attrs={"class": True}):
        if el.attrs is None:
            continue
        classes = " ".join(el.get("class") or [])
        if any(hint in classes for hint in bad_hints):
            to_drop.append(el)
    for el in to_drop:
        el.decompose()

    # unwrap any tag not in allowlist (keeps its text content)
    for el in list(container.find_all(True)):
        if el.name not in ALLOWED_BODY_TAGS:
            el.unwrap()

    def _asset_key(url: str) -> str:
        # The underlying asset URL is encoded after `?url=` in dims.apnews.com
        # transforms; everything before it is just a per-render cache key.
        m = re.search(r"[?&]url=([^&]+)", url)
        return m.group(1) if m else url

    # absolute-ize image src and dedupe (AP renders each photo twice in <picture>
    # responsive variants — the same image asset shows up under two different
    # dims.apnews.com transform URLs).
    seen_assets: set[str] = set()
    if hero_url:
        seen_assets.add(_asset_key(hero_url))
    for img in list(container.find_all("img")):
        src = img.get("src") or img.get("data-src")
        if not src:
            img.decompose()
            continue
        src = urljoin("https://apnews.com/", src)
        key = _asset_key(src)
        if key in seen_assets:
            img.decompose()
            continue
        seen_assets.add(key)
        img["src"] = src
        for attr in list(img.attrs):
            if attr not in ("src", "alt"):
                del img[attr]

    # drop anchor tracking attrs but keep href; drop empty anchors used as jump targets
    for a in list(container.find_all("a")):
        if not a.get_text(strip=True) and not a.find("img"):
            a.decompose()
            continue
        for attr in list(a.attrs):
            if attr != "href":
                del a[attr]

    # trim empty <p>
    for p in list(container.find_all("p")):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    inner = container.decode_contents().strip()
    if hero_url:
        inner = f'<p><img src="{hero_url}" alt="" /></p>\n{inner}'
    return inner


def fetch_article(url: str, session: requests.Session | None = None) -> Article | None:
    sess = session or _session()
    resp = sess.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    jsonld = _parse_jsonld(soup) or {}

    title = (
        jsonld.get("headline")
        or _meta(soup, "og:title")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    if not title:
        return None

    image_url = _meta(soup, "og:image")
    if not image_url:
        img = jsonld.get("image")
        if isinstance(img, dict):
            image_url = img.get("url")
        elif isinstance(img, list) and img:
            first = img[0]
            image_url = first.get("url") if isinstance(first, dict) else first
        elif isinstance(img, str):
            image_url = img

    published = (
        _parse_iso_date(jsonld.get("datePublished"))
        or _parse_iso_date(_meta(soup, "article:published_time"))
        or datetime.now(timezone.utc)
    )

    author = None
    a = jsonld.get("author")
    if isinstance(a, dict):
        author = a.get("name")
    elif isinstance(a, list) and a:
        first = a[0]
        author = first.get("name") if isinstance(first, dict) else (first if isinstance(first, str) else None)
    elif isinstance(a, str):
        author = a

    summary = _meta(soup, "og:description") or jsonld.get("description")

    # Tags: AP's keywords (people, topics) + articleSection (top-level section).
    # Both fields can be a string or list of strings.
    raw_tags: list[str] = []
    for field in ("articleSection", "keywords"):
        v = jsonld.get(field)
        if isinstance(v, str):
            raw_tags.extend(t.strip() for t in v.split(",") if t.strip())
        elif isinstance(v, list):
            raw_tags.extend(str(t).strip() for t in v if str(t).strip())
    # case-insensitive dedup, keep first-seen casing and order
    tags: list[str] = []
    seen_tags: set[str] = set()
    for t in raw_tags:
        key = t.lower()
        if key not in seen_tags:
            seen_tags.add(key)
            tags.append(t)

    body_div = soup.find("div", class_="RichTextStoryBody") or soup.find("div", class_="Page-storyBody")
    if not body_div:
        return None
    body_html = _clean_body(body_div, hero_url=image_url)
    if not body_html:
        return None

    return Article(
        url=url,
        title=title,
        body_html=body_html,
        image_url=image_url,
        published=published,
        author=author,
        summary=summary,
        tags=tags,
    )


def fetch_feed(hub_url: str, max_items: int, *, known_urls: set[str], delay_sec: float = 0.4) -> list[Article]:
    """Discover articles on the hub and fetch any not already in `known_urls`.

    Returns the list of newly-fetched Articles (caller merges with cached state).
    """
    sess = _session()
    candidates = discover_article_urls(hub_url, sess)
    fresh: list[Article] = []
    for url in candidates:
        if url in known_urls:
            continue
        if len(fresh) >= max_items:
            break  # cap per-run scrapes; remaining URLs picked up next cycle if needed
        try:
            art = fetch_article(url, sess)
        except requests.RequestException as exc:
            print(f"  ! failed {url}: {exc}")
            continue
        if art is None:
            continue
        fresh.append(art)
        time.sleep(delay_sec)  # be polite
    return fresh
