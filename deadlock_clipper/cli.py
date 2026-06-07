import argparse
import json
import logging
import sys
import time
from pathlib import Path

from deadlock_clipper.config import load_config
from deadlock_clipper.core.analyzer import analyze
from deadlock_clipper.core.parser import parse_demo


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def cmd_parse(args: argparse.Namespace) -> None:
    config = load_config()
    out = Path(config.get("parser", {}).get("output_dir", "data/parsed")) / f"{Path(args.dem).stem}.json"
    result = parse_demo(args.dem, out)
    print(
        f"Match {result['match_id']} | {result['total_clock_time']} "
        f"| {len(result['kills'])} kills → {out}"
    )


def cmd_analyze(args: argparse.Namespace) -> None:
    config = load_config()
    with open(args.parsed) as f:
        parsed_data = json.load(f)
    clips = analyze(parsed_data, config)
    tick_rate = parsed_data.get("tick_rate", 64)
    print(f"\nFound {len(clips)} clip zone(s) in match {parsed_data['match_id']}:")
    for clip in clips:
        start_s = clip["start_tick"] / tick_rate
        end_s = clip["end_tick"] / tick_rate
        print(
            f"  [{clip['clip_id']}] {clip['reason']:<22} "
            f"ticks {clip['start_tick']}–{clip['end_tick']}  "
            f"({start_s:.1f}s – {end_s:.1f}s)"
            + (f"  {clip['detail']}" if clip.get("detail") else "")
        )


def cmd_capture_test(args: argparse.Namespace) -> None:
    import tempfile

    from deadlock_clipper.recording.vidgear_controller import CaptureError, VidGearController

    config = load_config()
    print("Testing capture backend (Deadlock must be running)...")
    try:
        with VidGearController.from_config(config) as ctl:
            print("Connected. Deadlock window found.")
            if args.record:
                ctl.set_record_directory(tempfile.mkdtemp())
                print("Starting 3-second test recording...")
                ctl.start_recording()
                time.sleep(3)
                path = ctl.stop_recording()
                print(f"Saved to: {path}")
    except CaptureError as exc:
        print(f"Capture error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_launch_test(args: argparse.Namespace) -> None:
    from deadlock_clipper.recording.client_launcher import (
        _DEFAULT_STEAM_EXE,
        hide_hud,
        launch_demo,
        wait_for_launch,
    )

    config = load_config()
    rec = config.get("recording", {})
    steam_exe = rec.get("steam_exe", _DEFAULT_STEAM_EXE)
    launch_wait = float(rec.get("launch_wait_seconds", 30.0))

    launch_demo(args.dem, steam_exe)
    wait_for_launch(launch_wait)
    print("Game should be loaded. Sending test console command...")
    hide_hud()
    print("Done.")


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(
        prog="deadlock-clipper",
        description="Parse Deadlock replays and detect highlight clip zones.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="Parse a .dem replay file to JSON")
    p.add_argument("dem", help="Path to the .dem replay file")

    a = sub.add_parser("analyze", help="Detect clip zones from a parsed JSON file")
    a.add_argument("parsed", help="Path to a parsed match JSON file")

    o = sub.add_parser("capture-test", help="Test the VidGear screen capture backend")
    o.add_argument("--record", action="store_true", help="Do a 3-second test recording")

    l = sub.add_parser("launch-test", help="Launch a demo and verify HUD hide works")
    l.add_argument("dem", help="Path to the .dem file to launch")

    args = parser.parse_args()
    {
        "parse":        cmd_parse,
        "analyze":      cmd_analyze,
        "capture-test": cmd_capture_test,
        "launch-test":  cmd_launch_test,
    }[args.command](args)


if __name__ == "__main__":
    main()
