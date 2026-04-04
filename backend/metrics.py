from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from config import Config

config = Config()


class ResearchMetrics(BaseModel):
    """Data class for tracking research metrics"""

    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    steps_completed: int = 0
    steps_failed: int = 0
    research_id: str = ""
    query: str = ""

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    @property
    def success(self) -> bool:
        return self.steps_failed == 0 and self.steps_completed > 0
