import os

import yaml
from dotenv import load_dotenv

_cache: dict | None = None


def load_config(config_path: str = "config.yaml", *, force_reload: bool = False) -> dict:
    """Load config.yaml and overlay sensitive values from .env / environment variables.

    Result is cached after the first load. Pass force_reload=True to re-read from disk.

    Environment variables take precedence over config.yaml:
        DEADLOCK_STEAM_ID  → analyzer.target_player_steam_id
    """
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    load_dotenv()
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    steam_id = os.getenv("DEADLOCK_STEAM_ID")
    if steam_id:
        cfg.setdefault("analyzer", {})["target_player_steam_id"] = steam_id

    _cache = cfg
    return _cache
