# Deadlock Auto-Clipper: Project Specification & Implementation Plan

## 1. Project Context for AI Assistant
**System Prompt / Instruction:** You are acting as a Senior Software Engineer assisting with the development of a highly automated video clipping pipeline. Read this document thoroughly before proposing code. 
**Goal:** Build a headless or semi-headless system that monitors Deadlock `.dem` replay files, parses them for specific game events, calculates timestamps, and automates the game client and OBS to record and output trimmed video clips.
**Development Environment:** WSL2 for editing/development via VS Code. **Runtime: Windows-native Python** — the pipeline executes on Windows because the game client and OBS are Windows applications that cannot be controlled from WSL2.
**Core Tech Stack:**
- **Python (Windows-native):** Primary orchestration, logic, and file management. Must run on Windows Python (not WSL2 Python) so PyAutoGUI and OBS WebSocket can reach Windows applications.
- **Boon (`boon-deadlock` PyPI):** Source 2 / Deadlock `.dem` parser with native Python bindings. Outputs Polars DataFrames. Replaces the subprocess + external binary approach.
- **obsws-python:** OBS WebSocket v5 client library. PyPI package: `obsws-python`.
- **PyAV:** FFmpeg Python bindings (replaces unmaintained `ffmpeg-python`). Used for final clip trimming.
- **PyAutoGUI:** Keyboard/mouse automation for sending console commands to the Deadlock client. Works correctly on Windows-native Python.

> **Docker:** Dropped from the active stack. See [docker-setup/](docker-setup/) for documentation on how to containerize this pipeline if needed in the future.

---

## 2. System Architecture & Pipeline Flow
The system operates as a five-stage pipeline. When implementing, keep these stages decoupled so they can be tested independently.

### Stage 1: Ingestion (The Watcher)
- **Component:** A Python daemon script using `watchdog`.
- **Function:** Monitors a designated hotfolder (e.g., `~/deadlock_replays`) for new `.dem` files.
- **Trigger:** Upon detecting a completed file transfer, moves the file to a `/processing` directory and triggers Stage 2.
- **Note:** Deadlock stores replays at `%LOCALAPPDATA%\Deadlock\game\citadel\replays\`. The watcher should monitor this path or a symlinked copy.

### Stage 2: Data Extraction (The Parser)
- **Component:** `boon-deadlock` Python library (not a subprocess binary).
- **Function:** Uses Boon's Python API to parse the `.dem` file directly in-process.
- **Output:** Polars DataFrame containing serialized match events (kills, deaths, objective damage, ability usage) mapped to specific server ticks. Convert to a dict/list structure for Stage 3.

### Stage 3: The Analysis Engine (The Filter)
- **Component:** Python data processing module.
- **Function:** Parses the event data to identify "Clip Zones".
- **Logic:** Applies configurable rules (e.g., `find_multikill(time_window=10s, target_player="SteamID")`). 
- **Output:** An array of metadata objects: `[{ "clip_id": "01", "start_tick": 45000, "end_tick": 46500, "reason": "Triple Kill" }]`.

### Stage 4: Automation & Capture (The Director)
- **Component:** Python orchestration script + OBS WebSocket.
- **Function:**
  1. Launches the Deadlock client via Steam URI: `steam://run/1422450//-console +playdemo [file]`.
     - **Caveat:** Launch parameters via Steam URI may be unreliable. Fallback: launch Steam directly via `subprocess` with `-applaunch 1422450 -console +playdemo [file]`, or set launch options in Steam properties and rely on an `autoexec.cfg` in `citadel/cfg/`.
  2. Uses PyAutoGUI to open the developer console (F7) and send commands: `citadel_hide_replay_hud true`, then seek to `start_tick - 200`.
  3. Sends a "Start Recording" payload to the OBS WebSocket (`obsws-python`, default `localhost:4455`).
  4. Waits for `end_tick` + buffer, then sends "Stop Recording".

### Stage 5: Post-Processing (The Trimmer)
- **Component:** PyAV (or `subprocess` calling `ffmpeg` CLI as a fallback).
- **Function:** Takes the raw OBS `.mkv` or `.mp4` file, trims it exactly to the calculated timestamps, applies any requested metadata or compression, and moves it to the `/completed_clips` folder.
- **Note:** `ffmpeg-python` is unmaintained (last updated ~2019). Use `av` (PyAV) or direct subprocess FFmpeg calls instead.

---

## 3. Implementation Phases (Task List for AI)

### Phase 1: Environment Setup & Parser Integration
- [ ] Initialize Python virtual environment and `requirements.txt` (targeting Windows Python).
- [ ] Write `parser_wrapper.py`: A script that takes a hardcoded `.dem` file, uses `boon-deadlock` to parse it, and saves the resulting event data as a JSON file.
- [ ] Verify Boon output schema: identify which DataFrame columns/fields map to kills, deaths, ticks.

### Phase 2: Analysis Engine Logic
- [ ] Write `analyzer.py`: A script that loads event data from Phase 1.
- [ ] Implement a basic heuristic function to find a specific event (e.g., player death or kill streak) and return the surrounding tick numbers.
- [ ] Create a unit test for this heuristic using mock data.

### Phase 3: OBS & Client Automation
- [ ] Write `obs_controller.py`: Establish a connection to a local OBS instance using `obsws-python`. Test start/stop recording.
- [ ] Write `client_launcher.py`: A script to programmatically launch the game and execute the required developer console commands via PyAutoGUI (F7 to open console, type command, Enter).

### Phase 4: Pipeline Orchestration (Putting it together)
- [ ] Write `main.py`: Implement the `watchdog` directory monitor targeting the Deadlock replays path.
- [ ] Chain Phase 1, Phase 2, and Phase 3 together into a single asynchronous workflow.
- [ ] Add basic logging (`logging` module) to track the pipeline's progress through the stages.

---

## 4. Development Rules & Constraints
1. **Modular Code:** Each stage of the pipeline must be an isolated Python module.
2. **Error Handling:** `.dem` parsers can fail on malformed or unsupported files. Ensure robust `try/except` blocks around Boon parse calls.
3. **Tick vs. Time:** Always calculate based on server ticks, not real-world seconds, to avoid desyncs during fast-forwarding.
4. **Configuration:** Keep all thresholds (kill windows, recording buffers, file paths) in an external `config.yaml` file, not hardcoded.
5. **Windows Runtime:** All scripts that interact with the OS (file watching, game launching, OBS control) must run under Windows-native Python, not WSL2 Python.
