"""
Tests for the web scraper MCP server.
Run from the mcp/web_scrape/ directory: python tests.py
"""

import sys
import os

from bs4 import BeautifulSoup
from main import (
    _is_textual_content,
    _link_density,
    _content_score,
    _remove_boilerplate,
    _extract_main_content,
    _extract_metadata,
)


def test_content_type_gate():
    """Verify _is_textual_content accepts HTML/text and rejects binary types."""
    assert _is_textual_content("text/html; charset=utf-8") is True
    assert _is_textual_content("text/plain") is True
    assert _is_textual_content("application/json") is True
    assert _is_textual_content("application/xhtml+xml") is True
    assert _is_textual_content("application/xml") is True
    assert _is_textual_content("application/rss+xml") is True
    assert _is_textual_content("application/pdf") is False
    assert _is_textual_content("image/png") is False
    assert _is_textual_content("application/octet-stream") is False
    assert _is_textual_content("video/mp4") is False
    assert _is_textual_content("image/jpeg") is False
    print("✅ _is_textual_content gate")


def test_boilerplate_removal():
    """Verify _remove_boilerplate strips nav, footer, cookie banners, etc."""
    html = """
    <html><body>
    <nav>Menu items</nav>
    <header>Site header</header>
    <article><p>Important article content here.</p></article>
    <footer>Footer links</footer>
    <div class="cookie-consent">Accept cookies</div>
    <aside>Sidebar ads</aside>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    _remove_boilerplate(soup)
    text = soup.get_text(strip=True)

    assert "Menu items" not in text, "nav should be removed"
    assert "Site header" not in text, "header should be removed"
    assert "Footer links" not in text, "footer should be removed"
    assert "Accept cookies" not in text, "cookie banner should be removed"
    assert "Sidebar ads" not in text, "aside should be removed"
    assert "Important article content" in text, "article content should survive"
    print("✅ _remove_boilerplate")


def test_boilerplate_pattern_matching():
    """Verify class/id pattern matching catches common boilerplate variants."""
    html = """
    <html><body>
    <div id="newsletter-signup">Subscribe now</div>
    <div class="social-share-buttons">Share this</div>
    <div class="related-posts">You might also like</div>
    <div class="comment-section">Leave a comment</div>
    <div class="advertisement-banner">Buy stuff</div>
    <p>Real content that matters.</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    _remove_boilerplate(soup)
    text = soup.get_text(strip=True)

    assert "Subscribe now" not in text
    assert "Share this" not in text
    assert "You might also like" not in text
    assert "Leave a comment" not in text
    assert "Buy stuff" not in text
    assert "Real content that matters" in text
    print("✅ _remove_boilerplate pattern matching")


def test_main_content_extraction_article():
    """Verify _extract_main_content finds <article> over surrounding noise."""
    html = """
    <html><body>
    <div id="wrapper">
        <div class="sidebar">Lots of sidebar text with navigation links and promotions.</div>
        <article>
            <p>This is the main article about an important research topic that has substantial content
            with multiple sentences and real information that an agent would want to read and process
            for research purposes. It contains enough text to be considered meaningful content.</p>
        </article>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None, "Should find article element"
    assert main.name == "article", f"Expected article, got {main.name}"
    assert "important research topic" in main.get_text()
    print("✅ _extract_main_content (article)")


def test_main_content_extraction_main_tag():
    """Verify _extract_main_content finds <main> when no <article> exists."""
    html = """
    <html><body>
    <div class="sidebar">Short sidebar.</div>
    <main>
        <p>This is the primary content area with substantial text about a research topic
        that has enough characters to pass the 200-char threshold for selection by the
        content extraction heuristic in web_scrape main.py.</p>
    </main>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None, "Should find main element"
    assert main.name == "main", f"Expected main, got {main.name}"
    print("✅ _extract_main_content (main tag)")


def test_main_content_extraction_role_main():
    """Verify _extract_main_content finds role='main' attribute."""
    html = """
    <html><body>
    <div role="main">
        <p>This is the primary content area with substantial text about a research topic
        that has enough characters to pass the 200-char threshold for selection by the
        content extraction heuristic.</p>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None, "Should find role=main element"
    assert main.get("role") == "main"
    print("✅ _extract_main_content (role=main)")


def test_main_content_returns_none_for_sparse_page():
    """When no element has enough text, fall back to None."""
    html = "<html><body><p>Short.</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is None, "Should return None for sparse pages"
    print("✅ _extract_main_content (sparse page → None)")


def test_metadata_extraction():
    """Verify _extract_metadata pulls title, description, author, date."""
    html = """
    <html><head>
    <title>Research Paper Title</title>
    <meta name="description" content="A study on climate change impacts.">
    <meta name="author" content="Dr. Smith">
    <meta property="article:published_time" content="2026-01-15T10:00:00Z">
    </head><body></body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_metadata(soup)
    assert meta["title"] == "Research Paper Title"
    assert meta["description"] == "A study on climate change impacts."
    assert meta["author"] == "Dr. Smith"
    assert meta["published"] == "2026-01-15T10:00:00Z"
    print("✅ _extract_metadata")


def test_metadata_og_description_fallback():
    """Verify og:description is used when name=description is absent."""
    html = """
    <html><head>
    <title>Test</title>
    <meta property="og:description" content="OpenGraph description.">
    </head><body></body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_metadata(soup)
    assert meta["description"] == "OpenGraph description."
    print("✅ _extract_metadata (og:description fallback)")


def test_metadata_empty_page():
    """Verify _extract_metadata returns empty dict for pages with no metadata."""
    html = "<html><head></head><body></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_metadata(soup)
    assert meta == {}
    print("✅ _extract_metadata (empty page)")


def test_link_density_pure_nav():
    """A menu full of links should have link density near 1.0."""
    html = '<div><a href="/a">Home</a> <a href="/b">About</a> <a href="/c">Contact</a></div>'
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("div")
    density = _link_density(el)
    assert density > 0.9, f"Expected high link density, got {density:.2f}"
    print("✅ _link_density (nav menu)")


def test_link_density_article_body():
    """Article prose with one inline link should have low link density."""
    html = (
        "<div>"
        "<p>This is a long paragraph about climate research with lots of text and detail "
        "spanning many words to represent typical article body content in a real page.</p>"
        '<p>See <a href="/ref">this reference</a> for more.</p>'
        "</div>"
    )
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("div")
    density = _link_density(el)
    assert density < 0.2, f"Expected low link density, got {density:.2f}"
    print("✅ _link_density (article body)")


def test_link_density_empty_element():
    """Empty element should return 0.0 without ZeroDivisionError."""
    soup = BeautifulSoup("<div></div>", "html.parser")
    assert _link_density(soup.find("div")) == 0.0
    print("✅ _link_density (empty element)")


def test_content_score_penalises_link_dense():
    """A navigational div and an article div — article must score higher."""
    nav_html = (
        '<div id="nav">'
        + " ".join(f'<a href="/{i}">Item {i}</a>' for i in range(30))
        + "</div>"
    )
    article_html = (
        '<div id="art">'
        + "".join(
            "<p>" + "This is real article content with substantial text. " * 4 + "</p>"
            for _ in range(5)
        )
        + "</div>"
    )
    nav_soup = BeautifulSoup(nav_html, "html.parser")
    art_soup = BeautifulSoup(article_html, "html.parser")
    nav_score = _content_score(nav_soup.find("div"))
    art_score = _content_score(art_soup.find("div"))
    assert (
        art_score > nav_score
    ), f"Article score ({art_score:.0f}) should exceed nav score ({nav_score:.0f})"
    print("✅ _content_score (article beats nav)")


def test_main_content_stage2_positive_class():
    """Stage 2 should find a div whose class name matches _CONTENT_PATTERNS."""
    html = """
    <html><body>
    <div class="sidebar">Short sidebar.</div>
    <div class="post-content">
        <p>This is detailed article content about a research topic that has enough
        characters and substance to qualify as the main content of the page. It spans
        multiple sentences to ensure it exceeds the minimum threshold.</p>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None, "Should find post-content div"
    assert "post-content" in " ".join(main.get("class") or [])
    assert "research topic" in main.get_text()
    print("✅ _extract_main_content (Stage 2 positive class)")


def test_main_content_stage2_positive_id():
    """Stage 2 should find a div whose id matches _CONTENT_PATTERNS."""
    html = """
    <html><body>
    <div id="story-body">
        <p>Long-form journalism content with substantial text about an important event
        that spans many sentences and provides enough detail to be unambiguously the
        primary content of the page under test.</p>
    </div>
    <div id="sidebar">Short sidebar text.</div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None, "Should find story-body div"
    assert main.get("id") == "story-body"
    print("✅ _extract_main_content (Stage 2 positive id)")


def test_main_content_stage2_rejects_high_link_density():
    """Stage 2 must skip a positive-class element if its link density is >= 0.5."""
    # A div classed 'content' but entirely composed of links (e.g. a tag cloud)
    links = " ".join(f'<a href="/{i}">topic {i}</a>' for i in range(20))
    html = f"""
    <html><body>
    <div class="content">{links}</div>
    <div id="real">
        <p>Genuine prose content with low link density and enough text to exceed
        the composite score threshold. This paragraph goes into detail about a
        research topic, containing multiple sentences of real information that
        an agent would extract and process as meaningful article content.</p>
        <p>A second paragraph to further boost the paragraph-count component of
        the content score and ensure this div clearly wins in Stage 3.</p>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    # Stage 2 should skip the link-dense 'content' div and fall through to stage 3
    assert main is not None
    assert main.get("id") == "real", f"Expected 'real' div, got id={main.get('id')!r}"
    print("✅ _extract_main_content (Stage 2 rejects high link density)")


def test_main_content_stage3_link_density_penalty():
    """Stage 3 should prefer paragraph-rich content over a raw-text-heavy nav block."""
    nav_links = " ".join(
        f'<a href="/{i}">Nav link number {i} text</a>' for i in range(40)
    )
    html = (
        "<html><body>"
        f'<div id="nav">{nav_links}</div>'
        '<div id="article">'
        + "".join(
            "<p>" + "Article prose sentence with real content. " * 6 + "</p>"
            for _ in range(6)
        )
        + "</div></body></html>"
    )
    soup = BeautifulSoup(html, "html.parser")
    main = _extract_main_content(soup)
    assert main is not None
    assert (
        main.get("id") == "article"
    ), f"Expected article div, got id={main.get('id')!r}"
    print("✅ _extract_main_content (Stage 3 link density penalty)")


if __name__ == "__main__":
    errors = []

    tests = [
        test_content_type_gate,
        test_boilerplate_removal,
        test_boilerplate_pattern_matching,
        test_main_content_extraction_article,
        test_main_content_extraction_main_tag,
        test_main_content_extraction_role_main,
        test_main_content_returns_none_for_sparse_page,
        test_metadata_extraction,
        test_metadata_og_description_fallback,
        test_metadata_empty_page,
        test_link_density_pure_nav,
        test_link_density_article_body,
        test_link_density_empty_element,
        test_content_score_penalises_link_dense,
        test_main_content_stage2_positive_class,
        test_main_content_stage2_positive_id,
        test_main_content_stage2_rejects_high_link_density,
        test_main_content_stage3_link_density_penalty,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            errors.append(t.__name__)

    print()
    if errors:
        print(f"❌ {len(errors)} test(s) failed: {errors}")
        sys.exit(1)
    else:
        print("🎉 All web_scrape tests passed.")
        sys.exit(0)
