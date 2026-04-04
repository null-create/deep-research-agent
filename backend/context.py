"""
General purpose class for managing the ongoing context during research. This is used to process results from each research
step and to manage the state of the conversation. It can be used to store intermediate results, track the progress
of the research, and provide a way to access relevant information throughout the conversation. The Context class
provides methods for adding and retrieving information, as well as for managing the flow of the conversation. It can
be used in conjunction with the Researcher class to create a more structured and organized approach to handling research
tasks

"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from retrieval_utils import tfidf_similarity

logger = logging.getLogger(__name__)


@dataclass
class _StepRecord:
    """A completed step result stored for later semantic retrieval."""

    step_id: int
    description: str
    text: str
    superseded: bool = field(default=False)


class ResearchContext:
    def __init__(self):
        self.data: Dict[str, str] = {}
        self.progress: List[str] = []
        self.intermediate_results: Dict[str, Any] = {}
        self._step_records: List[_StepRecord] = []
        # step_ids that have been superseded by later, more authoritative findings
        self._superseded_step_ids: Set[int] = set()

    # ── Existing key/value store interface (unchanged) ───────────────────────

    def save_step(self, step_name: str, result: str):
        """Save the result of a research step."""
        self.data[step_name] = result
        self.progress.append(step_name)

    def get_previous_steps(self):
        """Get the list of completed steps."""
        return self.progress

    def get_step_result(self, step_name: str):
        """Get the result of a specific step."""
        return self.data.get(step_name, None)

    def clear_context(self):
        """Clear all context data."""
        self.data.clear()
        self.progress.clear()
        self.intermediate_results.clear()
        self._step_records.clear()
        self._superseded_step_ids.clear()

    # ── Semantic retrieval interface ─────────────────────────────────────────

    def add_result(self, step_id: int, description: str, text: str) -> None:
        """
        Store a completed step result for later semantic retrieval.
        Similarity is computed at retrieval time over the full corpus, so no
        per-document embedding is needed here.
        """
        self._step_records.append(
            _StepRecord(step_id=step_id, description=description, text=text)
        )

    def supersede(self, step_id: int, reason: str = "") -> None:
        """
        Mark the result for *step_id* as superseded so it is excluded from
        future ``retrieve_relevant()`` calls.

        This is called by the LoopAgent's contradiction-resolution flow: when
        a contradiction is resolved, the "losing" claim's source step is
        flagged here so stale findings no longer pollute retrieval results.

        Parameters
        ----------
        step_id:
            The step whose result should be flagged as superseded.
        reason:
            Optional human-readable explanation (logged for debugging).
        """
        self._superseded_step_ids.add(step_id)
        for rec in self._step_records:
            if rec.step_id == step_id:
                rec.superseded = True
        if reason:
            logger.debug(
                "[ResearchContext] Step %d superseded: %s", step_id, reason
            )

    def retrieve_relevant(
        self,
        query: str,
        top_k: int = 3,
        max_chars_per_result: int = 2_000,
        include_superseded: bool = False,
    ) -> str:
        """
        Return up to *top_k* prior step results most relevant to *query*,
        concatenated into a single context string.

        Superseded records are excluded by default (pass
        ``include_superseded=True`` to override, e.g. for debugging).

        Relevance is scored with TF-IDF cosine similarity via the shared
        ``retrieval_utils.tfidf_similarity`` function.  Falls back to the
        most recent non-superseded results if scoring fails.

        Each result is capped at *max_chars_per_result* characters so that
        total injected tokens remain bounded even across many steps.
        """
        active_records = [
            rec for rec in self._step_records
            if include_superseded or not rec.superseded
        ]
        if not active_records:
            return ""

        corpus_for_scoring = [
            f"{rec.description} {rec.text}" for rec in active_records
        ]
        try:
            scores = tfidf_similarity(query, corpus_for_scoring)
            scored: List[Tuple[float, _StepRecord]] = list(
                zip(scores, active_records)
            )
            scored.sort(key=lambda x: x[0], reverse=True)
            top_records = [rec for _, rec in scored[:top_k]]
        except Exception as exc:
            logger.warning("TF-IDF retrieval failed (%s); using recency fallback.", exc)
            top_records = active_records[-top_k:]

        parts: List[str] = []
        for rec in top_records:
            body = (
                rec.text
                if len(rec.text) <= max_chars_per_result
                else rec.text[:max_chars_per_result] + "…"
            )
            parts.append(f"Step {rec.step_id} ({rec.description[:80]}):\n{body}")
        return "\n\n".join(parts)
