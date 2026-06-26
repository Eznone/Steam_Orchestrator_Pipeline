import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

from deadlock_clipper.config import load_config
from deadlock_clipper.recording.vidgear_controller import CaptureError, VidGearController, probe_gpu_encoder
from deadlock_clipper.services.clip_session import GameSessionService, RecordOptions
from deadlock_clipper.web import state

bp = Blueprint("recording", __name__)

logger = logging.getLogger(__name__)


def _recording_defaults() -> dict:
    cfg = load_config()
    rec = cfg.get("recording", {})
    watcher = cfg.get("watcher", {})
    return {
        "steam_exe":                 rec.get("steam_exe", ""),
        "launch_wait_seconds":       float(rec.get("launch_wait_seconds", 30)),
        "enter_screen_settle_seconds": float(rec.get("enter_screen_settle_seconds", 5)),
        "seek_settle_seconds":       float(rec.get("seek_settle_seconds", 2)),
        "hud_visible":               bool(rec.get("hud_visible", True)),
        "replays_dir":               watcher.get("hotfolder", ""),
        "fps":                       int(rec.get("fps", 60)),
        "encoder":                   rec.get("encoder", "cpu"),
        "crf":                       int(rec.get("crf", 18)),
        "preset":                    rec.get("preset", "fast"),
        "bitrate":                   rec.get("bitrate", ""),
        "output_width":              int(rec.get("output_width", 0)),
        "output_height":             int(rec.get("output_height", 0)),
    }


def _opts_from_body(body: dict) -> RecordOptions:
    defaults = _recording_defaults()
    return RecordOptions.from_config(
        load_config(),
        overrides={
            "steam_exe":         body.get("steam_exe", defaults["steam_exe"]),
            "launch_wait":       body.get("launch_wait", defaults["launch_wait_seconds"]),
            "enter_screen_settle": body.get("enter_screen_settle", defaults["enter_screen_settle_seconds"]),
            "seek_settle":       body.get("seek_settle", defaults["seek_settle_seconds"]),
            "hud_visible":       body.get("hud_visible", defaults["hud_visible"]),
            "replays_dir":       body.get("replays_dir", defaults["replays_dir"]) or None,
        },
    )


def _require_session() -> "GameSessionService | None":
    """Return the clip session, or None if the capture backend is not connected."""
    session = state.clip_session
    if session is None or session._capture is None:
        return None
    return session


# ── Capture backend connection management ───────────────────────────────────


@bp.route("/api/capture/status")
def capture_status():
    defaults = _recording_defaults()
    gpu_encoder = probe_gpu_encoder()
    with state.capture_lock:
        if state.capture_controller is None:
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults, "gpu_encoder": gpu_encoder})
        try:
            recording = state.capture_controller.is_recording()
            return jsonify({"connected": True, "recording": recording, "config_defaults": defaults, "gpu_encoder": gpu_encoder})
        except Exception:
            state.capture_controller = None
            if state.clip_session:
                state.clip_session._capture = None
            return jsonify({"connected": False, "recording": False, "config_defaults": defaults, "gpu_encoder": gpu_encoder})


@bp.route("/api/capture/connect", methods=["POST"])
def capture_connect():
    body = request.get_json(silent=True) or {}
    encoder = body.get("encoder", "cpu")
    with state.capture_lock:
        if state.capture_controller is not None:
            state.capture_controller.disconnect()
            state.capture_controller = None
        try:
            cfg = load_config()
            cfg = {**cfg, "recording": {**cfg.get("recording", {}), "encoder": encoder}}
            ctl = VidGearController.from_config(cfg)
            ctl.connect()
            state.capture_controller = ctl
            if state.clip_session is None:
                state.clip_session = GameSessionService(
                    capture=ctl, recording_lock=state.recording_lock,
                )
            else:
                state.clip_session._capture = ctl
            return jsonify({"status": "ok", "message": "Capture backend connected."})
        except ImportError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except CaptureError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/capture/disconnect", methods=["POST"])
def capture_disconnect():
    with state.capture_lock:
        if state.capture_controller is not None:
            state.capture_controller.disconnect()
            state.capture_controller = None
        if state.clip_session:
            state.clip_session._capture = None
            state.clip_session.active_dem = None
    return jsonify({"status": "ok"})


@bp.route("/api/record/start", methods=["POST"])
def record_start():
    with state.capture_lock:
        if state.capture_controller is None:
            return jsonify({"status": "error", "message": "Capture backend not connected"}), 400
        try:
            if state.capture_controller.is_recording():
                return jsonify({"status": "error", "message": "Already recording"}), 400
            state.capture_controller.start_recording()
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/record/stop", methods=["POST"])
def record_stop():
    with state.capture_lock:
        if state.capture_controller is None:
            return jsonify({"status": "error", "message": "Capture backend not connected"}), 400
        try:
            output_path = state.capture_controller.stop_recording()
            return jsonify({"status": "ok", "output_path": output_path})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500


# ── Background pipeline routes ───────────────────────────────────────────────


@bp.route("/api/record/prepare", methods=["POST"])
def record_prepare():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    start_tick = int(body.get("start_tick", 0))
    end_tick = int(body.get("end_tick", 0))
    player_name = body.get("player_name", "")

    if not dem_path:
        return jsonify({"status": "error", "message": "dem_path is required"}), 400

    tick_rate = (state.parse_cache.get(dem_path) or {}).get("tick_rate", 64)
    opts = _opts_from_body(body)
    job_id, job = state.jobs.create()

    def _run():
        session = state.clip_session
        if session is None:
            state.jobs.update(job, "error", "Clip session not initialized")
            return
        try:
            session.prepare(
                dem_path, start_tick, end_tick, tick_rate, opts,
                on_status=lambda s, m: state.jobs.update(job, s, m),
                player_name=player_name,
            )
        except Exception as exc:
            state.jobs.update(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id})


@bp.route("/api/record/clip", methods=["POST"])
def record_clip():
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()
    clip = body.get("clip", {})

    if not dem_path or not clip:
        return jsonify({"status": "error", "message": "dem_path and clip are required"}), 400

    clip_id = clip.get("clip_id", "")
    tick_rate = (state.parse_cache.get(dem_path) or {}).get("tick_rate", 64)
    opts = _opts_from_body(body)
    job_id, job = state.jobs.create(clip_id)

    def _run():
        session = state.clip_session
        if session is None or session._capture is None:
            state.jobs.update(job, "error", "Capture backend not connected — click 'Connect' before recording")
            return
        try:
            output_path = session.record_clip(
                clip, dem_path, tick_rate, opts,
                on_status=lambda s, m: state.jobs.update(job, s, m),
            )
            state.jobs.update(job, "done", "Saved", output_path)
        except Exception as exc:
            state.jobs.update(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id, "clip_id": clip_id})


@bp.route("/api/record/teardown", methods=["POST"])
def record_teardown():
    job_id, job = state.jobs.create()

    def _run():
        session = state.clip_session
        if session is None:
            state.jobs.update(job, "error", "Clip session not initialized")
            return
        try:
            session.teardown(on_status=lambda s, m: state.jobs.update(job, s, m))
        except Exception as exc:
            state.jobs.update(job, "error", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id})


@bp.route("/api/record/job/<job_id>")
def record_job(job_id: str):
    job = state.jobs.get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "Job not found"}), 404
    return jsonify(job)
