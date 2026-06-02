import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from analyzer import analyze
from parser_wrapper import load_config, parse_demo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

CONFIG = load_config()

# In-memory parse cache: dem_path -> parsed_data dict
_parse_cache: dict[str, dict] = {}


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


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=True)
