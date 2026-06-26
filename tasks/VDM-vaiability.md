# Deadlock Clip Automation — Architecture & VDM Migration Guide

> This document is intended for Claude Code to understand the context, constraints, current implementation, and target architecture for a Deadlock automated highlight clip tool.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Asset & Data Sources](#2-asset--data-sources)
3. [Legal & Viability Assessment](#3-legal--viability-assessment)
4. [Current Implementation (Console Automation)](#4-current-implementation-console-automation)
5. [Target Implementation (VDM-Based)](#5-target-implementation-vdm-based)
6. [Migration Plan](#6-migration-plan)
7. [Full Pipeline Architecture](#7-full-pipeline-architecture)
8. [Key Technical References](#8-key-technical-references)
9. [Known Risks & Open Questions](#9-known-risks--open-questions)

---

## 1. Project Overview

This project is a **paid SaaS tool** that automatically generates highlight clips from Deadlock game replays. The product:

- Pulls match data (kill events, timestamps) from the community Deadlock API
- Identifies highlight-worthy moments (kills, streaks, clutch plays)
- Launches the game, loads the replay, seeks to each event, and records a clip
- Stitches clips into a highlight reel using FFmpeg
- Is delivered to users as downloadable desktop software with a paid subscription tier

The tool sits alongside a **Deadlock stats website** that displays hero/item analytics and is monetized via ads.

---

## 2. Asset & Data Sources

### 2.1 Community Assets API (Not Official Valve)

- **Base URL:** `https://assets.deadlock-api.com`
- **Source:** Community-run, open source. Extracts assets from Valve's VPK game files.
- **Not endorsed by Valve.** Tolerated in practice, same model as Dota 2 / CS2 fan sites.
- **Available static paths:**
  - `https://assets.deadlock-api.com/images/` — hero images, item icons, rank badges
  - `https://assets.deadlock-api.com/icons/` — SVG icons
  - `https://assets.deadlock-api.com/sounds/` — sound files
  - `https://assets.deadlock-api.com/videos/` — ability/hero videos

### 2.2 Community Match Data API

- **Base URL:** `https://api.deadlock-api.com`
- **Key endpoints:**
  - `GET /v2/heroes` — all heroes with image URLs and metadata
  - `GET /v2/items` — all items with image URLs
  - `GET /v2/ranks` — rank badge data
- **Match data:** Available via the same API ecosystem; pulls from Valve's public Steam endpoints

### 2.3 Official Valve / Steam Web API

- **Base URL:** `https://api.steampowered.com`
- **Requires:** Free API key from `https://steamcommunity.com/dev`
- **Provides:** Match history, player stats, leaderboard data
- **Does NOT provide:** Hero images, item icons, or any game asset files
- **TOS restriction:** Cannot charge for access to Steam data directly; displaying it with ads is acceptable

### 2.4 Replay Files (.dem)

- Stored locally in the user's Steam installation under Deadlock's app data directory
- Binary format based on **protobuf** (same as CS2 and Dota 2 — Source 2 engine)
- Contain: tick-by-tick game state, player positions, kill events, item purchases, ability usage
- Do NOT contain video frames — they are pure game state data
- Community libraries exist for parsing these files (see Section 8)

---

## 3. Legal & Viability Assessment

### 3.1 Stats Website with Ads

- **Risk: LOW**
- Well-established precedent in the Valve ecosystem (Dotabuff, tracker.gg, STRATZ)
- Valve has historically tolerated and implicitly encouraged community stat sites
- Use the disclaimer: *"Not affiliated with or endorsed by Valve Corporation"*
- Do not claim assets are your own IP

### 3.2 Free Clip Tool (Replay-Based)

- **Risk: LOW–MEDIUM**
- Replay reader/clip tools are a tolerated category
- Risk increases if the tool injects commands into a running game process
- Risk decreases significantly if using launch parameters or VDM files (see Section 5)

### 3.3 Paid Clip Tool

- **Risk: MEDIUM–HIGH depending on implementation**
- Charging money increases legal exposure — Valve enforces IP more aggressively when revenue is involved
- Steam Subscriber Agreement (SSA) prohibits automating interaction with the Steam client or game in unauthorized ways
- Console command injection into a running process is the highest-risk implementation
- Launch-parameter-based or VDM-based automation is significantly lower risk
- **Recommendation:** Get a formal legal review before launch. Consider the Overwolf platform as an alternative distribution path (they have official Valve partnerships for CS2/Dota 2)

### 3.4 What Makes an Approach "Safe"

| Implementation | Risk Level | Reason |
|---|---|---|
| Passive screen recording (like Medal) | Lowest | Never touches game process |
| `.dem` file parsing + external renderer | Very Low | Processes files, never runs game |
| Launch game with `+playdemo` args + VDM | Low | Standard game launch, no injection |
| Launch game then send console commands | Medium–High | Closer to automation/botting definition |
| Inject commands into running process | High | Direct SSA violation territory |

---

## 4. Current Implementation (Console Automation)

### 4.1 What It Currently Does

The existing tool performs the following sequence:

1. Opens the Deadlock game client programmatically
2. Waits for the game to load
3. Uses the developer console to load a specific replay file
4. Uses console commands to spectate a specific player
5. Uses console commands to seek to a specific game point/timestamp
6. Triggers recording via console command
7. Repeats steps 4–6 for each kill event

### 4.2 Why This Is Problematic

- **Console injection into a running process** is the specific behavior Valve's SSA targets
- Each kill requires a runtime command sent to the game — the tool is actively controlling the game process
- For a paid product, this is the implementation most likely to attract enforcement
- It is also **technically fragile** — any game update can change console command names, behavior, or add detection

### 4.3 The Core Technical Problem Being Solved

The reason console commands seemed necessary: after loading a replay, you need to **seek to multiple different timestamps** scattered throughout a match (e.g., kills at tick 5000, 14000, 31000). There was no known way to script this without interactive console input.

**This problem is solved by VDM files** (see Section 5).

---

## 5. Target Implementation (VDM-Based)

### 5.1 What is a VDM File?

A VDM (Demo Playback Script) is a plain-text file that sits alongside a `.dem` replay file and contains a list of timed actions to execute automatically during playback. The game engine reads and executes the VDM on its own — no runtime command injection required.

- **Filename convention:** Same name as the `.dem` file, with `.vdm` extension
  - Example: `match_12345678.dem` → `match_12345678.vdm`
- **Location:** Same directory as the `.dem` file
- **Format:** Valve's KeyValues format (VDF)
- **Engine support:** Source 1 and Source 2 (CS2 confirmed; Deadlock needs verification — see Section 9)

### 5.2 VDM File Structure

```
demoactions
{
   "1"
   {
      factory    "SkipAhead"
      name       "skip_to_kill_1"
      starttick  "12450"
      skiptotick "12450"
   }
   "2"
   {
      factory    "PlayCommands"
      name       "start_record_kill_1"
      starttick  "12450"
      commands   "startmovie clip_001"
   }
   "3"
   {
      factory    "PlayCommands"
      name       "stop_record_kill_1"
      starttick  "12600"
      commands   "stopmovie"
   }
   "4"
   {
      factory    "SkipAhead"
      name       "skip_to_kill_2"
      starttick  "12601"
      skiptotick "18900"
   }
   "5"
   {
      factory    "PlayCommands"
      name       "start_record_kill_2"
      starttick  "18900"
      commands   "startmovie clip_002"
   }
   "6"
   {
      factory    "PlayCommands"
      name       "stop_record_kill_2"
      starttick  "19050"
      commands   "stopmovie"
   }
   "7"
   {
      factory    "PlayCommands"
      name       "quit_game"
      starttick  "19051"
      commands   "quit"
   }
}
```

### 5.3 Key VDM Factory Types

| Factory | Purpose | Key Fields |
|---|---|---|
| `SkipAhead` | Jump to a specific tick instantly | `starttick`, `skiptotick` |
| `PlayCommands` | Execute one or more console commands at a tick | `starttick`, `commands` |
| `StopPlayback` | Stop demo playback | `starttick` |
| `ChangePlaybackRate` | Speed up/slow down playback | `starttick`, `playbackrate` |

### 5.4 Tick Calculation

Deadlock runs at **64 ticks per second**.

```
tick = time_in_seconds × 64

# Example: kill at 3:45 into the match
time_seconds = (3 × 60) + 45 = 225
tick = 225 × 64 = 14400

# For a clip starting 5 seconds before the kill:
clip_start_tick = 14400 - (5 × 64) = 14080

# For a clip ending 3 seconds after the kill:
clip_end_tick = 14400 + (3 × 64) = 14592
```

**Important:** Verify Deadlock's tickrate via the community API or `.dem` file header — 64 is standard for Source 2 but confirm before building around it.

### 5.5 Game Launch Command

```bash
# Windows
"C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\bin\win64\deadlock.exe" \
  -novid \
  -console \
  +playdemo "C:\path\to\match_12345678.dem"

# The .vdm file must exist at:
# C:\path\to\match_12345678.vdm
# (same directory and base filename as the .dem)
```

Key launch flags:
- `-novid` — skip intro videos, faster startup
- `-console` — enable developer console
- `+playdemo <path>` — load and start playing the specified demo immediately

---

## 6. Migration Plan

### 6.1 Step-by-Step Migration from Console Automation to VDM

**Step 1: Audit current kill event data**
- Confirm you are already receiving kill timestamps from the match data API
- Verify the timestamp format (seconds vs ticks vs milliseconds)
- Map timestamps → ticks using `tick = seconds × 64`

**Step 2: Build a VDM generator**
- Input: list of `{ tick, clip_name }` objects representing moments to capture
- Output: a valid `.vdm` file string
- Logic: for each event, generate a `SkipAhead` block followed by `PlayCommands` start/stop blocks
- Add a final `quit` command after all clips are captured

```python
# Pseudocode for VDM generator
def generate_vdm(kill_events, pre_roll_ticks=320, post_roll_ticks=192):
    """
    kill_events: list of { 'tick': int, 'name': str }
    pre_roll_ticks: ticks before kill to start clip (default 5s = 320 ticks)
    post_roll_ticks: ticks after kill to end clip (default 3s = 192 ticks)
    """
    blocks = []
    action_index = 1

    for i, event in enumerate(kill_events):
        clip_start = event['tick'] - pre_roll_ticks
        clip_end = event['tick'] + post_roll_ticks
        clip_name = f"clip_{i+1:03d}"

        # Skip to clip start
        blocks.append({
            'index': action_index,
            'factory': 'SkipAhead',
            'name': f"skip_to_{event['name']}",
            'starttick': clip_start - 10,  # small buffer before skip target
            'skiptotick': clip_start
        })
        action_index += 1

        # Start recording
        blocks.append({
            'index': action_index,
            'factory': 'PlayCommands',
            'name': f"record_start_{clip_name}",
            'starttick': clip_start,
            'commands': f"startmovie {clip_name}"
        })
        action_index += 1

        # Stop recording
        blocks.append({
            'index': action_index,
            'factory': 'PlayCommands',
            'name': f"record_stop_{clip_name}",
            'starttick': clip_end,
            'commands': "stopmovie"
        })
        action_index += 1

    # Quit after last clip
    blocks.append({
        'index': action_index,
        'factory': 'PlayCommands',
        'name': 'exit_game',
        'starttick': clip_end + 10,
        'commands': 'quit'
    })

    return render_vdm(blocks)
```

**Step 3: Write the VDM file to disk**
- Must be in the same directory as the `.dem` file
- Must have the same base filename as the `.dem` file
- Written before the game is launched

**Step 4: Replace process interaction with single launch call**
- Remove all code that sends commands to the running game process
- Replace with a single `subprocess.Popen()` (or equivalent) that launches the game with `+playdemo`
- Wait for the process to exit (the `quit` command in the VDM handles this)

**Step 5: Collect output clips**
- The game dumps raw video/frames to its output directory
- Collect all generated clip files after the process exits

**Step 6: FFmpeg post-processing**
- Encode raw frames to H.264/H.265
- Apply any overlays (player name, hero icon, kill count)
- Stitch all clips into a single highlight reel with transitions

### 6.2 Files to Modify

| Current File/Module | Change Required |
|---|---|
| Game launcher module | Replace process communication with single launch + args |
| Console command sender | **Delete entirely** — replaced by VDM |
| Seek/navigation logic | **Delete entirely** — replaced by VDM SkipAhead blocks |
| Recording trigger logic | **Delete entirely** — replaced by VDM PlayCommands |
| New: VDM generator module | **Create new** — generates `.vdm` from kill event list |
| New: VDM file writer | **Create new** — writes `.vdm` to correct path before launch |

---

## 7. Full Pipeline Architecture

```
┌─────────────────────────────────────────────────┐
│              INPUT LAYER                        │
│                                                 │
│  Match ID / Steam ID                            │
│         ↓                                       │
│  Deadlock Community API                         │
│  GET /v2/matches/{id}                           │
│         ↓                                       │
│  Kill events with timestamps                    │
│  [ { tick, killer, victim, position } ]         │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│              PROCESSING LAYER                   │
│                                                 │
│  1. Filter events (kills, streaks, clutches)    │
│  2. Convert timestamps → ticks (×64)            │
│  3. Add pre/post roll buffer to each tick       │
│  4. Generate VDM file content                   │
│  5. Write .vdm to same dir as .dem file         │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│              GAME LAYER                         │
│                                                 │
│  Launch: deadlock.exe -novid +playdemo X.dem    │
│                                                 │
│  VDM auto-executes:                             │
│    → SkipAhead to clip_001 start                │
│    → startmovie clip_001                        │
│    → stopmovie                                  │
│    → SkipAhead to clip_002 start                │
│    → startmovie clip_002                        │
│    → stopmovie                                  │
│    → ... (all clips)                            │
│    → quit                                       │
│                                                 │
│  Process exits automatically                    │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│              OUTPUT LAYER                       │
│                                                 │
│  Raw clip files on disk (TGA frames or AVI)     │
│         ↓                                       │
│  FFmpeg encoding (H.264 / H.265)                │
│         ↓                                       │
│  Optional: add overlays (hero icon, kill text)  │
│         ↓                                       │
│  FFmpeg stitch → single highlight reel .mp4     │
│         ↓                                       │
│  Deliver to user / upload to storage            │
└─────────────────────────────────────────────────┘
```

---

## 8. Key Technical References

### 8.1 Replay Parsing Libraries (Source 2 / .dem format)

If you need to parse `.dem` files directly (e.g., to extract kill ticks yourself rather than relying on the API):

- **clarity** (Java) — mature Dota 2 / Source 2 parser: `https://github.com/skadistats/clarity`
- **manta** (Go) — Source 2 replay parser: `https://github.com/dotabuff/manta`
- **demoinfocs-golang** (Go) — CS2/Source 2 focus: `https://github.com/markus-wa/demoinfocs-golang`
- **awpy** (Python) — CS2 demo parser, Source 2 based: `https://github.com/pnxenopoulos/awpy`

These libraries parse the protobuf-encoded game state and emit events (kills, item purchases, positions, etc.) as structured data.

### 8.2 VDM Documentation

- **Primary reference:** Valve Developer Wiki — [Demo Recording Tools](https://developer.valvesoftware.com/wiki/Demo_Recording_Tools)
- **CS2 community VDM research** — most up-to-date Source 2 VDM documentation lives in CS2 modding communities; findings transfer directly to Deadlock
- **Source 1 VDM format** — well documented, mostly compatible reference point

### 8.3 FFmpeg Commands

```bash
# Encode raw TGA frame sequence to H.264
ffmpeg -framerate 60 -i clip_001_%04d.tga -c:v libx264 -pix_fmt yuv420p clip_001.mp4

# Stitch multiple clips into a highlight reel
# First create a filelist.txt:
# file 'clip_001.mp4'
# file 'clip_002.mp4'
ffmpeg -f concat -safe 0 -i filelist.txt -c copy highlight_reel.mp4

# Add a simple text overlay
ffmpeg -i clip_001.mp4 -vf "drawtext=text='KILL':fontsize=48:fontcolor=white:x=50:y=50" clip_001_overlay.mp4
```

### 8.4 Relevant Steam / Valve Links

- Steam Web API key: `https://steamcommunity.com/dev`
- Steam API TOS: `https://steamcommunity.com/dev/apiterms`
- Valve Developer Wiki (Deadlock): `https://developer.valvesoftware.com/wiki/Deadlock`
- Steam Subscriber Agreement: `https://store.steampowered.com/subscriber_agreement/`
- Overwolf developer program (legitimate partnership path): `https://www.overwolf.com/creators/`

---

## 9. Known Risks & Open Questions

### 9.1 Must Verify Before Building

- [ ] **Deadlock VDM support confirmed?** — Source 2 supports VDM, but Deadlock specifically needs testing. CS2 is the best reference point. Test `SkipAhead` and `PlayCommands` factory types in Deadlock before building the generator.
- [ ] **Exact Deadlock tickrate** — Assumed 64 tick (standard Source 2). Verify from `.dem` file header or community documentation.
- [ ] **`startmovie` / `stopmovie` available in Deadlock?** — These are the recording console commands. Confirm they exist and function in Deadlock's console.
- [ ] **Output format of `startmovie`** — In Source 1 it outputs TGA frame sequences. Source 2 / CS2 behavior may differ. Confirm what files are produced and where they go.
- [ ] **`.dem` file location** on Windows for Deadlock — Find the exact path where Deadlock stores replay files so the tool can locate them automatically.

### 9.2 Business / Legal Risks

- [ ] Valve could change stance on paid automation tools at full game launch — the game is still in beta
- [ ] Community API (assets.deadlock-api.com) has no uptime SLA — self-host/cache all assets
- [ ] Steam API TOS compliance review needed before charging for access
- [ ] Consider Overwolf platform as a safer distribution alternative for the paid tier

### 9.3 Technical Fragility Points

- Game updates can rename or remove console commands used in VDM `PlayCommands` blocks
- Replay file format can change between patches (as happened with CS2 and Dota 2 historically)
- Community API endpoints may change — version-lock your API calls and handle deprecations

---

## 10. Summary of What Claude Code Should Do

When working on this codebase, the primary task is:

1. **Remove** all modules that inject console commands into a running game process
2. **Create** a `VdmGenerator` class/module that takes a list of kill events (with ticks) and produces a valid `.vdm` file string
3. **Create** a `VdmWriter` module that places the `.vdm` file at the correct path relative to the `.dem` file
4. **Modify** the game launcher to use a single `+playdemo` launch call instead of sequential command injection
5. **Keep** the FFmpeg post-processing pipeline — it is implementation-agnostic and does not need to change
6. **Verify** VDM factory type support in Deadlock before finalizing the generator (test environment required)

The end state is a tool that: fetches kill data → generates a VDM → writes it to disk → launches game once → waits for exit → processes output clips. Zero runtime interaction with the game process.