import threading
import uuid


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

# OBS controller — None when not connected (lock shared with background threads)
obs_controller: "OBSController | None" = None
obs_lock = threading.Lock()

# Path of the .dem currently loaded in the running game client.
# None means the game is not running or the demo is unknown.
# Set after a successful launch_and_prepare; cleared on error or disconnect.
active_dem: str | None = None

# Ensures only one recording job (prepare or clip) runs at a time so that
# game commands from a second job cannot fire while the first is recording.
recording_lock = threading.Lock()
