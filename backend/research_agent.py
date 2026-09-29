from typing import AsyncIterator, Optional

from config import Config
from mcp_client import MCPServerRegistry
from model_backend import (
    Message,
    ModelBackend,
    select_model_for_backend,
)
from models import ResearchStep
from observability import get_logger

logger = get_logger(__name__)


# Keyword sets shared with ``Orchestrator._select_model`` so both agents agree
# on which tasks warrant the heavy vs. light model tier.
_HEAVY_KEYWORDS = (
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
    "report",
)
_LIGHT_KEYWORDS = (
    "search",
    "find",
    "gather",
    "execute",
    "retrieve",
    "scrape",
    "follow links",
    "status",
    "message",
    "failure",
    "error",
    "inform the user",
)


class ResearchAgent:
    """Main research agent orchestrator"""

    def __init__(
        self,
        model_backend: ModelBackend,
        mcp_registry: MCPServerRegistry,
        config: Optional[Config] = None,
    ):
        self.model = model_backend
        self.mcp_servers = mcp_registry
        # Config carries the per-backend ``*_heavy_model`` / ``*_light_model``
        # fields that ``_select_model_for_step`` consults.  Fall back to a
        # freshly-loaded ``Config`` (which reads the environment) when no
        # explicit config is supplied so existing call sites keep working.
        self.config = config or Config()

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
        """Select the appropriate model for a given research step.

        Delegates the actual backend → model-name lookup to
        ``model_backend.select_model_for_backend`` so all agents agree on
        which config field to consult.  Only the heavy/light *decision*
        lives here — driven by keyword matching against the step
        description.
        """
        if isinstance(step, str):
            description = step.lower()
        else:
            description = step.description.lower()

        is_heavy = any(kw in description for kw in _HEAVY_KEYWORDS)
        is_light = (not is_heavy) and any(kw in description for kw in _LIGHT_KEYWORDS)

        # If nothing matched, default to the heavy model — historic behaviour
        # (the previous implementation fell through to the heavy path for
        # unmatched steps).
        resolved_heavy = is_heavy or (not is_light)

        model = select_model_for_backend(
            self.model, self.config, is_heavy=resolved_heavy
        )
        if model is None:
            # Single-model backends (HuggingFace) or unrecognised types —
            # fall back to whatever the backend was configured with.
            fallback = getattr(self.model, "model", None)
            if not fallback:
                raise ValueError(
                    f"Unsupported model backend for model selection: {type(self.model)}"
                )
            return fallback
        return model
