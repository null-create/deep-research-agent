"""
Search backend implementations for the web search MCP server.
Uses various libraries that don't require API keys.
"""

import os
import random
from typing import Any, Dict, List

import requests

import arxiv
from ddgs import DDGS
import wikipedia


# User-Agent pool — rotated per request to reduce bot-detection blocks
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class SearchBackends:
    """Handles various search backends without requiring API keys."""

    def __init__(self):
        self.session = requests.Session()
        self._github_token = os.getenv("GITHUB_TOKEN")
        self._s2_api_key = os.getenv("S2_API_KEY")

    def _pick_ua(self) -> str:
        return random.choice(_USER_AGENTS)

    def search_duckduckgo(
        self,
        query: str,
        max_results: int = 10,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> List[Dict[str, Any]]:
        """Search using DuckDuckGo."""
        try:
            with DDGS() as ddgs:
                results = []
                search_results = ddgs.text(
                    query=query,
                    region=region,
                    safesearch=safesearch,
                    max_results=max_results,
                )

                for result in search_results:
                    results.append(
                        {
                            "title": result.get("title", ""),
                            "url": result.get("href", ""),
                            "snippet": result.get("body", ""),
                            "source": "DuckDuckGo",
                        }
                    )

                return results[:max_results]

        except Exception as e:
            raise Exception(f"DuckDuckGo search failed: {str(e)}")

    def search_images(
        self,
        query: str,
        max_results: int = 10,
        size: str = "Medium",
        type_image: str = "photo",
        region: str = "wt-wt",
    ) -> List[Dict[str, Any]]:
        """Search images using DuckDuckGo."""
        try:
            with DDGS() as ddgs:
                results = []
                image_results = ddgs.images(
                    query=query,
                    region=region,
                    size=size,
                    type_image=type_image,
                    max_results=max_results,
                )

                for result in image_results:
                    results.append(
                        {
                            "title": result.get("title", ""),
                            "url": result.get("image", ""),
                            "thumbnail": result.get("thumbnail", ""),
                            "source": result.get("source", "DuckDuckGo Images"),
                            "width": result.get("width", ""),
                            "height": result.get("height", ""),
                        }
                    )

                return results[:max_results]

        except Exception as e:
            raise Exception(f"DuckDuckGo image search failed: {str(e)}")

    def search_videos(
        self,
        query: str,
        max_results: int = 10,
        duration: str = "Medium",
        resolution: str = "High",
        region: str = "wt-wt",
    ) -> List[Dict[str, Any]]:
        """Search videos using DuckDuckGo."""
        try:
            with DDGS() as ddgs:
                results = []
                video_results = ddgs.videos(
                    query=query,
                    region=region,
                    duration=duration,
                    resolution=resolution,
                    max_results=max_results,
                )

                for result in video_results:
                    results.append(
                        {
                            "title": result.get("title", ""),
                            "url": result.get("content", ""),
                            "thumbnail": result.get("image", ""),
                            "duration": result.get("duration", ""),
                            "source": result.get("publisher", "DuckDuckGo Videos"),
                            "published": result.get("published", ""),
                        }
                    )

                return results[:max_results]

        except Exception as e:
            raise Exception(f"DuckDuckGo video search failed: {str(e)}")

    def search_wikipedia(
        self, query: str, max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search Wikipedia."""
        try:
            results = []
            search_results = wikipedia.search(query, results=max_results)

            for title in search_results[:max_results]:
                try:
                    page = wikipedia.page(title)
                    results.append(
                        {
                            "title": page.title,
                            "url": page.url,
                            "snippet": (
                                page.summary[:300] + "..."
                                if len(page.summary) > 300
                                else page.summary
                            ),
                            "source": "Wikipedia",
                        }
                    )
                except wikipedia.exceptions.DisambiguationError as e:
                    # Try the first disambiguation option
                    try:
                        page = wikipedia.page(e.options[0])
                        results.append(
                            {
                                "title": page.title,
                                "url": page.url,
                                "snippet": (
                                    page.summary[:300] + "..."
                                    if len(page.summary) > 300
                                    else page.summary
                                ),
                                "source": "Wikipedia",
                            }
                        )
                    except:
                        continue
                except wikipedia.exceptions.PageError:
                    continue
                except Exception:
                    continue

            return results

        except Exception as e:
            raise Exception(f"Wikipedia search failed: {str(e)}")

    def search_github(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search GitHub repositories, using a token if GITHUB_TOKEN is set."""
        try:
            url = "https://api.github.com/search/repositories"
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": min(max_results, 100),
            }
            headers = {"User-Agent": self._pick_ua()}
            if self._github_token:
                headers["Authorization"] = f"Bearer {self._github_token}"
            response = self.session.get(url, params=params, headers=headers)
            response.raise_for_status()

            data = response.json()
            results = []

            for repo in data.get("items", [])[:max_results]:
                results.append(
                    {
                        "title": repo.get("full_name", ""),
                        "url": repo.get("html_url", ""),
                        "snippet": repo.get("description", "")
                        or "No description available",
                        "source": "GitHub",
                        "stars": repo.get("stargazers_count", 0),
                        "language": repo.get("language", ""),
                        "updated": repo.get("updated_at", ""),
                    }
                )

            return results

        except Exception as e:
            raise Exception(f"GitHub search failed: {str(e)}")

    def search_arxiv(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
    ) -> List[Dict[str, Any]]:
        """Search arXiv for papers."""
        try:
            sort_criterion = {
                "relevance": arxiv.SortCriterion.Relevance,
                "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
                "submittedDate": arxiv.SortCriterion.SubmittedDate,
            }.get(sort_by, arxiv.SortCriterion.Relevance)

            client = arxiv.Client()
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=sort_criterion,
            )

            results = []
            for paper in client.results(search):
                results.append(
                    {
                        "title": paper.title,
                        "url": paper.entry_id,
                        "pdf_url": paper.pdf_url,
                        "snippet": (
                            paper.summary[:500] + "..."
                            if len(paper.summary) > 500
                            else paper.summary
                        ),
                        "authors": [a.name for a in paper.authors[:5]],
                        "published": (
                            paper.published.strftime("%Y-%m-%d")
                            if paper.published
                            else ""
                        ),
                        "updated": (
                            paper.updated.strftime("%Y-%m-%d") if paper.updated else ""
                        ),
                        "categories": paper.categories,
                        "source": "arXiv",
                    }
                )

            return results

        except Exception as e:
            raise Exception(f"arXiv search failed: {str(e)}")

    def search_semantic_scholar(
        self,
        query: str,
        max_results: int = 10,
        fields_of_study: List[str] = None,
        year_range: str = None,
    ) -> List[Dict[str, Any]]:
        """Search Semantic Scholar for academic papers via REST API."""
        try:
            params: Dict[str, Any] = {
                "query": query,
                "limit": min(max_results, 100),
                "fields": "title,abstract,authors,year,citationCount,openAccessPdf,externalIds,url,fieldsOfStudy",
            }
            if fields_of_study:
                params["fieldsOfStudy"] = ",".join(fields_of_study)
            if year_range:
                params["year"] = year_range

            headers = {"User-Agent": self._pick_ua()}
            if self._s2_api_key:
                headers["x-api-key"] = self._s2_api_key

            response = self.session.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for paper in data.get("data", [])[:max_results]:
                pdf_url = ""
                oa = paper.get("openAccessPdf") or {}
                if oa:
                    pdf_url = oa.get("url", "")

                external_ids = paper.get("externalIds") or {}
                arxiv_id = external_ids.get("ArXiv", "")
                paper_url = paper.get("url") or (
                    f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
                )

                abstract = paper.get("abstract") or ""
                snippet = abstract[:500] + "..." if len(abstract) > 500 else abstract

                results.append(
                    {
                        "title": paper.get("title") or "",
                        "url": paper_url,
                        "pdf_url": pdf_url,
                        "snippet": snippet,
                        "authors": [
                            a.get("name", "") for a in (paper.get("authors") or [])[:5]
                        ],
                        "year": paper.get("year"),
                        "citation_count": paper.get("citationCount"),
                        "fields_of_study": paper.get("fieldsOfStudy") or [],
                        "source": "Semantic Scholar",
                    }
                )

            return results

        except Exception as e:
            raise Exception(f"Semantic Scholar search failed: {str(e)}")
