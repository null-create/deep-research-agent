import uuid
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from pydantic import BaseModel


# Top level response model for API responses.
# This is what the frontend will expect to receive from the backend for all API calls,
# including websocket messages.
class ResponseMessage(BaseModel):
    type: str
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None

    # Valid ``type`` values (informational — not enforced at runtime so that
    # legacy callers continue to work):
    #
    #   status            — generic progress notification
    #   plan              — research plan ready for approval
    #   approve_plan      — user approved the plan (frontend → backend)
    #   modify_plan       — user requested plan modification
    #   deny_plan         — user denied the plan (frontend → backend)
    #   plan_denied       — plan was denied / acknowledgement (backend → frontend)
    #   step_start        — a research step has begun
    #   step_complete     — a research step finished successfully
    #   step_failed       — a research step failed
    #   synthesis         — synthesis / structured findings data
    #   section_draft     — a single drafted report section (multi-pass synthesis)
    #   report            — final research report document
    #   research_complete — all steps finished, proceeding to synthesis
    #   error             — a non-fatal error notification


@dataclass
class Message:
    role: str
    content: str


@dataclass
class ToolCall:
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class ModelResponse:
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: str = "stop"


@dataclass
class ResearchMetrics:
    research_id: str
    query: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    steps_completed: int
    steps_failed: int
    sources_scraped: int
    tokens_used: int
    success: bool


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchRequest(BaseModel):
    query: str
    max_depth: Optional[int] = 2
    max_sources: Optional[int] = 5


class ResearchResponse(BaseModel):
    research_id: str
    status: str
    message: str


class ConfigUpdate(BaseModel):
    # Provider
    model_backend: Optional[str] = None

    # API credentials — applied to the currently selected (or newly selected) backend
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None

    # Per-backend model names (heavy = root/report/qa, light = search/analyst)
    heavy_model: Optional[str] = None
    light_model: Optional[str] = None

    # Per-agent model overrides (take precedence over heavy/light defaults)
    root_model_override: Optional[str] = None
    search_model_override: Optional[str] = None
    analyst_model_override: Optional[str] = None
    qa_model_override: Optional[str] = None

    # Per-agent sampling parameters
    root_temperature: Optional[float] = None
    search_temperature: Optional[float] = None
    analyst_temperature: Optional[float] = None
    qa_temperature: Optional[float] = None

    root_top_p: Optional[float] = None
    search_top_p: Optional[float] = None
    analyst_top_p: Optional[float] = None
    qa_top_p: Optional[float] = None

    root_max_tokens: Optional[int] = None
    search_max_tokens: Optional[int] = None
    analyst_max_tokens: Optional[int] = None
    qa_max_tokens: Optional[int] = None

    # Legacy field kept for backward compat with existing callers
    model: Optional[str] = None


class SynthesisResult(BaseModel):
    summary: str
    key_insights: List[str]
    patterns: List[str]
    recommendations: List[str]
    creative_applications: List[str]
    knowledge_gaps: List[str]


# NOTE: These are not Pydantic models because they are used internally and not for API request/response validation.
# Also, Pydantic was being a pain when it came time to serialize these responses to JSON in websocket responses,
# so I opted for pure Python classes with custom to_dict methods instead.
class ResearchStep:
    id: int
    name: str
    description: str
    status: StepStatus
    result: Optional[str] = None
    error: Optional[str] = None
    parallel_group: Optional[str] = None  # Steps sharing a group ID run concurrently

    def __init__(
        self,
        id: int,
        name: str,
        description: str,
        status: StepStatus,
        result: Optional[str] = None,
        error: Optional[str] = None,
        parallel_group: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.status = status
        self.result = result if result else ""
        self.error = error if error else ""
        self.parallel_group = parallel_group  # None → sequential

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "parallel_group": self.parallel_group,
        }


class ResearchPlan:
    id: str
    goal: str
    steps: List[ResearchStep]

    def __init__(self, goal: str, steps: List[ResearchStep]):
        self.id = str(uuid.uuid4())
        self.goal = goal
        self.steps = steps

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
        }
