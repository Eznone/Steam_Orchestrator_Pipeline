# Deadlock Auto-Clipper: Project Specification

## 1. Context for AI Assistants

You are a Senior Software Engineer working on a headless pipeline that monitors Deadlock `.dem` replay files, parses them for highlight events, and automates OBS and the game client to record trimmed video clips.

**Before writing any code, read this document in full.** The codebase has a specific structure and set of conventions that must be respected.

**Runtime constraint:** The game client (Deadlock) and OBS are Windows applications. All pipeline stages that interact with them — file watching, game launching, console automation, OBS control — must run under **Windows-native Python**, not WSL2. The development environment is WSL2/VS Code; the execution environment is Windows Python.

---

## 2. Tech Stack

| Library | Purpose |
|---------|---------|
| `boon-deadlock` | Source 2 / Deadlock `.dem` parser. Outputs Polars DataFrames. |
| `obsws-python` | OBS WebSocket v5 client (`localhost:4455`). |
| `pyautogui` | Keyboard/mouse automation for Deadlock console commands. Windows-only. |
| `flask` | Development web UI for manually driving the pipeline during testing. |
| `watchdog` | Directory monitor for Stage 1 (Phase 4, not yet implemented). |
| `pyav` | FFmpeg bindings for clip trimming (Stage 5, not yet implemented). |
| `pyyaml` + `python-dotenv` | Configuration loading. |
| `polars` | DataFrame library used by Boon output. |

> **Docker:** Dropped from the active stack. See `docker-setup/` for documentation if containerisation is needed in future.

---

## 3. Repository Layout

```
deadlock_clipper/           ← single installable package (all application code lives here)
├── config.py               ← load_config(): loads config.yaml, overlays .env vars, caches result
├── cli.py                  ← CLI entry point: `deadlock-clipper parse|analyze|obs-test|launch-test`
├── core/                   ← pure data-processing tools, no I/O side effects
│   ├── models.py           ← Kill, Player, ObjectiveEvent dataclasses (schema contract)
│   ├── parser.py           ← parse_demo(): wraps Boon, returns structured dict
│   └── analyzer.py         ← analyze(): detects clip zones from parsed match data
├── recording/              ← Windows-only automation tools
│   ├── obs_controller.py   ← OBSController: connect/disconnect/start/stop recording
│   ├── client_launcher.py  ← launch_demo(), prepare_replay(), send_console_command()
│   └── pipeline.py         ← launch_and_prepare(): shared launch→wait→seek sequence
└── web/                    ← Flask development UI (self-contained)
    ├── __init__.py         ← create_app() factory, registers blueprints
    ├── state.py            ← ParseCache, JobStore, obs_controller singleton
    ├── routes/
    │   ├── analysis.py     ← Blueprint: GET /, /api/files, /api/parse, /api/analyze
    │   └── recording.py    ← Blueprint: /api/obs/*, /api/record/*
    └── templates/
        └── index.html      ← Single-page UI (vanilla JS, dark theme)

data/                       ← pipeline I/O (gitignored outputs)
├── parsed/                 ← JSON output from parser
├── processing/             ← files in-flight through the pipeline
└── clips/                  ← final recorded video output

tests/
├── fixtures/               ← .dem replay files for development and testing
├── test_parser.py
├── test_analyzer.py
├── test_obs_controller.py
└── test_client_launcher.py

serve.py                    ← starts the Flask dev UI: `python serve.py`
main.py                     ← Phase 4 stub: watchdog orchestration (not yet implemented)
config.yaml                 ← all runtime configuration (see Section 5)
```

**Key rule:** `deadlock_clipper.core` must never import from `deadlock_clipper.recording` or `deadlock_clipper.web`. Dependency direction is always: `web` → `core`/`recording` → `config`.

---

## 4. Five-Stage Pipeline

### Stage 1 — Ingestion (The Watcher) · *Phase 4, not implemented*
- Watchdog daemon monitors `config.watcher.hotfolder` for new `.dem` files.
- On file completion, moves to `data/processing/` and triggers Stage 2.
- Deadlock stores replays at `C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\citadel\replays\`.

### Stage 2 — Data Extraction (The Parser) · *Complete*
- **Module:** `deadlock_clipper/core/parser.py`
- **Entry point:** `parse_demo(dem_path, output_path=None) -> dict`
- Uses `boon-deadlock` to parse `.dem` in-process. Converts Polars DataFrames to plain dicts via `Kill`, `Player`, `ObjectiveEvent` dataclasses (`core/models.py`).
- Output schema: `{match_id, map_name, total_ticks, tick_rate, total_clock_time, winning_team_num, players[], kills[], objectives_destroyed[]}`.

### Stage 3 — Analysis Engine (The Filter) · *Complete*
- **Module:** `deadlock_clipper/core/analyzer.py`
- **Entry point:** `analyze(parsed_data: dict, config: dict) -> list[dict]`
- Detects four event types: `multikill`, `single_kill`, `kill_streak`, `objective`.
- Each clip zone: `{clip_id, start_tick, end_tick, reason, event_type, detail, kill_count, kill_ticks}`.
- All thresholds and labels come from `config.yaml` — nothing game-specific is hardcoded.

### Stage 4 — Automation & Capture (The Director) · *Modules complete, orchestration pending*
- **Modules:** `deadlock_clipper/recording/obs_controller.py`, `client_launcher.py`, `pipeline.py`
- **OBSController** (`obs_controller.py`): wraps `obsws-python`. Connect once, reuse. Context manager supported.
- **`launch_demo(dem_path, steam_exe)`**: launches Deadlock via `steam.exe -applaunch 1422450 -console +playdemo <stem>`. The `.dem` file must exist in Deadlock's replays directory; only the filename stem is passed to `+playdemo`.
- **`prepare_replay(start_tick, seek_settle_seconds)`**: hides HUD via console (`citadel_hide_replay_hud true`), seeks to tick (`demo_goto <tick>`).
- **`launch_and_prepare(..., on_status)`** (`pipeline.py`): shared launch→wait→seek sequence used by the web UI's background jobs. `on_status(status, message)` callback updates job state.
- **Orchestration** (`main.py`): chains Stages 1–4 into a watchdog-driven automated loop. **Not yet implemented.**

### Stage 5 — Post-Processing (The Trimmer) · *Not implemented*
- **Planned module:** `deadlock_clipper/core/trimmer.py`
- Takes raw OBS `.mkv`/`.mp4`, trims to exact tick-derived timestamps using PyAV (`av` package).
- Output to `data/clips/`.
- Do not use `ffmpeg-python` (unmaintained since 2019). Use `av` (PyAV) or `subprocess` FFmpeg calls.

---

## 5. Configuration (`config.yaml`)

All runtime values live in `config.yaml`. Load with `deadlock_clipper.config.load_config()` — result is cached after first call; pass `force_reload=True` to re-read from disk.

Sensitive values are overlaid from `.env` (or environment):
- `DEADLOCK_STEAM_ID` → `analyzer.target_player_steam_id`
- `OBS_PASSWORD` → `recording.obs_password`

**Schema:**
```yaml
watcher:
  hotfolder: 'C:\...\replays'        # Windows path to Deadlock replays
  processing_dir: "./data/processing"
  dev_replay_dir: "./tests/fixtures"  # .dem files shown in the web UI file picker

parser:
  output_dir: "./data/parsed"

analyzer:
  target_player_steam_id: ""          # Steam ID64, or blank for all players
  multikill_window_seconds: 10
  clip_lead_ticks: 200
  clip_buffer_ticks: 300
  multikill_threshold: 2
  kill_streak_threshold: 3
  objective_types: [walker, barracks, patron]

recording:
  obs_host: "localhost"
  obs_port: 4455
  obs_password: ""
  scene_name: ""                      # OBS scene to switch to before recording
  steam_exe: 'C:\...\steam.exe'
  launch_wait_seconds: 30             # wait after launch before console commands
  seek_settle_seconds: 2              # wait after demo_goto before recording

clips:
  output_dir: "./data/clips"

game_constants:                       # display labels; edit here, not in code
  kill_labels: {2: "Double Kill", ...}
  team_names: {2: "Amber Hand", 3: "Sapphire Flame"}
  lane_names: {1: "Yellow", 4: "Blue", 6: "Purple"}
  objective_labels: {walker: "Walker Destroyed", ...}
```

---

## 6. Flask Development UI

The web UI exists to manually drive the pipeline during development. It is **not** the Phase 4 automated orchestrator.

**Start:** `python serve.py` → `http://localhost:5000`

**Analysis page** — load a `.dem`, run clip detection, review found zones.
**Recording page** — connect to OBS, launch game, seek to clip ticks, trigger recording.

**API surface:**
```
GET  /api/files                  list .dem files from dev_replay_dir + hotfolder
POST /api/parse                  {dem_path} → parsed match data (cached in ParseCache)
POST /api/analyze                {dem_path, event_type, ...} → clip zones list
GET  /api/obs/status             {connected, recording, config_defaults}
POST /api/obs/connect            {host, port, password}
POST /api/obs/disconnect
POST /api/record/start           manual OBS start
POST /api/record/stop            manual OBS stop → {output_path}
POST /api/record/prepare         background job: launch + seek → {job_id}
POST /api/record/clip            background job: full pipeline for one clip → {job_id}
GET  /api/record/job/<job_id>    poll background job → {status, message, output_path}
```

Long-running routes (`/api/record/prepare`, `/api/record/clip`) spawn background threads immediately and return a `job_id`. Poll `/api/record/job/<id>` every 2 seconds for status. Job statuses: `preparing → recording → done | error`.

Shared state lives in `deadlock_clipper/web/state.py`:
- `parse_cache` (`ParseCache`) — thread-safe dem_path → parsed data cache
- `jobs` (`JobStore`) — thread-safe job_id → job state store
- `obs_controller` + `obs_lock` — persistent OBS connection between requests

---

## 7. CLI

```
deadlock-clipper parse <dem>          parse a .dem file, write JSON to data/parsed/
deadlock-clipper analyze <json>       detect clip zones from a parsed JSON file
deadlock-clipper obs-test [--record]  test OBS WebSocket connection; optionally do 3s test recording
deadlock-clipper launch-test <dem>    launch demo and verify HUD hide works (Windows only)
```

**Module:** `deadlock_clipper/cli.py` · **Entry point in pyproject.toml:** `deadlock-clipper = "deadlock_clipper.cli:main"`

---

## 8. Development Rules

1. **Package boundary:** All application code lives in `deadlock_clipper/`. Root-level files are entry points only (`serve.py`, `main.py`).

2. **Dependency direction:** `web` and `cli` import from `core`/`recording`. `core` never imports from `recording` or `web`. `config` is the only cross-cutting import.

3. **Configuration:** No thresholds, labels, paths, or game-specific strings in code. All go in `config.yaml` under the relevant section.

4. **Tick-based timing:** All clip calculations use server ticks, not real-world seconds, to avoid desyncs during replay fast-forward. Convert to seconds only for display or sleep durations.

5. **Windows-only modules:** `client_launcher.py` and any future OS-automation code must guard with `_GUI_AVAILABLE` / `_require_gui()` so imports don't fail on Linux/WSL. The guard is already in place — preserve it.

6. **Thread safety:** Flask runs with `threaded=True`. Any shared mutable state must use locks. `ParseCache` and `JobStore` in `state.py` are already locked — do not add new bare module-level mutable globals.

7. **Error handling:** Boon parse calls can fail on corrupt files — always wrap in `try/except (InvalidDemoError, DemoHeaderError)` and re-raise as `RuntimeError`. OBS calls can fail if disconnected mid-session — `_require_connected()` guard is on all `OBSController` methods.

8. **Tests:** Run with `uv run pytest`. All 56 tests must pass before committing. Test fixtures (`.dem` files) live in `tests/fixtures/`.

9. **No hardcoded paths in app code:** The `tests/fixtures` path is in `config.yaml` under `watcher.dev_replay_dir` — read it from there, never hardcode it.

---

## 9. Implementation Status

| Phase | Stage | Status |
|-------|-------|--------|
| 1 | Parser integration | ✅ Complete |
| 2 | Analysis engine | ✅ Complete |
| 3 | OBS + client automation modules | ✅ Complete |
| 3 | Flask development UI | ✅ Complete |
| 4 | Watchdog orchestration (`main.py`) | 🔲 Not started |
| 5 | Clip trimmer (PyAV) | 🔲 Not started |

**Next implementation target — Phase 4 (`main.py`):**
- Watchdog daemon watching `config.watcher.hotfolder`
- On new `.dem` detected: parse → analyze → for each clip zone: launch + prepare + record
- Logging via `logging` module throughout
- Import from: `deadlock_clipper.config`, `deadlock_clipper.core.parser`, `deadlock_clipper.core.analyzer`, `deadlock_clipper.recording.pipeline`, `deadlock_clipper.recording.obs_controller`
