"""VDM viability probe — does Deadlock execute .vdm playback scripts?

Run this on Windows-native Python BEFORE building the VDM migration. It writes a
minimal test VDM next to a chosen .dem replay, launches the game with

    deadlock.exe -novid -console +playdemo <stem>

and then watches the process and the output directory to answer three questions:

  Signal 1 — Does the game quit on its own?      → PlayCommands works at all
  Signal 2 — Did it quit faster than real-time?  → SkipAhead works
  Signal 3 — Did startmovie write output files?   → full capture is viable

You do not need to watch the game. The test VDM ends with a `quit` command, so a
working VDM closes the process by itself; this script just times that and scans
for output.

Usage (PowerShell / cmd):
    python scripts\test_vdm.py --dry-run          # preview the VDM, write/launch nothing
    python scripts\test_vdm.py                     # run against newest replay
    python scripts\test_vdm.py --dem match_12345678.dem
    python scripts\test_vdm.py --skip-tick 20000 --timeout 120

Exit code is 0 if Signal 1 passed (VDM was executed), 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ── Defaults (match config.yaml / project conventions) ────────────────────────

DEFAULT_DEADLOCK_EXE = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\bin\win64\deadlock.exe"
)
DEFAULT_REPLAYS_DIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\citadel\replays"
)
# startmovie dumps into the game's working dir (citadel\) on Source 2 / CS2.
DEFAULT_OUTPUT_DIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Deadlock\game\citadel"
)

MOVIE_NAME = "vdm_test"
TICKS_PER_SECOND = 64  # Source 2 default; one of the things this test indirectly checks

# Patterns startmovie might produce: AVI, raw frames (TGA), or paired WAV audio.
OUTPUT_GLOBS = [f"{MOVIE_NAME}*", f"{MOVIE_NAME}*.*"]


# ── VDM generation ────────────────────────────────────────────────────────────


def build_test_vdm(skip_tick: int, record_ticks: int) -> str:
    """Return the text of a minimal VDM exercising all three signals.

    Skips far into the demo, records a short movie, then quits. If SkipAhead works
    the game reaches `skip_tick` near-instantly instead of playing in real time.
    """
    stop_tick = skip_tick + record_ticks
    quit_tick = stop_tick + 1
    return f"""demoactions
{{
    "1"
    {{
        factory    "SkipAhead"
        name       "jump_to_test_point"
        starttick  "100"
        skiptotick "{skip_tick}"
    }}
    "2"
    {{
        factory    "PlayCommands"
        name       "start_recording"
        starttick  "{skip_tick}"
        commands   "startmovie {MOVIE_NAME}"
    }}
    "3"
    {{
        factory    "PlayCommands"
        name       "stop_recording"
        starttick  "{stop_tick}"
        commands   "stopmovie"
    }}
    "4"
    {{
        factory    "PlayCommands"
        name       "quit_game"
        starttick  "{quit_tick}"
        commands   "quit"
    }}
}}
"""


# ── Filesystem helpers ────────────────────────────────────────────────────────


def find_dem(replays_dir: Path, dem_arg: str | None) -> Path:
    """Resolve the .dem to test against, or raise with a helpful message."""
    if dem_arg:
        candidate = Path(dem_arg)
        if not candidate.is_absolute():
            candidate = replays_dir / candidate
        if candidate.suffix != ".dem":
            candidate = candidate.with_suffix(".dem")
        if not candidate.exists():
            raise FileNotFoundError(f"Replay not found: {candidate}")
        return candidate

    dems = sorted(replays_dir.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dems:
        raise FileNotFoundError(
            f"No .dem files in {replays_dir}. Pass one with --dem, or check the path."
        )
    return dems[0]


def snapshot_outputs(output_dir: Path) -> set[Path]:
    """Existing movie-output files, so we can detect newly created ones later."""
    found: set[Path] = set()
    for pattern in OUTPUT_GLOBS:
        found.update(output_dir.glob(pattern))
    return found


# ── Main probe ────────────────────────────────────────────────────────────────


def run_probe(args: argparse.Namespace) -> int:
    import subprocess  # local import keeps the module importable for unit tests

    deadlock_exe = Path(args.deadlock_exe)
    replays_dir = Path(args.replays_dir)
    output_dir = Path(args.output_dir)

    # --dry-run previews the VDM and launch command on any machine, so it runs
    # before the game/replay existence checks (it needs neither installed).
    if args.dry_run:
        try:
            stem = find_dem(replays_dir, args.dem).stem
        except FileNotFoundError:
            stem = "<replay_stem>"
        print("[dry-run] VDM that would be written:\n")
        print(build_test_vdm(args.skip_tick, args.record_ticks))
        print(f"[dry-run] Would write it to : {replays_dir / (stem + '.vdm')}")
        print(f"[dry-run] Would launch      : {deadlock_exe} -novid -console +playdemo {stem}")
        print("[dry-run] Nothing was written and the game was not launched.")
        return 0

    if not deadlock_exe.exists():
        print(f"[!] deadlock.exe not found at {deadlock_exe}")
        print("    Pass the correct path with --deadlock-exe.")
        return 1

    dem = find_dem(replays_dir, args.dem)
    stem = dem.stem
    vdm_path = dem.with_suffix(".vdm")

    print("=" * 64)
    print("VDM viability probe")
    print("=" * 64)
    print(f"  replay      : {dem}")
    print(f"  vdm written : {vdm_path}")
    print(f"  skip to tick: {args.skip_tick}  (~{args.skip_tick / TICKS_PER_SECOND:.0f}s into match)")
    print(f"  output scan : {output_dir}")
    print()

    vdm_text = build_test_vdm(args.skip_tick, args.record_ticks)
    cmd = [str(deadlock_exe), "-novid", "-console", "+playdemo", stem]

    if vdm_path.exists() and not args.overwrite:
        print(f"[!] A .vdm already exists at {vdm_path}. Re-run with --overwrite to replace it.")
        return 1
    vdm_path.write_text(vdm_text, encoding="utf-8")
    print(f"[+] Wrote test VDM ({args.record_ticks} ticks of recording).")

    before = snapshot_outputs(output_dir)
    print(f"[i] {len(before)} pre-existing '{MOVIE_NAME}*' file(s) in output dir.")

    print(f"\n[>] Launching: {' '.join(cmd)}")
    start = time.monotonic()
    proc = subprocess.Popen(cmd)

    quit_on_its_own = False
    try:
        proc.wait(timeout=args.timeout)
        quit_on_its_own = True
    except subprocess.TimeoutExpired:
        print(f"\n[!] Game still running after {args.timeout}s — killing it.")
        proc.kill()
        proc.wait()
    elapsed = time.monotonic() - start

    if not args.keep_vdm:
        vdm_path.unlink(missing_ok=True)

    # Allow the OS a moment to flush any movie files the engine just closed.
    time.sleep(2)
    after = snapshot_outputs(output_dir)
    new_outputs = sorted(after - before)

    # Real-time baseline: how long playback *would* take to reach the quit tick
    # if SkipAhead did nothing.
    realtime_baseline = (args.skip_tick + args.record_ticks) / TICKS_PER_SECOND

    print("\n" + "=" * 64)
    print("RESULTS")
    print("=" * 64)

    print(f"  process ran for : {elapsed:.1f}s")

    # Signal 1
    sig1 = quit_on_its_own
    print(f"\n  Signal 1 — game quit on its own ......... {'PASS' if sig1 else 'FAIL'}")
    if sig1:
        print("     → VDM was read and PlayCommands executed (the 'quit' fired).")
    else:
        print("     → VDM appears ignored: no quit fired before timeout.")
        print("       Check: is the .vdm beside the .dem with the same stem?")

    # Signal 2 — only meaningful if it quit on its own
    if sig1:
        sig2 = elapsed < realtime_baseline * 0.5
        print(f"\n  Signal 2 — SkipAhead jumped instantly ... {'PASS' if sig2 else 'FAIL'}")
        print(f"     real-time baseline to reach quit tick: ~{realtime_baseline:.0f}s")
        if sig2:
            print("     → Quit far sooner than real-time: SkipAhead works.")
        else:
            print("     → Took ~real-time: PlayCommands works but SkipAhead may not.")
    else:
        print("\n  Signal 2 — SkipAhead ..................... SKIPPED (Signal 1 failed)")

    # Signal 3
    sig3 = bool(new_outputs)
    print(f"\n  Signal 3 — startmovie wrote output ...... {'PASS' if sig3 else 'FAIL'}")
    if sig3:
        print("     New files:")
        for p in new_outputs:
            print(f"       {p.name}  ({p.stat().st_size} bytes)")
    else:
        print(f"     → No new '{MOVIE_NAME}*' files in {output_dir}.")
        print("       startmovie may output elsewhere — search the game tree for")
        print(f"       '{MOVIE_NAME}' if Signal 1 passed but this failed.")

    print("\n" + "=" * 64)
    if sig1 and sig3:
        print("VERDICT: VDM migration is viable. Full pipeline confirmed.")
    elif sig1:
        print("VERDICT: VDM executes, but verify startmovie output format/location.")
    else:
        print("VERDICT: VDM not executed — migration blocked. Investigate before building.")
    print("=" * 64)

    return 0 if sig1 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe whether Deadlock executes VDM playback scripts.")
    p.add_argument("--dem", help="Replay filename or path (default: newest .dem in replays dir).")
    p.add_argument("--deadlock-exe", default=str(DEFAULT_DEADLOCK_EXE), help="Path to deadlock.exe.")
    p.add_argument("--replays-dir", default=str(DEFAULT_REPLAYS_DIR), help="Deadlock replays folder.")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Where startmovie writes output.")
    p.add_argument("--skip-tick", type=int, default=20000, help="Tick to SkipAhead to (default 20000 ~5min).")
    p.add_argument("--record-ticks", type=int, default=64, help="Ticks to record (default 64 = ~1s).")
    p.add_argument("--timeout", type=float, default=120.0, help="Max seconds to wait for the game to quit.")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing .vdm beside the replay.")
    p.add_argument("--keep-vdm", action="store_true", help="Leave the test .vdm on disk after running.")
    p.add_argument("--dry-run", action="store_true", help="Print the VDM and launch command, then exit without writing or launching.")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        return run_probe(args)
    except FileNotFoundError as e:
        print(f"[!] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
