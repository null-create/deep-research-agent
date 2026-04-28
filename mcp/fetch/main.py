"""
Web Scraper MCP Server using FastMCP

A Model Context Protocol server that provides web scraping capabilities.
Allows agents to fetch and read the contents of web pages.
"""

import os
import asyncio
import logging
import random
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv
from bs4 import BeautifulSoup, Tag
from mcp.server import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HOST_ADDR = os.getenv("HOST_ADDR", "0.0.0.0")
HOST_PORT = int(os.getenv("HOST_PORT", 9292))

# Create the FastMCP server
mcp = FastMCP(name="Web Scraper", host=HOST_ADDR, port=HOST_PORT, log_level="INFO")

# User-Agent pool — modern browser strings rotated per request to reduce bot-detection blocks
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Concurrency cap: at most 3 simultaneous scrape requests to prevent burst 429s
_scrape_semaphore = asyncio.Semaphore(3)

# Per-domain last-request timestamp for minimum request interval enforcement
_domain_last_request: dict[str, float] = {}
_DOMAIN_MIN_INTERVAL = 2.0  # seconds between requests to the same domain

# Truncate extracted page text to prevent log bloat (100 KB)
_SCRAPE_MAX_CHARS = int(os.getenv("SCRAPE_MAX_CHARS", 100000))
_SCRAPE_TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", 10))

# Content-Type prefixes we can meaningfully extract text from.
# Everything else (PDF, images, video, binary) is rejected early.
_TEXTUAL_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
    "application/rss+xml",
    "application/atom+xml",
)

# Elements that carry boilerplate, not article content
_BOILERPLATE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript"]

# CSS class / id substrings that indicate non-content containers
_BOILERPLATE_PATTERNS = re.compile(
    r"cookie|consent|banner|popup|modal|sidebar|advert|newsletter|promo|social-share|"
    r"share-button|related-posts|comment|breadcrumb|pagination|signup|subscribe",
    re.IGNORECASE,
)

# CSS class / id substrings that indicate a likely primary content container
_CONTENT_PATTERNS = re.compile(
    r"\b(content|article|post|entry|story|prose)\b",
    re.IGNORECASE,
)

# HTTP client — User-Agent injected per request from pool above
client = httpx.AsyncClient(
    timeout=_SCRAPE_TIMEOUT,
    follow_redirects=True,
)


def _pick_ua() -> str:
    return random.choice(_USER_AGENTS)


async def _enforce_domain_rate(domain: str) -> None:
    """Sleep if necessary to respect the per-domain minimum request interval."""
    now = time.monotonic()
    last = _domain_last_request.get(domain, 0.0)
    wait = (last + _DOMAIN_MIN_INTERVAL) - now
    # Update before sleeping so concurrent callers for the same domain chain their waits
    _domain_last_request[domain] = max(now, last + _DOMAIN_MIN_INTERVAL)
    if wait > 0:
        await asyncio.sleep(wait)


async def _fetch_with_retry(url: str) -> httpx.Response:
    """Fetch a URL with UA rotation and one automatic retry on HTTP 429."""
    headers = {"User-Agent": _pick_ua()}
    response = await client.get(url, headers=headers)

    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", "3"))
        wait = min(retry_after, 10)
        logger.info("scrape_url 429 for %s — retrying after %ss", url, wait)
        await asyncio.sleep(wait)
        headers = {"User-Agent": _pick_ua()}
        response = await client.get(url, headers=headers)

    response.raise_for_status()
    return response


def _is_textual_content(content_type: str) -> bool:
    """Return True if the Content-Type header indicates parseable text."""
    ct = content_type.lower().split(";")[0].strip()
    return ct.startswith(_TEXTUAL_CONTENT_TYPES)


def _is_binary_content(raw: bytes) -> bool:
    """Return True if the response body contains null bytes in the first 8 KB.

    Null bytes are absent from all valid UTF-8/latin-1 HTML and plain text but
    present in virtually every binary format (PDF, images, archives, etc.).
    This catches mislabeled Content-Type headers where a server sends binary
    data under a text/* or application/xhtml+xml type.
    """
    return b"\x00" in raw[:8192]


def _remove_boilerplate(soup: BeautifulSoup) -> None:
    """Remove structural noise elements in-place before text extraction."""
    # Standard non-content tags
    for tag in soup(_BOILERPLATE_TAGS):
        tag.decompose()

    # Elements whose class or id matches common boilerplate patterns.
    # Guard with try/except: after iterative decompose(), some elements in the
    # snapshot list may have corrupted internal state (attrs=None) causing
    # AttributeError: 'NoneType' object has no attribute 'get'.
    for el in soup.find_all(True):
        try:
            classes = " ".join(el.get("class") or [])
            el_id = el.get("id") or ""
            if _BOILERPLATE_PATTERNS.search(classes) or _BOILERPLATE_PATTERNS.search(
                el_id
            ):
                el.decompose()
        except Exception:
            continue


def _link_density(el: Tag) -> float:
    """Fraction of el's visible text that lives inside <a> tags (0.0–1.0)."""
    total = len(el.get_text(strip=True))
    if not total:
        return 0.0
    link_chars = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))
    return min(link_chars / total, 1.0)


def _content_score(el: Tag) -> float:
    """Composite score: text volume boosted by paragraph count, penalized by link density.

    Each <p> tag contributes a 50-char bonus — a reliable proxy for article richness.
    High link density (navigation menus) is heavily penalized.
    """
    text_len = len(el.get_text(strip=True))
    p_count = len(el.find_all("p"))
    return (text_len + p_count * 50) * (1.0 - _link_density(el))


def _extract_main_content(soup: BeautifulSoup) -> Tag | None:
    """Try to locate the primary content container.

    Checks in order:
      1. Semantic elements (<article>, <main>, role=main, content/post id)
      2. Positive class/id patterns (content, article, post, entry, story, prose)
      3. Highest-scoring block by text volume, paragraph count, and link density
    Returns None if nothing convincing is found (caller falls back to full page).
    """
    # Stage 1: semantic containers
    for selector in [
        soup.find("article"),
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.find(attrs={"id": re.compile(r"content|article|post|entry", re.I)}),
    ]:
        if selector and len(selector.get_text(strip=True)) > 200:
            return selector

    # Stage 2: positive class / id heuristic — names that strongly imply article content
    for el in soup.find_all(["div", "section"]):
        try:
            classes = " ".join(el.get("class") or [])
            el_id = el.get("id") or ""
            if _CONTENT_PATTERNS.search(classes) or _CONTENT_PATTERNS.search(el_id):
                if len(el.get_text(strip=True)) > 200 and _link_density(el) < 0.5:
                    return el
        except Exception:
            continue

    # Stage 3: score all block-level candidates by content score
    candidates = soup.find_all(["div", "section"])
    if not candidates:
        return None
    try:
        best = max(candidates, key=_content_score)
        score = _content_score(best)
    except Exception:
        return None
    if score > 300:
        return best
    return None


def _extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """Pull structured page metadata useful for research context."""
    meta: dict[str, str] = {}

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        meta["title"] = title_tag.string.strip()

    # <meta name="description"> or <meta property="og:description">
    for attr, val in [("name", "description"), ("property", "og:description")]:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            meta["description"] = tag["content"].strip()
            break

    # Publish date — common meta tags
    for attr, val in [
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "publish_date"),
        ("name", "DC.date"),
        ("itemprop", "datePublished"),
    ]:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            meta["published"] = tag["content"].strip()
            break

    # Author
    for attr, val in [("name", "author"), ("property", "article:author")]:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            meta["author"] = tag["content"].strip()
            break

    return meta


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for Docker"""
    return JSONResponse({"status": "healthy", "service": "web-scraper-server"})


@mcp.tool(
    name="fetch_url",
    description="""Fetch and extract the text content of a web page. 
    Rejects non-text content (PDFs, images, binary files). Strips boilerplate 
    (nav, footer, ads, cookie banners) and extracts the main article content when possible.""",
)
async def fetch_url(
    url: str,
    include_links: bool = False,
    include_images: bool = False,
    clean_text: bool = True,
) -> str:
    """
    Fetch and extract the text content of a web page.

    Args:
        url: The URL of the web page to scrape
        include_links: Whether to include links found on the page
        include_images: Whether to include image URLs found on the page
        clean_text: Whether to clean and format the extracted text

    Returns:
        The extracted text content of the web page
    """
    try:
        # Validate URL
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError("Invalid URL format")

        # Fetch the page (rate-limited, concurrency-capped, with retry on 429)
        async with _scrape_semaphore:
            await _enforce_domain_rate(parsed_url.netloc)
            response = await _fetch_with_retry(url)

        # Reject non-textual content types (PDF, images, binary, etc.)
        content_type = response.headers.get("content-type", "")
        if not _is_textual_content(content_type):
            ct_short = content_type.split(";")[0].strip()
            return (
                f"URL: {url}\n"
                f"Content-Type: {ct_short}\n\n"
                f"[SKIPPED] Non-text content type '{ct_short}' — "
                f"cannot extract readable text. This may be a PDF, image, "
                f"or binary file."
            )

        # Secondary guard: detect binary content regardless of Content-Type header.
        # Some servers mislabel PDFs/images as text/html or text/plain. Null bytes
        # are a reliable indicator — they never appear in valid HTML or plain text.
        if _is_binary_content(response.content):
            ct_short = content_type.split(";")[0].strip() or "unknown"
            return (
                f"URL: {url}\n"
                f"Content-Type: {ct_short}\n\n"
                f"[SKIPPED] Response body contains binary data despite Content-Type "
                f"'{ct_short}' — cannot extract readable text."
            )

        # Parse HTML and extract content.
        # Fall back to regex-based stripping if BS4 processing raises any
        # error (e.g. AttributeError from corrupted element state on
        # malformed pages).
        soup = None
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            metadata = _extract_metadata(soup)
            _remove_boilerplate(soup)
            main_el = _extract_main_content(soup)
            content_source = main_el if main_el is not None else soup
            text_content = content_source.get_text()
        except Exception:
            metadata = {}
            text_content = re.sub(r"<[^>]+>", " ", response.text)
            text_content = re.sub(r"[ \t]+", " ", text_content)

        if clean_text:
            # Clean up the text
            lines = (line.strip() for line in text_content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text_content = "\n".join(chunk for chunk in chunks if chunk)

        # Cap output size to prevent context bloat from large pages
        if len(text_content) > _SCRAPE_MAX_CHARS:
            original_len = len(text_content)
            text_content = (
                text_content[:_SCRAPE_MAX_CHARS]
                + f"\n[... truncated — original {original_len} chars]"
            )

        # Build structured response
        result_parts = [f"URL: {url}"]
        if metadata.get("title"):
            result_parts.append(f"Title: {metadata['title']}")
        if metadata.get("author"):
            result_parts.append(f"Author: {metadata['author']}")
        if metadata.get("published"):
            result_parts.append(f"Published: {metadata['published']}")
        if metadata.get("description"):
            result_parts.append(f"Description: {metadata['description']}")

        result_parts.append("\n--- PAGE CONTENT ---")
        result_parts.append(text_content)

        # Include links if requested
        if include_links and soup is not None:
            links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                text = link.get_text().strip()
                # Convert relative URLs to absolute
                absolute_url = urljoin(url, href)
                links.append(f"{text}: {absolute_url}")

            if links:
                result_parts.append("\n--- LINKS ---")
                result_parts.extend(links)

        # Include images if requested
        if include_images and soup is not None:
            images = []
            for img in soup.find_all("img", src=True):
                src = img["src"]
                alt = img.get("alt", "No alt text")
                # Convert relative URLs to absolute
                absolute_url = urljoin(url, src)
                images.append(f"{alt}: {absolute_url}")

            if images:
                result_parts.append("\n--- IMAGES ---")
                result_parts.extend(images)

        return "\n".join(result_parts)

    except httpx.HTTPStatusError as e:
        raise ValueError(
            f"HTTP Error {e.response.status_code}: {e.response.reason_phrase} — {url}"
        )
    except httpx.RequestError as e:
        raise ValueError(f"Request Error: {str(e)} — {url}")
    except Exception as e:
        raise Exception(f"Error scraping URL {url}: {str(e)}")


async def cleanup():
    """Cleanup resources when the server shuts down."""
    await client.aclose()


if __name__ == "__main__":
    try:
        logger.info(f"Starting Web fetch MCP Server on {HOST_ADDR}:{HOST_PORT}...")
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        asyncio.run(cleanup())
        logger.info("Server shutdown complete.")
