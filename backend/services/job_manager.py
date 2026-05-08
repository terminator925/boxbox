from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class JobState:
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    message: str = "Waiting"


class JobManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, dict[str, Any]] = {}

    def set(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            state = self._states.get(job_id, JobState().__dict__.copy())
            state.update(kwargs)
            self._states[job_id] = state

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(job_id)
            return dict(state) if state else None


job_manager = JobManager()
