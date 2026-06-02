import logging

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
            except Exception:
                pass
            self._client = None
            logger.info("Disconnected from OBS.")

    def __enter__(self) -> "OBSController":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ── Recording controls ───────────────────────────────────────────────────

    def is_recording(self) -> bool:
        resp = self._client.get_record_status()
        return bool(resp.output_active)

    def start_recording(self) -> None:
        if self.is_recording():
            logger.warning("OBS is already recording — skipping start.")
            return
        self._client.start_record()
        logger.info("OBS recording started.")

    def stop_recording(self) -> str | None:
        """Stop recording and return the output file path, or None if not recording."""
        if not self.is_recording():
            logger.warning("OBS is not recording — nothing to stop.")
            return None
        resp = self._client.stop_record()
        path = getattr(resp, "output_path", None)
        logger.info("OBS recording stopped. Output: %s", path)
        return path

    def set_scene(self, scene_name: str) -> None:
        if not scene_name:
            return
        self._client.set_current_program_scene(scene_name)
        logger.info("OBS scene set to '%s'.", scene_name)


# ── Standalone connection test ───────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import logging
    from config import load_config

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config()

    print("Testing OBS connection...")
    try:
        with OBSController.from_config(config) as ctl:
            recording = ctl.is_recording()
            print(f"Connected. Currently recording: {recording}")

            if "--record" in sys.argv:
                print("Starting 3-second test recording...")
                import time
                ctl.start_recording()
                time.sleep(3)
                path = ctl.stop_recording()
                print(f"Saved to: {path}")
    except OBSConnectionError as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)
