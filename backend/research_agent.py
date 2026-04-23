from typing import AsyncIterator

from mcp_client import MCPServerRegistry
from model_backend import (
    Message,
    ModelBackend,
    OpenAIBackend,
    OllamaBackend,
    AzureOpenAIBackend,
    AWSOpenAIBackend,
    GCPVertexAIBackend,
)
from models import ResearchStep
from observability import get_logger

logger = get_logger(__name__)


class ResearchAgent:
    """Main research agent orchestrator"""

    def __init__(self, model_backend: ModelBackend, mcp_registry: MCPServerRegistry):
        self.model = model_backend
        self.mcp_servers = mcp_registry

    async def chat(self, message: str) -> AsyncIterator[str]:
        """Simple chat interface for testing the model backend independently of the research process

        Does not invoke any tools or use any context from previous steps, just a simple back-and-forth with the
        model to test streaming responses.
        """
        system_prompt = """You are a helpful research assistant. Answer the user's message based on your 
            knowledge and capabilities."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=message),
        ]
        async for response_chunk in self.model.stream_generate(messages, []):
            yield response_chunk

    def _select_model_for_step(self, step: ResearchStep | str) -> str:
        """Select the appropriate model for a given research step based on its description and complexity"""
        if isinstance(step, str):
            description = step.lower()
        else:
            description = step.description.lower()

        # Use the most powerful model available for synthesis and insight generation
        if any(
            keyword in description
            for keyword in [
                "plan",
                "synthesize",
                "insight",
                "pattern",
                "analyze",
                "summarize",
                "research",
                "develop",
                "assess",
                "assessment",
                "recommendation",
                "creative application",
                "document",
            ]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5.2"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5.2"
            elif isinstance(self.model, AWSOpenAIBackend):
                return "global.anthropic.claude-sonnet-4-6"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-pro"
            elif isinstance(self.model, OllamaBackend):
                return "nemotron-3-nano"
            else:
                raise ValueError(
                    f"Unsupported model backend for synthesis steps: {type(self.model)}"
                )

        # Use a more efficient model for information gathering and tool execution steps
        elif any(
            keyword in description
            for keyword in [
                "search",
                "find",
                "gather",
                "execute",
                "retrieve",
                "scrape",
                "follow links",
            ]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AWSOpenAIBackend):
                return "global.anthropic.claude-haiku-4-5-20251001-v1:0"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-nano"
            elif isinstance(self.model, OllamaBackend):
                return "llama3.2"
            else:
                raise ValueError(
                    f"Unsupported model backend for synthesis steps: {type(self.model)}"
                )

        # Pick a model for simple status messages or failure messages
        elif any(
            keyword in description
            for keyword in ["status", "message", "failure", "error", "inform the user"]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AWSOpenAIBackend):
                return "global.anthropic.claude-haiku-4-5-20251001-v1:0"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-nano"
            elif isinstance(self.model, OllamaBackend):
                return "llama3.2"
            else:
                raise ValueError(
                    f"Unsupported model backend for status/failure messages: {type(self.model)}"
                )
        # Default to the most powerful model for any steps that don't match specific keywords,
        # as a safe fallback to ensure quality.
        if isinstance(self.model, OpenAIBackend):
            return "gpt-5.2"
        elif isinstance(self.model, AzureOpenAIBackend):
            return "gpt-5.2"
        elif isinstance(self.model, AWSOpenAIBackend):
            return "global.anthropic.claude-sonnet-4-6"
        elif isinstance(self.model, GCPVertexAIBackend):
            return "gemini-2.5-pro"
        elif isinstance(self.model, OllamaBackend):
            return "nemotron-3-nano"
        else:
            raise ValueError(f"Unsupported model backend: {type(self.model)}")
