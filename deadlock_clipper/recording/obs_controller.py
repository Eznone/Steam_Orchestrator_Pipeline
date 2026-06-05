import logging
import time

logger = logging.getLogger(__name__)

try:
    import obsws_python as obs
    _OBS_AVAILABLE = True
except ImportError:
    _OBS_AVAILABLE = False


class OBSConnectionError(Exception):
    pass


class OBSController:
    """Thin wrapper around obsws-python for the clip pipeline.

    Usage as a context manager:
        with OBSController.from_config(config) as ctl:
            ctl.start_recording()
            ...
            path = ctl.stop_recording()

    Or manual connect/disconnect:
        ctl = OBSController("localhost", 4455, "password")
        ctl.connect()
        ...
        ctl.disconnect()
    """

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = ""):
        if not _OBS_AVAILABLE:
            raise ImportError("obsws-python is not installed. Run: uv add obsws-python")
        self._host = host
        self._port = port
        self._password = password
        self._client = None

    @classmethod
    def from_config(cls, config: dict) -> "OBSController":
        cfg = config.get("recording", {})
        return cls(
            host=cfg.get("obs_host", "localhost"),
            port=int(cfg.get("obs_port", 4455)),
            password=cfg.get("obs_password", ""),
        )

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            self._client = obs.ReqClient(
                host=self._host, port=self._port, password=self._password
            )
            logger.info("Connected to OBS at %s:%d", self._host, self._port)
        except Exception as exc:
            raise OBSConnectionError(
                f"Could not connect to OBS at {self._host}:{self._port} — "
                "make sure OBS is open and WebSocket server is enabled."
            ) from exc

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception as exc:
                logger.warning("Error during OBS disconnect: %s", exc)
            self._client = None
            logger.info("Disconnected from OBS.")

    def __enter__(self) -> "OBSController":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ── Recording controls ───────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if self._client is None:
            raise RuntimeError("OBS is not connected. Call connect() first.")

    def is_recording(self) -> bool:
        self._require_connected()
        resp = self._client.get_record_status()
        return bool(resp.output_active)

    def start_recording(self) -> None:
        self._require_connected()
        if self.is_recording():
            # A leftover recording from a previous run (or manual OBS start) is
            # still active. Stop it so this clip gets a clean, bounded recording.
            logger.warning("OBS was already recording — stopping leftover recording before clip capture.")
            self._client.stop_record()
            time.sleep(0.5)
        self._client.start_record()
        logger.info("OBS recording started.")

    def stop_recording(self) -> str | None:
        """Stop recording and return the output file path, or None if not recording."""
        self._require_connected()
        if not self.is_recording():
            logger.warning("OBS is not recording — nothing to stop.")
            return None
        resp = self._client.stop_record()
        path = getattr(resp, "output_path", None)
        logger.info("OBS recording stopped. Output: %s", path)
        return path

    def set_record_directory(self, directory: str) -> None:
        self._require_connected()
        self._client.set_record_directory(recordDirectory=directory)
        logger.info("OBS recording directory set to '%s'.", directory)

    def set_scene(self, scene_name: str) -> None:
        self._require_connected()
        if not scene_name:
            return
        self._client.set_current_program_scene(scene_name)
        logger.info("OBS scene set to '%s'.", scene_name)


