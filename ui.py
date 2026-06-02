import logging
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from analyzer import analyze
from client_launcher import launch_demo, prepare_replay, wait_for_launch
from obs_controller import OBSConnectionError, OBSController
from parser_wrapper import load_config, parse_demo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

CONFIG = load_config()

# In-memory parse cache: dem_path -> parsed_data dict
_parse_cache: dict[str, dict] = {}

# Persistent OBS controller (None = not connected)
_obs_controller: OBSController | None = None
_obs_lock = threading.Lock()

# Background job tracking: job_id -> {status, message, output_path, clip_id}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _recording_cfg() -> dict:
    return CONFIG.get("recording", {})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/files")
def list_files():
    """Return all .dem files found in test_data/ and the configured hotfolder."""
    found: list[dict] = []

    for directory in [Path("test_data"), Path(CONFIG.get("watcher", {}).get("hotfolder", ""))]:
        if directory.exists():
            for f in sorted(directory.glob("*.dem")):
                found.append({"path": str(f), "name": f.name})

    return jsonify({"files": found})


@app.route("/api/parse", methods=["POST"])
def parse_route():
    """Parse a .dem file and return match metadata + players."""
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()

    if not dem_path:
        return jsonify({"status": "error", "message": "dem_path is required"}), 400

    if dem_path in _parse_cache:
        return jsonify({"status": "ok", "cached": True, "data": _parse_cache[dem_path]})

    try:
        result = parse_demo(dem_path)
        _parse_cache[dem_path] = result
        return jsonify({"status": "ok", "cached": False, "data": result})
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/analyze", methods=["POST"])
def analyze_route():
    """Run the analysis engine with UI-supplied parameters."""
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()

    if dem_path not in _parse_cache:
        return jsonify({"status": "error", "message": "Load the match first."}), 400

    config_override = {
        "analyzer": {
            "event_type": body.get("event_type", "multikill"),
            "target_player_steam_id": body.get("steam_id", ""),
            "multikill_window_seconds": float(body.get("window_seconds", 10)),
            "multikill_threshold": int(body.get("threshold", 2)),
            "kill_streak_threshold": int(body.get("streak_threshold", 3)),
            "objective_types": body.get("objective_types", ["walker", "patron"]),
            "clip_lead_ticks": int(body.get("lead_ticks", 200)),
            "clip_buffer_ticks": int(body.get("buffer_ticks", 300)),
        }
    }

    clips = analyze(_parse_cache[dem_path], config_override)
    return jsonify({"status": "ok", "clips": clips})


# ── OBS connection management ────────────────────────────────────────────────


@app.route("/api/obs/status")
def obs_status():
    global _obs_controller
    rec_cfg = _recording_cfg()
    defaults = {
        "host": rec_cfg.get("obs_host", "localhost"),
        "port": int(rec_cfg.get("obs_port", 4455)),
        "password": rec_cfg.get("obs_password", ""),
        "steam_exe": rec_cfg.get("steam_exe", ""),
        "launch_wait_seconds": float(rec_cfg.get("launch_wait_seconds", 30)),
        "seek_settle_seconds": float(rec_cfg.get("seek_settle_seconds", 2)),
    }
    with _obs_lock:
        if _obs_controller is None:
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults})
        try:
            recording = _obs_controller.is_recording()
            return jsonify({"connected": True, "recording": recording, "config_defaults": defaults})
        except Exception:
            _obs_controller = None
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults})


@app.route("/api/obs/connect", methods=["POST"])
def obs_connect():
    global _obs_controller
    body = request.get_json(silent=True) or {}
    host = body.get("host", "localhost")
    port = int(body.get("port", 4455))
    password = body.get("password", "")

    with _obs_lock:
        if _obs_controller is not None:
            _obs_controller.disconnect()
            _obs_controller = None
        try:
            ctl = OBSController(host=host, port=port, password=password)
            ctl.connect()
            _obs_controller = ctl
            return jsonify({"status": "ok", "message": f"Connected to OBS at {host}:{port}"})
        except ImportError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except OBSConnectionError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/obs/disconnect", methods=["POST"])
def obs_disconnect():
    global _obs_controller
    with _obs_lock:
        if _obs_controller is not None:
            _obs_controller.disconnect()
            _obs_controller = None
    return jsonify({"status": "ok"})


@app.route("/api/record/start", methods=["POST"])
def record_start():
    with _obs_lock:
        if _obs_controller is None:
            return jsonify({"status": "error", "message": "Not connected to OBS"}), 400
        try:
            if _obs_controller.is_recording():
                return jsonify({"status": "error", "message": "Already recording"}), 400
            _obs_controller.start_recording()
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/record/stop", methods=["POST"])
def record_stop():
    with _obs_lock:
        if _obs_controller is None:
            return jsonify({"status": "error", "message": "Not connected to OBS"}), 400
        try:
            output_path = _obs_controller.stop_recording()
            return jsonify({"status": "ok", "output_path": output_path})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


# ── Background pipeline routes ───────────────────────────────────────────────


def _make_job(clip_id: str | None = None) -> tuple[str, dict]:
    job_id = str(uuid.uuid4())
    job = {"status": "preparing", "message": "Starting...", "output_path": None, "clip_id": clip_id}
    with _jobs_lock:
        _jobs[job_id] = job
    return job_id, job


def _set_job(job: dict, status: str, message: str = "", output_path: str | None = None) -> None:
    with _jobs_lock:
        job["status"] = status
        job["message"] = message
        if output_path is not None:
            job["output_path"] = output_path


@app.route("/api/record/prepare", methods=["POST"])
def record_prepare():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    start_tick = int(body.get("start_tick", 0))
    launch_wait = float(body.get("launch_wait", _recording_cfg().get("launch_wait_seconds", 30)))
    seek_settle = float(body.get("seek_settle", _recording_cfg().get("seek_settle_seconds", 2)))
    steam_exe = body.get("steam_exe", _recording_cfg().get("steam_exe", ""))

    if not dem_path:
        return jsonify({"status": "error", "message": "dem_path is required"}), 400

    job_id, job = _make_job()

    def _run():
        try:
            _set_job(job, "preparing", "Launching game...")
            launch_demo(dem_path, steam_exe)
            wait_for_launch(launch_wait)
            _set_job(job, "preparing", "Seeking to tick...")
            prepare_replay(start_tick, seek_settle)
            _set_job(job, "done", "Ready at tick")
        except Exception as exc:
            _set_job(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id})


@app.route("/api/record/clip", methods=["POST"])
def record_clip():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    clip = body.get("clip", {})
    launch_wait = float(body.get("launch_wait", _recording_cfg().get("launch_wait_seconds", 30)))
    seek_settle = float(body.get("seek_settle", _recording_cfg().get("seek_settle_seconds", 2)))
    steam_exe = body.get("steam_exe", _recording_cfg().get("steam_exe", ""))

    if not dem_path or not clip:
        return jsonify({"status": "error", "message": "dem_path and clip are required"}), 400

    clip_id = clip.get("clip_id", "")
    start_tick = int(clip.get("start_tick", 0))
    end_tick = int(clip.get("end_tick", 0))
    tick_rate = _parse_cache.get(dem_path, {}).get("tick_rate", 64)
    duration_s = max((end_tick - start_tick) / tick_rate, 1)

    job_id, job = _make_job(clip_id)

    def _run():
        try:
            _set_job(job, "preparing", "Launching game...")
            launch_demo(dem_path, steam_exe)
            wait_for_launch(launch_wait)
            _set_job(job, "preparing", "Seeking to tick...")
            prepare_replay(start_tick, seek_settle)
            with _obs_lock:
                if _obs_controller is None:
                    raise RuntimeError("OBS not connected")
                _set_job(job, "recording", "Recording...")
                _obs_controller.start_recording()
            time.sleep(duration_s)
            with _obs_lock:
                if _obs_controller is None:
                    raise RuntimeError("OBS disconnected during recording")
                output_path = _obs_controller.stop_recording()
            _set_job(job, "done", "Saved", output_path)
        except Exception as exc:
            _set_job(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id, "clip_id": clip_id})


@app.route("/api/record/job/<job_id>")
def record_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False, threaded=True)
