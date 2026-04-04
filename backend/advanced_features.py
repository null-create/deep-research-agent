import os
import json
import asyncio
from typing import List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from research_agent import ResearchAgent, ResearchStep, StepStatus
from model_backend import ModelBackend
from mcp_client import MCPServerRegistry, Message, MCPClient
from observability import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# DEPRECATION NOTICE
# ---------------------------------------------------------------------------
# AdvancedResearchAgent (this file) is the legacy v1 research path.
#
# All new traffic should use the v2 Orchestrator path defined in orchestrator.py.
# The Orchestrator supersedes every feature in this class:
#
#   _execute_step_parallel    → Orchestrator.execute() with parallel_group batches
#   _assess_source_credibility → Orchestrator._distill_tool_result() (Pillar 1)
#   _deep_scrape               → SearchAgent tool loop in Orchestrator._run_search()
#   _cross_reference_sources   → AnalystAgent + LoopAgent in Orchestrator._run_step()
#   _generate_follow_up_questions → knowledge_gaps section in ReportComposer output
#
# This class is retained only for backward-compatibility with
# api_server.py's non-Orchestrator code paths (AGENT_MODE != "research").
# It will be removed in a future release.
# ---------------------------------------------------------------------------


@dataclass
class Source:
    url: str
    title: str
    content: str
    credibility_score: float
    timestamp: datetime


class AdvancedResearchAgent(ResearchAgent):
    """Extended research agent with advanced features"""

    def __init__(self, model_backend: ModelBackend, mcp_registry: MCPServerRegistry):
        super().__init__(model_backend, mcp_registry)
        self.sources: List[Source] = []

    async def _execute_step_parallel(
        self, steps: List[ResearchStep], query: str, memory_context: str
    ) -> List[str]:
        """Execute multiple steps in parallel when possible"""
        tasks = []
        for step in steps:
            task = self._execute_step(step, query)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle results and exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                steps[i].status = StepStatus.FAILED
                steps[i].error = str(result)
                processed_results.append(f"Step failed: {str(result)}")
            else:
                steps[i].status = StepStatus.COMPLETED
                steps[i].result = result
                processed_results.append(result)

        return processed_results

    async def _assess_source_credibility(self, url: str, content: str) -> float:
        """Assess the credibility of a source using the model"""
        system_prompt = """You are a source credibility assessor. Evaluate the credibility of the given source
based on:
1. Domain authority
2. Content quality and depth
3. Presence of citations
4. Author credentials (if available)
5. Recency of information
6. Bias indicators

Respond with a JSON object: {"score": <0.0-1.0>, "reasoning": "<explanation>"}"""

        user_prompt = f"""URL: {url}

Content Preview:
{content[:1000]}...

Assess the credibility of this source."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        # Select a specialized model for credibility assessment if available
        model = self._select_model_for_step("credibility assessment")

        try:
            response = await self.model.generate(model, messages, temperature=0.3)
            result = json.loads(response.content)
            return result.get("score", 0.0)
        except Exception as e:
            logger.exception(f"Error assessing credibility for {url}: {str(e)}")
            return 0.0  # Default credibility score. Don't add credibility score if assessment fails

    async def _deep_scrape(
        self, url: str, max_depth: int = 2, current_depth: int = 0
    ) -> List[Dict[str, Any]]:
        """Recursively scrape a URL and follow relevant links"""
        if current_depth >= max_depth:
            return []

        scraper_client: MCPClient = self.mcp_servers.get("scraper")
        if not scraper_client:
            return []

        # Scrape the current page
        result = await scraper_client.call_tool(
            "scrape", {"url": url, "extract_links": True}
        )

        scraped_data = [
            {"url": url, "content": result.get("content", ""), "depth": current_depth}
        ]

        # Extract and filter relevant links
        links = result.get("links", [])
        if links and current_depth < max_depth - 1:
            # Use model to determine which links are relevant
            relevant_links = await self._filter_relevant_links(url, links)

            # Scrape relevant links in parallel
            tasks = [
                self._deep_scrape(link, max_depth, current_depth + 1)
                for link in relevant_links[:3]  # Limit to top 3 relevant links
            ]

            nested_results = await asyncio.gather(*tasks)
            for nested in nested_results:
                scraped_data.extend(nested)

        return scraped_data

    async def _filter_relevant_links(
        self, source_url: str, links: List[str]
    ) -> List[str]:
        """Use the model to determine which links are worth following"""
        system_prompt = """You are a research link filter. Given a source URL and a list of links found on that page,
identify which links are most relevant to follow for deeper research.

Consider:
1. Link relevance to the original topic
2. Link authority (edu, gov, established publications)
3. Likelihood of containing substantial information

Respond with a JSON array of the most relevant URLs (maximum 3): ["url1", "url2", "url3"]"""

        user_prompt = f"""Source URL: {source_url}

Links found:
{json.dumps(links[:20], indent=2)}

Which links should we follow?"""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        try:
            response = await self.model.generate(messages=messages, temperature=0.3)
            relevant = json.loads(response.content)
            return relevant if isinstance(relevant, list) else []
        except Exception as e:
            logger.exception(f"Error filtering links for {source_url}: {str(e)}")
            return links[:3]  # Fallback to first 3 links

    async def _cross_reference_sources(self, sources: List[Source]) -> Dict[str, Any]:
        """Cross-reference multiple sources to verify information"""
        system_prompt = """You are a fact-checking assistant. Given multiple sources about the same topic,
identify:
1. Facts that are corroborated across multiple sources
2. Conflicting information between sources
3. Unique insights from each source
4. Overall consensus view

Respond with a JSON object containing these categories."""

        sources_text = "\n\n".join(
            [
                f"Source {i + 1} ({source.url}):\n{source.content}"
                for i, source in enumerate(sources)
            ]
        )

        user_prompt = f"""Cross-reference these sources:

{sources_text}

Provide your analysis."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        try:
            response = await self.model.generate(messages, temperature=0.5)
            return json.loads(response.content)
        except Exception:
            return {
                "corroborated_facts": [],
                "conflicts": [],
                "unique_insights": [],
                "consensus": "",
            }

    async def _generate_follow_up_questions(
        self, synthesis: Dict[str, Any]
    ) -> List[str]:
        """Generate follow-up research questions based on findings"""
        system_prompt = """Based on research findings, generate thoughtful follow-up questions that would
deepen understanding or explore knowledge gaps. Focus on:
1. Unexplored implications
2. Potential applications
3. Contradictions that need resolution
4. Related areas worth investigating

Respond with a JSON array of questions."""

        user_prompt = f"""Research Synthesis:
{json.dumps(synthesis, indent=2)}

Generate 5 follow-up research questions."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        try:
            response = await self.model.generate(messages, temperature=0.8)
            questions = json.loads(response.content)
            return questions if isinstance(questions, list) else []
        except Exception as e:
            logger.exception(f"Error generating follow-up questions: {str(e)}")
            return []
