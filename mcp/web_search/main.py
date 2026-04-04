"""
Web Search MCP Server
A comprehensive web search server using multiple backends without API keys.
"""

import asyncio
import os
import logging
from typing import Any

from mcp.server import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from search_backends import SearchBackends

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HOST_PORT = int(os.getenv("HOST_PORT", "9393"))
HOST_ADDRESS = os.getenv("HOST_ADDRESS", "0.0.0.0")

# Initialize the FastMCP server
mcp = FastMCP(name="Web Search Server", host=HOST_ADDRESS, port=HOST_PORT)

# Initialize search backends
search_engine = SearchBackends()


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for Docker"""
    return JSONResponse({"status": "healthy", "service": "web-search-server"})


@mcp.tool(
    name="web_search",
    description="""
    Search the web using DuckDuckGo and other backends. Supports region and safesearch settings. Use this for general web searches.
    """,
)
async def web_search(
    query: str,
    max_results: int = 10,
    region: str = "wt-wt",
    safesearch: str = "moderate",
) -> dict[str, Any]:
    """
    Search the web using various search engines.

    Args:
        query: Search query string
        backend: Search backend to use (duckduckgo, searx, wikipedia, github)
        max_results: Maximum number of results to return (1-50)
        region: Search region code (e.g., us-en, uk-en, wt-wt for worldwide)
        safesearch: Safe search setting (strict, moderate, off)

    Returns:
        Dictionary containing search results with titles, URLs, and snippets
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_duckduckgo, query, max_results, region, safesearch
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="image_search",
    description="""
    Search for images using DuckDuckGo image search. Supports size, type, and region settings.
    """,
)
async def image_search(
    query: str,
    max_results: int = 10,
    size: str = "Medium",
    type_image: str = "photo",
    region: str = "wt-wt",
) -> dict[str, Any]:
    """
    Search for images using DuckDuckGo image search.

    Args:
        query: Image search query
        max_results: Maximum number of images to return (1-50)
        size: Image size (Small, Medium, Large, Wallpaper)
        type_image: Image type (photo, clipart, gif, transparent, line)
        region: Search region code

    Returns:
        Dictionary containing image URLs, titles, and sources
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_images, query, max_results, size, type_image, region
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="video_search",
    description="""
    Search for videos using DuckDuckGo video search. Supports duration, resolution, and region settings.
    This will return video URLs, titles, durations, and sources. Use this for finding links to videos on the web.
    """,
)
async def video_search(
    query: str,
    max_results: int = 10,
    duration: str = "Medium",
    resolution: str = "High",
    region: str = "wt-wt",
) -> dict[str, Any]:
    """
    Search for videos using DuckDuckGo video search.

    Args:
        query: Video search query
        max_results: Maximum number of videos to return (1-50)
        duration: Video duration (Short, Medium, Long)
        resolution: Video resolution (High, Standard)
        region: Search region code

    Returns:
        Dictionary containing video URLs, titles, durations, and sources
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_videos,
            query,
            max_results,
            duration,
            resolution,
            region,
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="search_wikipedia",
    description="""
    Search Wikipedia articles. Supports region and language settings.
    """,
)
async def search_wikipedia(query: str, max_results: int = 10) -> dict[str, Any]:
    """
    Search Wikipedia for articles matching the query.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (1-50)
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_wikipedia, query, max_results
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="search_github",
    description="""
    Search GitHub repositories matching the query. Returns repository names, descriptions, star counts, and URLs.
    Use this for finding code repositories related to a topic.
    """,
)
async def search_github(query: str, max_results: int = 10) -> dict[str, Any]:
    """
    Search GitHub repositories matching the query.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (1-50)
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_github, query, max_results
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="search_arxiv",
    description="""
    Search arXiv for academic papers and preprints. Returns titles, abstracts, authors, publication dates, PDF links, and categories.
    Use this for scientific, technical, and research queries — especially for CS, physics, math, economics, and quantitative biology.
    Supports sorting by relevance, lastUpdatedDate, or submittedDate.
    """,
)
async def search_arxiv(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
) -> dict[str, Any]:
    """
    Search arXiv for papers matching the query.

    Args:
        query: Search query string (supports arXiv query syntax: ti:, au:, abs:, etc.)
        max_results: Maximum number of results to return (1-50)
        sort_by: Sort order — relevance, lastUpdatedDate, or submittedDate
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_arxiv, query, max_results, sort_by
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


@mcp.tool(
    name="search_semantic_scholar",
    description="""
    Search Semantic Scholar for peer-reviewed academic papers. Returns titles, abstracts, authors, citation counts,
    open-access PDF links, and fields of study. Semantic Scholar indexes 200M+ papers across all disciplines.
    Use this for finding well-cited, peer-reviewed research with richer metadata than arXiv (citation counts, field classification).
    Optionally filter by field of study or year range.
    """,
)
async def search_semantic_scholar(
    query: str,
    max_results: int = 10,
    fields_of_study: list[str] = None,
    year_range: str = None,
) -> dict[str, Any]:
    """
    Search Semantic Scholar for papers matching the query.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (1-50)
        fields_of_study: Optional list of fields to filter by (e.g. ["Computer Science", "Medicine"])
        year_range: Optional year filter, e.g. "2020-2024" or "2023"
    """
    try:
        max_results = max(1, min(50, max_results))
        results = await asyncio.to_thread(
            search_engine.search_semantic_scholar,
            query,
            max_results,
            fields_of_study,
            year_range,
        )

        return {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "query": query}


if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Shutting down the Web Search MCP Server...")
    except Exception as e:
        logger.exception(f"Error running the server: {str(e)}")
