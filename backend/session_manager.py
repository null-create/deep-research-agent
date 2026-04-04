"""
session_manager.py

Centralized lifecycle management for all research background tasks.

Replaces ad-hoc ``asyncio.create_task()`` calls scattered across the WebSocket
handler with a single registry that:

* Tracks every running research job by ID.
* Provides **coordinated shutdown** — ``shutdown()`` cancels all running tasks
  and ``await``s them, guaranteeing no orphan tasks survive after the FastAPI
  process exits.
* Exposes per-job status so callers can query whether a job is still running,
  has completed, or has failed.

Integration
-----------
1. Created in the FastAPI ``lifespan`` and stored on ``app.state.session_manager``.
2. The WebSocket ``approve_plan`` handler calls ``start_job()`` instead of
   ``asyncio.create_task()``.
3. The ``lifespan`` shutdown block calls ``shutdown()`` before tearing down
   shared resources (MCP registry, agent pool).
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Coroutine, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResearchJob:
    """A tracked research background task."""

    job_id: str
    session_id: str
    task: asyncio.Task
    status: str = "running"


class ResearchSessionManager:
    """Owns every research background task across the FastAPI app lifetime.

    All research execution tasks **must** be created through this manager so
    they can be cancelled cleanly during shutdown.

    Parameters
    ----------
    max_concurrent_sessions:
        Maximum number of research pipelines that may execute simultaneously.
        When the limit is reached, new ``start_job()`` calls block until a
        running job finishes.  Set to 0 to disable the limit.
    """

    def __init__(self, max_concurrent_sessions: int = 0) -> None:
        self._jobs: Dict[str, ResearchJob] = {}
        self._lock = asyncio.Lock()
        self._session_semaphore: asyncio.Semaphore | None = None
        if max_concurrent_sessions > 0:
            self._session_semaphore = asyncio.Semaphore(max_concurrent_sessions)
            logger.info(
                "ResearchSessionManager initialized (max_concurrent_sessions=%d).",
                max_concurrent_sessions,
            )
        else:
            logger.info("ResearchSessionManager initialized (unlimited concurrency).")

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    async def start_job(
        self,
        session_id: str,
        coro: Coroutine,
    ) -> ResearchJob:
        """Create and track a research job from a coroutine.

        If a session semaphore is configured, the coroutine is wrapped so
        that it acquires a slot before executing and releases it on
        completion.  This means more jobs can be *registered* than the
        concurrency limit allows — excess jobs simply wait for a slot.

        Parameters
        ----------
        session_id:
            The ``ResearchSession.session_id`` this job belongs to.
        coro:
            The coroutine to schedule (e.g. the execute + synthesize pipeline).

        Returns
        -------
        ResearchJob
            The tracked job whose ``.task`` can be used for drain / resume.
        """
        job_id = str(uuid.uuid4())

        # Wrap the coroutine with semaphore gating when a limit is configured.
        if self._session_semaphore is not None:
            raw = coro

            async def _gated() -> None:
                async with self._session_semaphore:  # type: ignore[union-attr]
                    logger.info(
                        "Job %s acquired session slot (session=%s).",
                        job_id,
                        session_id,
                    )
                    await raw

            task = asyncio.create_task(_gated(), name=f"research-{job_id}")
        else:
            task = asyncio.create_task(coro, name=f"research-{job_id}")

        job = ResearchJob(
            job_id=job_id,
            session_id=session_id,
            task=task,
        )

        # Synchronous done-callback — only mutates a string field, so no
        # async lock required.
        task.add_done_callback(lambda t: self._on_done(job_id, t))

        async with self._lock:
            self._jobs[job_id] = job

        logger.info("Started job %s for session %s.", job_id, session_id)
        return job

    def _on_done(self, job_id: str, task: asyncio.Task) -> None:
        """Synchronous task-done callback to update job status."""
        job = self._jobs.get(job_id)
        if job is None:
            return
        if task.cancelled():
            job.status = "cancelled"
        elif task.exception():
            job.status = "failed"
            logger.warning("Job %s failed: %s", job_id, task.exception())
        else:
            job.status = "complete"

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> Optional[ResearchJob]:
        """Return a job by ID, or ``None``."""
        return self._jobs.get(job_id)

    def get_job_for_session(self, session_id: str) -> Optional[ResearchJob]:
        """Return the most recent *running* job for a session, or ``None``."""
        for job in reversed(list(self._jobs.values())):
            if job.session_id == session_id and job.status == "running":
                return job
        return None

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a specific job.  Returns ``True`` if cancellation requested."""
        job = self._jobs.get(job_id)
        if job is None or job.task.done():
            return False
        return job.task.cancel()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self, timeout: float = 30.0) -> None:
        """Cancel **all** running tasks and await them.

        Called from the FastAPI lifespan shutdown block.  Guarantees that no
        orphan tasks survive process exit.

        Shutdown follows two passes:

        1. **First pass** — every task is cancelled; we wait up to *timeout*
           seconds for them to finish.
        2. **Second pass** — tasks still running after the first pass are
           cancelled a second time and given 2 s to stop.  Any survivors are
           logged as errors and abandoned.
        """
        async with self._lock:
            running = [j for j in self._jobs.values() if not j.task.done()]

        if not running:
            logger.info("[SessionManager] No running jobs to cancel.")
            return

        tasks = [job.task for job in running]
        logger.info("[SessionManager] Cancelling %d running job(s)…", len(running))
        for task in tasks:
            task.cancel()

        # pending tracks tasks that haven't finished yet; start with all of them
        # so the error-reporting block below still fires even if we hit an
        # early CancelledError.
        pending = set(tasks)

        try:
            # First pass — wait up to `timeout` s.
            _, pending = await asyncio.wait(tasks, timeout=timeout)

            # Second pass — tasks that ignored the first cancellation.
            if pending:
                logger.warning(
                    "[SessionManager] %d task(s) did not stop within %.0f s;"
                    " sending a second cancellation.",
                    len(pending),
                    timeout,
                )
                for task in pending:
                    task.cancel()
                _, pending = await asyncio.wait(pending, timeout=timeout / 2)

        except asyncio.CancelledError:
            # The lifespan cancel scope fired while we were waiting.
            # The tasks already have a CancelledError queued; give them a
            # short grace period to finish their own cleanup handlers.
            pending = {t for t in tasks if not t.done()}
            if pending:
                try:
                    await asyncio.wait(pending, timeout=timeout / 2)
                except asyncio.CancelledError:
                    pass
            pending = {t for t in tasks if not t.done()}

        except Exception as exc:
            logger.error("[SessionManager] Error during shutdown: %s", exc)

            # In case of any other exception, attempt a best-effort cleanup of pending tasks.
            pending = {t for t in tasks if not t.done()}
            if pending:
                try:
                    await asyncio.wait(pending, timeout=timeout / 2)
                except asyncio.CancelledError:
                    pass
            pending = {t for t in tasks if not t.done()}

        if pending:
            logger.error(
                "[SessionManager] %d task(s) could not be stopped cleanly"
                " and will be abandoned.",
                len(pending),
            )

        # Log any tasks that raised exceptions during shutdown.
        # They may have been cancelled, but if they failed to handle cancellation
        # properly we still want to know about it.
        for job in running:
            if job.task.done() and not job.task.cancelled():
                try:
                    exc = job.task.exception()
                except asyncio.CancelledError:
                    pass
                else:
                    if exc is not None:
                        logger.warning(
                            "[SessionManager] Job %s raised during shutdown: %s",
                            job.job_id,
                            exc,
                        )

        if not pending:
            logger.info("[SessionManager] Shutdown complete — all jobs stopped.")

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def prune_done(self) -> int:
        """Remove completed / failed / cancelled jobs.  Returns count removed."""
        async with self._lock:
            done_ids = [jid for jid, j in self._jobs.items() if j.task.done()]
            for jid in done_ids:
                del self._jobs[jid]
        return len(done_ids)
