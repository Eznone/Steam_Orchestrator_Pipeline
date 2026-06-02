import logging
from collections.abc import Callable

from deadlock_clipper.config import load_config

logger = logging.getLogger(__name__)

_gc = load_config().get("game_constants", {})
_KILL_LABELS: dict[int, str]      = {int(k): v for k, v in _gc.get("kill_labels", {}).items()}
_TEAM_NAMES: dict[int, str]       = {int(k): v for k, v in _gc.get("team_names", {}).items()}
_LANE_NAMES: dict[int, str]       = {int(k): v for k, v in _gc.get("lane_names", {}).items()}
_OBJECTIVE_LABELS: dict[str, str] = _gc.get("objective_labels", {})
del _gc


def _detect_for_all_players(
    fn: Callable[..., list[dict]],
    kills: list[dict],
    players: list[dict],
    *args,
) -> list[dict]:
    """Run a clip-zone detector for every player and return results grouped by player."""
    result = []
    for p in players:
        zones = fn(kills, p["hero_id"], *args)
        for z in zones:
            z["player_name"] = p["player_name"]
        result.extend(zones)
    result.sort(key=lambda z: (z["player_name"], z["start_tick"]))
    return result


def find_player_hero_id(players: list[dict], steam_id: str | int) -> int | None:
    target = str(steam_id)
    for p in players:
        if str(p["steam_id"]) == target:
            return p["hero_id"]
    return None


def find_multikills(
    kills: list[dict],
    target_hero_id: int,
    window_ticks: int,
    threshold: int,
    lead_ticks: int,
    buffer_ticks: int,
) -> list[dict]:
    """Detect multi-kill sequences for a single hero.

    Uses a greedy left-anchor approach: anchor on each kill in order and collect
    all subsequent kills that fall within `window_ticks`. Groups that meet
    `threshold` become clip zones. Each kill is consumed into at most one group.
    """
    player_kills = sorted(
        (k for k in kills if k["attacker_hero_id"] == target_hero_id),
        key=lambda k: k["tick"],
    )

    if not player_kills:
        return []

    clip_zones: list[dict] = []
    consumed: set[int] = set()

    for i, anchor in enumerate(player_kills):
        if i in consumed:
            continue

        group = [anchor]
        for j in range(i + 1, len(player_kills)):
            if player_kills[j]["tick"] - anchor["tick"] <= window_ticks:
                group.append(player_kills[j])
            else:
                break

        if len(group) >= threshold:
            for k in range(i, i + len(group)):
                consumed.add(k)

            first_tick = group[0]["tick"]
            last_tick = group[-1]["tick"]
            count = len(group)
            reason = _KILL_LABELS.get(count, f"{count}x Kill Streak")

            clip_zones.append(
                {
                    "start_tick": max(0, first_tick - lead_ticks),
                    "end_tick": last_tick + buffer_ticks,
                    "kill_count": count,
                    "reason": reason,
                    "kill_ticks": [k["tick"] for k in group],
                    "event_type": "multikill",
                    "detail": "",
                }
            )

    return clip_zones


def find_single_kills(
    kills: list[dict],
    target_hero_id: int,
    lead_ticks: int,
    buffer_ticks: int,
) -> list[dict]:
    """One clip zone per kill by the target hero."""
    player_kills = sorted(
        (k for k in kills if k["attacker_hero_id"] == target_hero_id),
        key=lambda k: k["tick"],
    )
    return [
        {
            "start_tick": max(0, k["tick"] - lead_ticks),
            "end_tick": k["tick"] + buffer_ticks,
            "kill_count": 1,
            "reason": "Kill",
            "kill_ticks": [k["tick"]],
            "event_type": "single_kill",
            "detail": f"vs {k['victim_hero_name']}",
        }
        for k in player_kills
    ]


def find_kill_streaks(
    kills: list[dict],
    target_hero_id: int,
    streak_threshold: int,
    lead_ticks: int,
    buffer_ticks: int,
) -> list[dict]:
    """Detect kill streaks (consecutive kills without dying) for a single hero.

    Distinct from multi-kill: a streak resets only on death, not on time.
    One clip zone is emitted per streak run that reaches `streak_threshold`,
    extended as additional kills are added to the same run.
    """
    events: list[tuple[str, int, dict]] = []
    for k in kills:
        if k["attacker_hero_id"] == target_hero_id:
            events.append(("kill", k["tick"], k))
        if k["victim_hero_id"] == target_hero_id:
            events.append(("death", k["tick"], k))
    events.sort(key=lambda e: e[1])

    current_streak = 0
    streak_kills: list[dict] = []
    active_zone: dict | None = None
    clip_zones: list[dict] = []

    for ev_type, _tick, k in events:
        if ev_type == "death":
            if active_zone is not None:
                clip_zones.append(active_zone)
                active_zone = None
            current_streak = 0
            streak_kills = []
        else:
            current_streak += 1
            streak_kills.append(k)

            if current_streak == streak_threshold:
                active_zone = {
                    "start_tick": max(0, streak_kills[0]["tick"] - lead_ticks),
                    "end_tick": k["tick"] + buffer_ticks,
                    "kill_count": current_streak,
                    "reason": f"{current_streak}x Kill Streak",
                    "kill_ticks": [kk["tick"] for kk in streak_kills],
                    "event_type": "kill_streak",
                    "detail": f"Streak of {current_streak} (no death)",
                }
            elif current_streak > streak_threshold and active_zone is not None:
                active_zone["end_tick"] = k["tick"] + buffer_ticks
                active_zone["kill_count"] = current_streak
                active_zone["reason"] = f"{current_streak}x Kill Streak"
                active_zone["kill_ticks"].append(k["tick"])
                active_zone["detail"] = f"Streak of {current_streak} (no death)"

    if active_zone is not None:
        clip_zones.append(active_zone)

    return clip_zones


def find_objective_destructions(
    objectives_destroyed: list[dict],
    objective_types: list[str],
    lead_ticks: int,
    buffer_ticks: int,
) -> list[dict]:
    """One clip zone per destroyed objective matching the requested types."""
    result: list[dict] = []
    for obj in objectives_destroyed:
        if obj["objective_type"] not in objective_types:
            continue

        team_label = _TEAM_NAMES.get(obj["team_num"], f"Team {obj['team_num']}")
        lane_label = _LANE_NAMES.get(obj["lane"], "")
        detail = team_label + (f" · {lane_label} Lane" if lane_label else "")
        reason = _OBJECTIVE_LABELS.get(obj["objective_type"], obj["objective_type"].title())

        result.append(
            {
                "start_tick": max(0, obj["tick"] - lead_ticks),
                "end_tick": obj["tick"] + buffer_ticks,
                "kill_count": 0,
                "reason": reason,
                "kill_ticks": [obj["tick"]],
                "event_type": "objective",
                "detail": detail,
            }
        )
    return result


def analyze(parsed_data: dict, config: dict) -> list[dict]:
    """Identify clip zones from parsed match data using rules from config.

    Returns:
        List of clip zone objects with clip_id, start_tick, end_tick, reason,
        event_type, detail, kill_count, kill_ticks.
    """
    cfg = config.get("analyzer", {})
    event_type: str = cfg.get("event_type", "multikill")
    steam_id: str = cfg.get("target_player_steam_id", "")
    lead_ticks: int = int(cfg.get("clip_lead_ticks", 200))
    buffer_ticks: int = int(cfg.get("clip_buffer_ticks", 300))

    tick_rate: int = parsed_data.get("tick_rate", 64)
    players: list[dict] = parsed_data.get("players", [])
    kills: list[dict] = parsed_data.get("kills", [])
    objectives_destroyed: list[dict] = parsed_data.get("objectives_destroyed", [])

    if event_type == "objective":
        objective_types: list[str] = cfg.get("objective_types", ["walker", "patron"])
        zones = find_objective_destructions(objectives_destroyed, objective_types, lead_ticks, buffer_ticks)
        for z in zones:
            z["player_name"] = ""

    else:
        # Resolve target hero
        hero_id: int | None = None
        target_player_name: str = ""
        if steam_id:
            hero_id = find_player_hero_id(players, steam_id)
            if hero_id is None:
                logger.warning("Steam ID %s not found in this match — no clips generated.", steam_id)
                return []
            target_player_name = next(
                (p["player_name"] for p in players if str(p["steam_id"]) == str(steam_id)), ""
            )
            logger.info("Analyzing %s for hero_id=%d (steam_id=%s)", event_type, hero_id, steam_id)

        if event_type == "single_kill":
            if hero_id is not None:
                zones = find_single_kills(kills, hero_id, lead_ticks, buffer_ticks)
            else:
                zones = _detect_for_all_players(find_single_kills, kills, players, lead_ticks, buffer_ticks)

        elif event_type == "kill_streak":
            streak_threshold: int = int(cfg.get("kill_streak_threshold", 3))
            if hero_id is not None:
                zones = find_kill_streaks(kills, hero_id, streak_threshold, lead_ticks, buffer_ticks)
            else:
                zones = _detect_for_all_players(find_kill_streaks, kills, players, streak_threshold, lead_ticks, buffer_ticks)

        else:  # multikill (default)
            window_seconds: float = float(cfg.get("multikill_window_seconds", 10))
            threshold: int = int(cfg.get("multikill_threshold", 2))
            window_ticks: int = int(window_seconds * tick_rate)
            if hero_id is not None:
                zones = find_multikills(kills, hero_id, window_ticks, threshold, lead_ticks, buffer_ticks)
            else:
                zones = _detect_for_all_players(find_multikills, kills, players, window_ticks, threshold, lead_ticks, buffer_ticks)

        if hero_id is not None:
            for z in zones:
                z["player_name"] = target_player_name

    result = [{"clip_id": f"{i:02d}", **zone} for i, zone in enumerate(zones, start=1)]
    logger.info("Found %d clip zone(s) [event_type=%s] in match %s.", len(result), event_type, parsed_data.get("match_id"))
    return result


