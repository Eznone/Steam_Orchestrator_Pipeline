from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deadlock_clipper.services.clip_session import GameSessionService
    from deadlock_clipper.ports.capture import CapturePort


class ParseCache:
    """Thread-safe cache mapping dem_path -> parsed match data dict."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = value

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data


class JobStore:
    """Thread-safe store for background recording job state."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, clip_id: str | None = None) -> tuple[str, dict]:
        job_id = str(uuid.uuid4())
        job = {"status": "preparing", "message": "Starting...", "output_path": None, "clip_id": clip_id}
        with self._lock:
            self._jobs[job_id] = job
        return job_id, job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job: dict, status: str, message: str = "", output_path: str | None = None) -> None:
        with self._lock:
            job["status"] = status
            job["message"] = message
            if output_path is not None:
                job["output_path"] = output_path


# Module-level singletons
parse_cache = ParseCache()
jobs = JobStore()

# Capture controller — None when not connected (lock shared with background threads)
capture_controller: "CapturePort | None" = None
capture_lock = threading.Lock()

# Ensures only one recording job (prepare or clip) runs at a time so that
# game commands from a second job cannot fire while the first is recording.
recording_lock = threading.Lock()

# Clip session service — owns game state machine and recording orchestration.
# Initialized by create_app(); replaced when OBS is reconnected.
clip_session: "GameSessionService | None" = None
