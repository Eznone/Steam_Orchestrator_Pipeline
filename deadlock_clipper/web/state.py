import threading
import uuid

# Parse cache: dem_path -> parsed match data dict
parse_cache: dict[str, dict] = {}

# Persistent OBS controller — None when not connected
obs_controller = None  # OBSController | None
obs_lock = threading.Lock()

# Background jobs: job_id -> {status, message, output_path, clip_id}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def make_job(clip_id: str | None = None) -> tuple[str, dict]:
    job_id = str(uuid.uuid4())
    job = {"status": "preparing", "message": "Starting...", "output_path": None, "clip_id": clip_id}
    with jobs_lock:
        jobs[job_id] = job
    return job_id, job


def set_job(job: dict, status: str, message: str = "", output_path: str | None = None) -> None:
    with jobs_lock:
        job["status"] = status
        job["message"] = message
        if output_path is not None:
            job["output_path"] = output_path
