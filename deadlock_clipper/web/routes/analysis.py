import logging
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from deadlock_clipper.config import load_config
from deadlock_clipper.core.analyzer import analyze
from deadlock_clipper.core.parser import parse_demo
from deadlock_clipper.web import state

bp = Blueprint("analysis", __name__)

_CONFIG = load_config()
logger = logging.getLogger(__name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/files")
def list_files():
    """Return all .dem files found in configured replay directories."""
    found: list[dict] = []

    watcher_cfg = _CONFIG.get("watcher", {})
    dirs = [watcher_cfg.get("dev_replay_dir", ""), watcher_cfg.get("hotfolder", "")]
    for directory in [Path(d) for d in dirs if d]:
        if directory.exists():
            for f in sorted(directory.glob("*.dem")):
                found.append({"path": str(f), "name": f.name})

    return jsonify({"files": found})


@bp.route("/api/parse", methods=["POST"])
def parse_route():
    """Parse a .dem file and return match metadata + players."""
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()

    if not dem_path:
        return jsonify({"status": "error", "message": "dem_path is required"}), 400

    if dem_path in state.parse_cache:
        return jsonify({"status": "ok", "cached": True, "data": state.parse_cache[dem_path]})

    try:
        result = parse_demo(dem_path)
        state.parse_cache[dem_path] = result
        return jsonify({"status": "ok", "cached": False, "data": result})
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@bp.route("/api/analyze", methods=["POST"])
def analyze_route():
    """Run the analysis engine with UI-supplied parameters."""
    body = request.get_json(silent=True) or {}
    dem_path = body.get("dem_path", "").strip()

    if dem_path not in state.parse_cache:
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

    clips = analyze(state.parse_cache[dem_path], config_override)
    return jsonify({"status": "ok", "clips": clips})
