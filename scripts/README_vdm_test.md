# VDM Viability Test

Before migrating the recording pipeline from console injection to VDM
(see [../tasks/VDM-vaiability.md](../tasks/VDM-vaiability.md)), confirm that
Deadlock actually executes `.vdm` playback scripts. This script does that.

It writes a minimal test VDM next to a replay, launches the game with

```
deadlock.exe -novid -console +playdemo <stem>
```

and reads three signals from the process behavior and the filesystem. **You do
not need to watch the game** — the test VDM ends with a `quit` command, so a
working VDM closes the process by itself.

> Run on **Windows-native Python**, not WSL. The game and its paths only exist
> on the Windows side. (`--dry-run` is the one exception — it works anywhere.)

---

## 1. Prerequisites

- At least one `.dem` replay in your Deadlock replays folder:
  `C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\citadel\replays`
- Deadlock fully closed before you start (the test launches its own instance).

---

## 2. Preview first (no game needed)

Run a dry run to see exactly what VDM will be written and what command will
launch. This writes nothing and launches nothing:

```powershell
python scripts\test_vdm.py --dry-run
```

Eyeball the VDM block. If it looks right, do a real run.

---

## 3. Run the test

```powershell
python scripts\test_vdm.py
```

This picks the **newest** `.dem` in the replays folder automatically. To target
a specific replay:

```powershell
python scripts\test_vdm.py --dem match_12345678.dem
```

The game window will appear briefly (`-novid` skips intros but there is no
headless mode for a Source 2 client). Leave it alone — if the VDM works it quits
on its own within the timeout.

---

## 4. Reading the results

The script prints a PASS/FAIL for three signals:

| Signal | PASS means | If it FAILS |
|---|---|---|
| **1 — game quit on its own** | The VDM was read and `PlayCommands` executed. | VDM is ignored. Confirm the `.vdm` is in the same folder as the `.dem` and shares the exact stem. This is the migration blocker. |
| **2 — quit faster than real-time** | `SkipAhead` jumped instantly instead of playing through. | The engine played in real time. `PlayCommands` still works; `SkipAhead` may be unsupported (seek another way). |
| **3 — startmovie wrote output** | `startmovie`/`stopmovie` produced files — full capture is viable. | Recording command ran but output landed elsewhere. Search the game tree for `vdm_test` and re-run with `--output-dir <that path>`. |

The final **VERDICT** line summarizes whether the migration is viable. The
script's **exit code** is `0` only if Signal 1 passed, so you can gate a build
step on it.

### Example of a full pass

```
  Signal 1 — game quit on its own ......... PASS
  Signal 2 — SkipAhead jumped instantly ... PASS
  Signal 3 — startmovie wrote output ...... PASS
     New files:
       vdm_test.avi  (8421376 bytes)

VERDICT: VDM migration is viable. Full pipeline confirmed.
```

---

## 5. Options

| Flag | Default | Purpose |
|---|---|---|
| `--dem` | newest `.dem` | Replay filename or full path to test against. |
| `--deadlock-exe` | standard Steam path | Path to `deadlock.exe` (override if Steam is on another drive). |
| `--replays-dir` | standard Steam path | Folder holding the `.dem` files. |
| `--output-dir` | `...\game\citadel\` | Where the script scans for `startmovie` output. |
| `--skip-tick` | `20000` (~5 min) | Tick to `SkipAhead` to. Lower it for short replays. |
| `--record-ticks` | `64` (~1 s) | How long to record before stopping. |
| `--timeout` | `120` | Max seconds to wait before force-killing the game. |
| `--overwrite` | off | Replace an existing `.vdm` next to the replay. |
| `--keep-vdm` | off | Leave the test `.vdm` on disk after running (for inspection). |
| `--dry-run` | off | Print the VDM + launch command, then exit. Writes/launches nothing. |

---

## 6. Troubleshooting

- **"deadlock.exe not found"** — Steam is on a different drive or path. Pass
  `--deadlock-exe "D:\...\deadlock.exe"`.
- **"No .dem files in ..."** — Wrong replays folder, or you have no replays.
  Pass `--replays-dir` or `--dem`.
- **"A .vdm already exists"** — A previous run left one (or you used
  `--keep-vdm`). Re-run with `--overwrite`.
- **Signal 1 passed but Signal 3 failed** — Recording worked but output is
  somewhere unexpected. From the game's `citadel\` folder, search for files
  containing `vdm_test`, then re-run with `--output-dir` pointing there. This
  also tells you the output **format** (`.avi` vs TGA frame sequence vs `.wav`),
  which the real pipeline needs to know for FFmpeg encoding.

---

## 7. After a green run

A full PASS confirms the assumptions in the migration guide. Next:

1. Note the **output format and location** from Signal 3 — the FFmpeg step
   depends on it.
2. Build the `VdmGenerator` / `VdmWriter` modules described in
   [../tasks/VDM-vaiability.md](../tasks/VDM-vaiability.md) §6.
3. Delete the console-injection code
   (`send_console_command`, `goto_tick`, `hide_hud`, `prepare_replay`).
