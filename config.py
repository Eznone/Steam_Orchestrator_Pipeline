import os

import yaml
from dotenv import load_dotenv


def load_config(config_path: str = "config.yaml") -> dict:
    """Load config.yaml and overlay sensitive values from .env / environment variables.

    Environment variables take precedence over config.yaml:
        DEADLOCK_STEAM_ID  → analyzer.target_player_steam_id
        OBS_PASSWORD       → recording.obs_password
    """
    load_dotenv()
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    steam_id = os.getenv("DEADLOCK_STEAM_ID")
    if steam_id:
        cfg.setdefault("analyzer", {})["target_player_steam_id"] = steam_id

    obs_password = os.getenv("OBS_PASSWORD")
    if obs_password is not None:
        cfg.setdefault("recording", {})["obs_password"] = obs_password

    return cfg
