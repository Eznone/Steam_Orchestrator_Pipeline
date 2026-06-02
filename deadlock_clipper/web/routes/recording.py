import logging
import threading
import time

from flask import Blueprint, jsonify, request

from deadlock_clipper.config import load_config
from deadlock_clipper.recording.client_launcher import launch_demo, prepare_replay, wait_for_launch
from deadlock_clipper.recording.obs_controller import OBSConnectionError, OBSController
from deadlock_clipper.web import state

bp = Blueprint("recording", __name__)

_CONFIG = load_config()
logger = logging.getLogger(__name__)


def _recording_cfg() -> dict:
    return _CONFIG.get("recording", {})


# ── OBS connection management ────────────────────────────────────────────────


@bp.route("/api/obs/status")
def obs_status():
    rec_cfg = _recording_cfg()
    defaults = {
        "host": rec_cfg.get("obs_host", "localhost"),
        "port": int(rec_cfg.get("obs_port", 4455)),
        "password": rec_cfg.get("obs_password", ""),
        "steam_exe": rec_cfg.get("steam_exe", ""),
        "launch_wait_seconds": float(rec_cfg.get("launch_wait_seconds", 30)),
        "seek_settle_seconds": float(rec_cfg.get("seek_settle_seconds", 2)),
    }
    with state.obs_lock:
        if state.obs_controller is None:
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults})
        try:
            recording = state.obs_controller.is_recording()
            return jsonify({"connected": True, "recording": recording, "config_defaults": defaults})
        except Exception:
            state.obs_controller = None
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults})


@bp.route("/api/obs/connect", methods=["POST"])
def obs_connect():
    body = request.get_json(silent=True) or {}
    host = body.get("host", "localhost")
    port = int(body.get("port", 4455))
    password = body.get("password", "")

    with state.obs_lock:
        if state.obs_controller is not None:
            state.obs_controller.disconnect()
            state.obs_controller = None
        try:
            ctl = OBSController(host=host, port=port, password=password)
            ctl.connect()
            state.obs_controller = ctl
            return jsonify({"status": "ok", "message": f"Connected to OBS at {host}:{port}"})
        except ImportError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except OBSConnectionError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/obs/disconnect", methods=["POST"])
def obs_disconnect():
    with state.obs_lock:
        if state.obs_controller is not None:
            state.obs_controller.disconnect()
            state.obs_controller = None
    return jsonify({"status": "ok"})


@bp.route("/api/record/start", methods=["POST"])
def record_start():
    with state.obs_lock:
        if state.obs_controller is None:
            return jsonify({"status": "error", "message": "Not connected to OBS"}), 400
        try:
            if state.obs_controller.is_recording():
                return jsonify({"status": "error", "message": "Already recording"}), 400
            state.obs_controller.start_recording()
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/record/stop", methods=["POST"])
def record_stop():
    with state.obs_lock:
        if state.obs_controller is None:
            return jsonify({"status": "error", "message": "Not connected to OBS"}), 400
        try:
            output_path = state.obs_controller.stop_recording()
            return jsonify({"status": "ok", "output_path": output_path})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


# ── Background pipeline routes ───────────────────────────────────────────────


@bp.route("/api/record/prepare", methods=["POST"])
def record_prepare():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    start_tick = int(body.get("start_tick", 0))
    rec_cfg = _recording_cfg()
    launch_wait = float(body.get("launch_wait", rec_cfg.get("launch_wait_seconds", 30)))
    seek_settle = float(body.get("seek_settle", rec_cfg.get("seek_settle_seconds", 2)))
    steam_exe = body.get("steam_exe", rec_cfg.get("steam_exe", ""))

    if not dem_path:
        return jsonify({"status": "error", "message": "dem_path is required"}), 400

    job_id, job = state.make_job()

    def _run():
        try:
            state.set_job(job, "preparing", "Launching game...")
            launch_demo(dem_path, steam_exe)
            wait_for_launch(launch_wait)
            state.set_job(job, "preparing", "Seeking to tick...")
            prepare_replay(start_tick, seek_settle)
            state.set_job(job, "done", "Ready at tick")
        except Exception as exc:
            state.set_job(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id})


@bp.route("/api/record/clip", methods=["POST"])
def record_clip():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    clip = body.get("clip", {})
    rec_cfg = _recording_cfg()
    launch_wait = float(body.get("launch_wait", rec_cfg.get("launch_wait_seconds", 30)))
    seek_settle = float(body.get("seek_settle", rec_cfg.get("seek_settle_seconds", 2)))
    steam_exe = body.get("steam_exe", rec_cfg.get("steam_exe", ""))

    if not dem_path or not clip:
        return jsonify({"status": "error", "message": "dem_path and clip are required"}), 400

    clip_id = clip.get("clip_id", "")
    start_tick = int(clip.get("start_tick", 0))
    end_tick = int(clip.get("end_tick", 0))
    tick_rate = state.parse_cache.get(dem_path, {}).get("tick_rate", 64)
    duration_s = max((end_tick - start_tick) / tick_rate, 1)

    job_id, job = state.make_job(clip_id)

    def _run():
        try:
            state.set_job(job, "preparing", "Launching game...")
            launch_demo(dem_path, steam_exe)
            wait_for_launch(launch_wait)
            state.set_job(job, "preparing", "Seeking to tick...")
            prepare_replay(start_tick, seek_settle)
            with state.obs_lock:
                if state.obs_controller is None:
                    raise RuntimeError("OBS not connected")
                state.set_job(job, "recording", "Recording...")
                state.obs_controller.start_recording()
            time.sleep(duration_s)
            with state.obs_lock:
                if state.obs_controller is None:
                    raise RuntimeError("OBS disconnected during recording")
                output_path = state.obs_controller.stop_recording()
            state.set_job(job, "done", "Saved", output_path)
        except Exception as exc:
            state.set_job(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id, "clip_id": clip_id})


@bp.route("/api/record/job/<job_id>")
def record_job(job_id: str):
    with state.jobs_lock:
        job = state.jobs.get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "Job not found"}), 404
    return jsonify(job)
