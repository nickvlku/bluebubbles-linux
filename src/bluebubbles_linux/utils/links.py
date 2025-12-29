"""Link detection and preview utilities."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, quote

import httpx

# URL regex pattern
URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+'
    r'|www\.[^\s<>"{}|\\^`\[\]]+'
)

# oEmbed endpoints for specific sites
OEMBED_ENDPOINTS = {
    "tiktok.com": "https://www.tiktok.com/oembed?url={url}",
    "www.tiktok.com": "https://www.tiktok.com/oembed?url={url}",
    "vm.tiktok.com": "https://www.tiktok.com/oembed?url={url}",
    "youtube.com": "https://www.youtube.com/oembed?url={url}&format=json",
    "www.youtube.com": "https://www.youtube.com/oembed?url={url}&format=json",
    "youtu.be": "https://www.youtube.com/oembed?url={url}&format=json",
    "twitter.com": "https://publish.twitter.com/oembed?url={url}",
    "x.com": "https://publish.twitter.com/oembed?url={url}",
}

# Cache for link previews (in-memory + SQLite)
_preview_cache: dict[str, tuple[LinkPreview, float]] = {}
CACHE_TTL = 86400  # 24 hours


@dataclass
class LinkPreview:
    """Metadata extracted from a URL."""
    url: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None


def find_urls(text: str) -> list[tuple[int, int, str]]:
    """
    Find all URLs in text.

    Returns list of (start, end, url) tuples.
    """
    results = []
    for match in URL_PATTERN.finditer(text):
        url = match.group()
        # Add https:// if it starts with www.
        if url.startswith("www."):
            url = "https://" + url
        results.append((match.start(), match.end(), url))
    return results


def _get_cache_path() -> Path:
    """Get the path to the link preview cache database."""
    cache_dir = Path.home() / ".cache" / "bluebubbles-linux"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "link_previews.db"


def _init_cache_db() -> sqlite3.Connection:
    """Initialize the cache database."""
    conn = sqlite3.connect(_get_cache_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS link_previews (
            url TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            image_url TEXT,
            site_name TEXT,
            fetched_at REAL
        )
    """)
    conn.commit()
    return conn


def _get_cached_preview(url: str) -> LinkPreview | None:
    """Get a cached preview if it exists and is not expired."""
    # Check in-memory cache first
    if url in _preview_cache:
        preview, fetched_at = _preview_cache[url]
        if time.time() - fetched_at < CACHE_TTL:
            return preview

    # Check SQLite cache
    try:
        conn = _init_cache_db()
        cursor = conn.execute(
            "SELECT title, description, image_url, site_name, fetched_at FROM link_previews WHERE url = ?",
            (url,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            title, description, image_url, site_name, fetched_at = row
            if time.time() - fetched_at < CACHE_TTL:
                preview = LinkPreview(
                    url=url,
                    title=title,
                    description=description,
                    image_url=image_url,
                    site_name=site_name
                )
                # Update in-memory cache
                _preview_cache[url] = (preview, fetched_at)
                return preview
    except Exception as e:
        print(f"Error reading link preview cache: {e}")

    return None


def _save_preview_to_cache(preview: LinkPreview) -> None:
    """Save a preview to both in-memory and SQLite cache."""
    now = time.time()
    _preview_cache[preview.url] = (preview, now)

    try:
        conn = _init_cache_db()
        conn.execute("""
            INSERT OR REPLACE INTO link_previews
            (url, title, description, image_url, site_name, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (preview.url, preview.title, preview.description,
              preview.image_url, preview.site_name, now))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving link preview to cache: {e}")


async def _fetch_oembed(url: str, oembed_url: str, timeout: float) -> LinkPreview | None:
    """Fetch preview using oEmbed API."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(
                oembed_url.format(url=quote(url, safe="")),
                headers={"Accept": "application/json"}
            )

            if response.status_code != 200:
                return None

            data = response.json()

            # Extract info from oEmbed response
            preview = LinkPreview(url=url)
            preview.title = data.get("title")
            preview.site_name = data.get("provider_name") or data.get("author_name")
            preview.image_url = data.get("thumbnail_url")

            # For TikTok, try to get author info as description
            if "tiktok" in url.lower():
                author = data.get("author_name", "")
                if author:
                    preview.description = f"Video by @{author}"

            return preview
    except Exception as e:
        print(f"oEmbed fetch failed for {url}: {e}")
        return None


async def _fetch_html_preview(url: str, timeout: float) -> LinkPreview | None:
    """Fetch preview by scraping HTML meta tags."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; BlueBubbles/1.0)",
                    "Accept": "text/html",
                }
            )

            if response.status_code != 200:
                return None

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None

            html = response.text[:50000]  # Limit to first 50KB

            preview = LinkPreview(url=url)

            # Extract Open Graph tags
            og_patterns = {
                "title": re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
                "description": re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
                "image": re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
                "site_name": re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', re.I),
            }

            # Also try content before property order
            og_patterns_alt = {
                "title": re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', re.I),
                "description": re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', re.I),
                "image": re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
                "site_name": re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:site_name["\']', re.I),
            }

            for key, pattern in og_patterns.items():
                match = pattern.search(html)
                if not match:
                    match = og_patterns_alt[key].search(html)
                if match:
                    setattr(preview, key if key != "image" else "image_url", match.group(1))

            # Fallback to standard meta tags if OG not found
            if not preview.title:
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
                if title_match:
                    preview.title = title_match.group(1).strip()

            if not preview.description:
                desc_match = re.search(
                    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                    html, re.I
                )
                if not desc_match:
                    desc_match = re.search(
                        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
                        html, re.I
                    )
                if desc_match:
                    preview.description = desc_match.group(1)

            # Get site name from domain if not found
            if not preview.site_name:
                parsed = urlparse(url)
                preview.site_name = parsed.netloc

            # Make relative image URLs absolute
            if preview.image_url and not preview.image_url.startswith(("http://", "https://")):
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                if preview.image_url.startswith("/"):
                    preview.image_url = base + preview.image_url
                else:
                    preview.image_url = base + "/" + preview.image_url

            return preview

    except Exception as e:
        print(f"Error fetching HTML preview for {url}: {e}")
        return None


async def fetch_link_preview(url: str, timeout: float = 5.0) -> LinkPreview | None:
    """
    Fetch link preview metadata from a URL.

    Uses oEmbed for supported sites (TikTok, YouTube, Twitter),
    falls back to HTML meta tag scraping, and caches results.
    """
    # Check cache first
    cached = _get_cached_preview(url)
    if cached:
        return cached

    preview = None

    # Check if this URL has an oEmbed endpoint
    parsed = urlparse(url)
    oembed_url = OEMBED_ENDPOINTS.get(parsed.netloc)

    if oembed_url:
        # Try oEmbed first for supported sites
        preview = await _fetch_oembed(url, oembed_url, timeout)

    if not preview:
        # Fall back to HTML scraping
        preview = await _fetch_html_preview(url, timeout)

    # Cache the result if we got something useful
    if preview and (preview.title or preview.description):
        _save_preview_to_cache(preview)

    return preview
