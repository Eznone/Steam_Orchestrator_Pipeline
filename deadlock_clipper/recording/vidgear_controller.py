import ctypes
import datetime
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    import dxcam
    from vidgear.gears import WriteGear
    from vidgear.gears.helper import get_valid_ffmpeg_path
    _CAPTURE_DEPS_AVAILABLE = True
except ImportError:
    _CAPTURE_DEPS_AVAILABLE = False

try:
    import soundcard as sc
    _AUDIO_DEPS_AVAILABLE = True
except ImportError:
    _AUDIO_DEPS_AVAILABLE = False

try:
    from comtypes import CLSCTX_INPROC_SERVER, GUID, IUnknown, COMMETHOD, HRESULT

    class _IMMDevice(IUnknown):
        _iid_ = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
        _methods_ = [
            COMMETHOD([], HRESULT, 'Activate',
                      (['in'], ctypes.c_void_p, 'iid'),
                      (['in'], ctypes.c_ulong, 'dwClsCtx'),
                      (['in'], ctypes.c_void_p, 'pActivationParams'),
                      (['out'], ctypes.POINTER(ctypes.c_void_p), 'ppInterface')),
            COMMETHOD([], HRESULT, 'OpenPropertyStore',
                      (['in'], ctypes.c_ulong, 'stgmAccess'),
                      (['out'], ctypes.POINTER(ctypes.c_void_p), 'ppProperties')),
            COMMETHOD([], HRESULT, 'GetId',
                      (['out'], ctypes.POINTER(ctypes.c_wchar_p), 'ppstrId')),
            COMMETHOD([], HRESULT, 'GetState',
                      (['out'], ctypes.POINTER(ctypes.c_ulong), 'pdwState')),
        ]

    class _IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
        _methods_ = [
            COMMETHOD([], HRESULT, 'EnumAudioEndpoints',
                      (['in'], ctypes.c_uint, 'dataFlow'),
                      (['in'], ctypes.c_uint, 'dwStateMask'),
                      (['out'], ctypes.POINTER(ctypes.c_void_p), 'ppDevices')),
            COMMETHOD([], HRESULT, 'GetDefaultAudioEndpoint',
                      (['in'], ctypes.c_uint, 'dataFlow'),
                      (['in'], ctypes.c_uint, 'role'),
                      (['out'], ctypes.POINTER(ctypes.POINTER(_IMMDevice)), 'ppEndpoint')),
            COMMETHOD([], HRESULT, 'GetDevice',
                      (['in'], ctypes.c_wchar_p, 'pwstrId'),
                      (['out'], ctypes.POINTER(ctypes.c_void_p), 'ppDevice')),
            COMMETHOD([], HRESULT, 'RegisterEndpointNotificationCallback',
                      (['in'], ctypes.c_void_p, 'pNotify')),
            COMMETHOD([], HRESULT, 'UnregisterEndpointNotificationCallback',
                      (['in'], ctypes.c_void_p, 'pNotify')),
        ]

    _CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
    _WASAPI_AVAILABLE = True
except Exception:
    _WASAPI_AVAILABLE = False

from deadlock_clipper.recording.gpu_encoder import detect_gpu_encoder

_SOFTWARE_CODEC = "libx264"
_HARDWARE_FALLBACK_BITRATE = "12M"

# Higher-quality chroma-downsampling for the implicit BGRA → yuv420p swscale step.
# accurate_rnd reduces rounding errors in the YCbCr conversion math; full_chroma_int
# enables full-resolution chroma interpolation at intermediate steps. Both are low-cost
# precision improvements over ffmpeg's plain "bicubic" default that directly reduce
# chroma-subsampling artifacts on sharp HUD text / coloured UI edges.
_SWS_FLAGS = "bicubic+accurate_rnd+full_chroma_int"

# NVENC quality preset — p6 ("slower / better quality") gives meaningfully better
# rate-distortion than the default p4 ("medium") and still encodes well above
# real-time on any RTX-class GPU with dedicated NVENC hardware.
_NVENC_QUALITY_PRESET = "p6"

# Maps each supported codec name to the ffmpeg output flag that engages its
# quality-targeted (CRF-equivalent) rate-control mode.  All use the same 0-51
# numeric scale so self._crf (from config "crf:") works unchanged as the knob.
# NOTE: do NOT derive these from self._preset — ffmpeg accepts libx264 preset
# names (e.g. "fast") for h264_nvenc too, but they map to NVENC's unrelated
# legacy "hp"/"hq" modes, producing a silent semantic mismatch.
_QUALITY_PARAM_BY_CODEC: dict[str, str] = {
    _SOFTWARE_CODEC:  "-crf",
    "h264_nvenc":     "-cq",
    "h264_qsv":       "-global_quality",
    "h264_amf":       "-qvbr_quality_level",
}

_AUDIO_SAMPLERATE = 48000
_AUDIO_CHANNELS = 2
_AUDIO_BLOCKSIZE = 1024

# eRender=0 data-flow direction; eConsole=0, eMultimedia=1 roles.
# soundcard.default_speaker() internally uses eCommunications (role 2), which
# is the communications default and is often different from the multimedia/game
# default.  Games use eMultimedia (1); system sounds use eConsole (0).  We try
# both roles and prefer eMultimedia so the captured device matches what the game
# is actually outputting to.
_WASAPI_ROLES = (1, 0)  # eMultimedia first, eConsole as fallback


def _get_default_render_id(role: int) -> str | None:
    """Return the Windows endpoint ID for the default render device at *role*.

    Uses IMMDeviceEnumerator::GetDefaultAudioEndpoint via comtypes so we can
    query the eMultimedia role (what games use) rather than soundcard's
    eCommunications default.  Returns None if comtypes/MMDevAPI is unavailable.
    """
    if not _WASAPI_AVAILABLE:
        return None
    try:
        import comtypes
        enumerator = comtypes.CoCreateInstance(
            _CLSID_MMDeviceEnumerator, _IMMDeviceEnumerator, CLSCTX_INPROC_SERVER,
        )
        device = enumerator.GetDefaultAudioEndpoint(0, role)
        return device.GetId()
    except Exception:
        logger.debug("GetDefaultAudioEndpoint(role=%d) failed", role, exc_info=True)
        return None


def _select_loopback_device(preferred_name: str):
    """Return the loopback microphone to record from.

    Priority:
    1. *preferred_name* if explicitly set in config — used as-is.
    2. Windows default multimedia render endpoint (eMultimedia, then eConsole)
       matched to a soundcard loopback device by endpoint ID.
    3. soundcard.default_speaker() loopback as last resort.
    """
    if preferred_name:
        return sc.get_microphone(preferred_name, include_loopback=True)

    loopback_by_id = {m.id: m for m in sc.all_microphones(include_loopback=True)}

    for role in _WASAPI_ROLES:
        device_id = _get_default_render_id(role)
        if device_id and device_id in loopback_by_id:
            device = loopback_by_id[device_id]
            logger.info(
                "Audio: using default render device (role=%d) '%s'", role, device.name,
            )
            return device

    # Final fallback — soundcard's own default (usually eCommunications).
    fallback = sc.get_microphone(sc.default_speaker().id, include_loopback=True)
    logger.warning(
        "Audio: could not resolve render endpoint via Windows API — "
        "falling back to soundcard default '%s'. "
        "Set audio_device in config.yaml if this is wrong.",
        fallback.name,
    )
    return fallback


def _audio_capture_fn(
    device_name: str,
    frames: list,
    stop: threading.Event,
) -> None:
    # dxcam imports comtypes which calls CoInitializeEx(STA) in the main thread
    # before soundcard can establish MTA.  This leaves the audio thread with no
    # COM apartment, so all WASAPI calls return CO_E_NOTINITIALIZED.  Initialize
    # COM as MTA explicitly on this thread so soundcard can proceed.
    _coinit_hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # 0 = COINIT_MULTITHREADED
    _coinit_owner = (_coinit_hr == 0)  # S_OK: we init'd; S_FALSE: already init'd
    try:
        loopback = _select_loopback_device(device_name)
        logger.info("Audio capture: recording loopback from '%s'", loopback.name)
        with loopback.recorder(
            samplerate=_AUDIO_SAMPLERATE,
            channels=_AUDIO_CHANNELS,
            blocksize=_AUDIO_BLOCKSIZE,
        ) as recorder:
            while not stop.is_set():
                frames.append(recorder.record(numframes=_AUDIO_BLOCKSIZE))
    except Exception:
        logger.warning("Audio capture thread failed.", exc_info=True)
    finally:
        if _coinit_owner:
            ctypes.windll.ole32.CoUninitialize()


def _write_audio_pcm(frames: list, path: str) -> None:
    np.concatenate(frames, axis=0).astype("float32").tofile(path)


def _mux_video_audio(
    ffmpeg_bin: str,
    video_path: str,
    audio_pcm_path: str,
    out_path: str,
) -> None:
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "f32le", "-ar", str(_AUDIO_SAMPLERATE), "-ac", str(_AUDIO_CHANNELS),
        "-i", audio_pcm_path,
        "-i", video_path,
        "-map", "1:v:0", "-map", "0:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=creationflags)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))


def _try_unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.debug("Could not delete temp file: %s", path)


def _try_rename(src: str, dst: str) -> None:
    try:
        Path(src).replace(dst)
    except Exception:
        logger.warning("Could not rename %s → %s", src, dst)


def probe_gpu_encoder() -> str | None:
    """Return the name of a working hardware encoder on this machine, or None.

    Exposed at module level (rather than only via a connected controller) so
    the UI can show GPU availability — and warn the user before they even try
    to connect with "gpu" selected. Cached by detect_gpu_encoder().
    """
    if not _CAPTURE_DEPS_AVAILABLE:
        return None
    ffmpeg_path = get_valid_ffmpeg_path("", sys.platform == "win32")
    return detect_gpu_encoder(ffmpeg_path) if ffmpeg_path else None


class CaptureError(Exception):
    pass


class VidGearController:
    """Screen-capture backend using VidGear (ScreenGear + WriteGear) for the clip pipeline.

    Captures the "Deadlock" game window directly — no external recording app required.

    Usage as a context manager:
        with VidGearController.from_config(config) as ctl:
            ctl.set_record_directory(out_dir)
            ctl.start_recording()
            ...
            path = ctl.stop_recording()

    Or manual connect/disconnect:
        ctl = VidGearController(fps=60, codec="libx264")
        ctl.connect()
        ...
        ctl.disconnect()
    """

    def __init__(
        self,
        fps: int = 60,
        encoder: str = "cpu",
        crf: int = 18,
        preset: str = "fast",
        bitrate: str | None = None,
        output_width: int | None = None,
        output_height: int | None = None,
        window_title: str = "Deadlock",
        audio_capture: bool = False,
        audio_device: str = "",
    ):
        if not _CAPTURE_DEPS_AVAILABLE:
            raise ImportError("vidgear/dxcam are not installed. Run: uv add vidgear dxcam opencv-python")
        self._fps = fps
        self._encoder = encoder
        self._codec: str | None = None  # resolved in connect() — depends on a GPU probe for "gpu"
        self._crf = crf
        self._preset = preset
        self._bitrate = bitrate
        self._output_width = output_width
        self._output_height = output_height
        self._window_title = window_title
        self._audio_capture = audio_capture
        self._audio_device = audio_device

        self._record_dir = ""
        self._connected = False

        # Capture-loop lifecycle state — guarded by _state_lock
        self._state_lock = threading.Lock()
        self._capture_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._first_frame_event = threading.Event()
        self._recording = False
        self._thread_exc: BaseException | None = None
        self._output_path: str | None = None

    @classmethod
    def from_config(cls, config: dict) -> "VidGearController":
        rec = config.get("recording", {})
        return cls(
            fps=int(rec.get("fps", 60)),
            encoder=rec.get("encoder", "cpu"),
            crf=int(rec.get("crf", 18)),
            preset=rec.get("preset", "fast"),
            bitrate=rec.get("bitrate") or None,
            output_width=int(rec.get("output_width", 0)) or None,
            output_height=int(rec.get("output_height", 0)) or None,
            audio_capture=bool(rec.get("audio_capture", False)),
            audio_device=rec.get("audio_device", ""),
        )

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify the Deadlock window is reachable and resolve the encoder. Raises CaptureError if either fails.

        No persistent capture resources are opened here — ScreenGear/WriteGear
        are created fresh for each recording so GPU handles aren't held between clips.
        """
        from deadlock_clipper.recording.client_launcher import find_window_hwnd

        hwnd = find_window_hwnd(self._window_title)
        if hwnd is None:
            raise CaptureError(
                f"'{self._window_title}' window not found — "
                "make sure the game is running before connecting."
            )
        self._codec = self._resolve_codec()
        self._connected = True
        logger.info(
            "Capture backend ready — found '%s' window (hwnd=%s), encoding with '%s'.",
            self._window_title, hwnd, self._codec,
        )

    def _resolve_codec(self) -> str:
        """Translate the user's "cpu"/"gpu" choice into a concrete, working FFmpeg codec.

        "gpu" requires actually probing for a hardware encoder that works on this
        machine — the encoder being compiled into FFmpeg doesn't mean the matching
        GPU/driver is present (see gpu_encoder.detect_gpu_encoder).
        """
        if self._encoder != "gpu":
            return _SOFTWARE_CODEC

        gpu_codec = probe_gpu_encoder()
        if gpu_codec is None:
            raise CaptureError("No working GPU encoder detected on this system — switch to CPU encoding.")
        return gpu_codec

    def disconnect(self) -> None:
        if self._recording:
            logger.warning("Disconnecting while recording — stopping capture first.")
            self.stop_recording()
        self._connected = False
        logger.info("Capture backend disconnected.")

    def __enter__(self) -> "VidGearController":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ── Recording controls ───────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Capture backend is not connected. Call connect() first.")

    def is_recording(self) -> bool:
        with self._state_lock:
            return self._recording

    def set_record_directory(self, directory: str) -> None:
        self._record_dir = directory
        logger.info("Capture output directory set to '%s'.", directory)

    def start_recording(self) -> None:
        self._require_connected()
        if self._capture_thread is not None:
            logger.warning("Leftover capture thread found — stopping it before starting a new one.")
            self.stop_recording()

        region = self._resolve_window_region()
        out_path = self._make_output_path()

        self._stop_event.clear()
        self._first_frame_event.clear()
        self._thread_exc = None
        self._output_path = None

        thread = threading.Thread(
            target=self._capture_loop, args=(region, out_path), name="vidgear-capture", daemon=True,
        )
        with self._state_lock:
            self._recording = True
        self._capture_thread = thread
        thread.start()
        logger.info("Capture started (region=%s) → %s", region, out_path)
        if not self._first_frame_event.wait(timeout=5.0):
            logger.warning("First frame not delivered within 5s — encoding may have started late.")

    def stop_recording(self) -> str | None:
        """Stop recording and return the output file path, or None if nothing was running.

        Joins whatever thread was last started regardless of the current
        `_recording` flag — `_capture_loop` flips that flag itself on its way
        out (including on crash), so gating on it here would let a crashed
        loop's exception go unjoined and silently discarded.
        """
        thread = self._capture_thread
        if thread is None:
            logger.warning("Not recording — nothing to stop.")
            return None

        self._stop_event.set()
        thread.join(timeout=30)  # WriteGear.close() needs time to flush/finalise the file
        self._capture_thread = None

        with self._state_lock:
            self._recording = False
            exc, self._thread_exc = self._thread_exc, None
            path, self._output_path = self._output_path, None

        if exc is not None:
            raise RuntimeError(f"Capture failed: {exc}") from exc

        logger.info("Capture stopped. Output: %s", path)
        return path

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _resolve_window_region(self) -> tuple[int, int, int, int]:
        """Snapshot the game window's screen rectangle for this recording.

        Resolved once per recording (not per-frame): clips are short and the
        automation holds foreground throughout, so the window won't move/resize
        mid-clip — and a per-frame lookup would just add syscalls with no benefit.
        """
        from deadlock_clipper.recording.client_launcher import find_window_hwnd, get_window_rect

        hwnd = find_window_hwnd(self._window_title)
        if hwnd is None:
            raise CaptureError(f"'{self._window_title}' window not found — is the game still running?")

        rect = get_window_rect(hwnd)
        if rect is None:
            raise CaptureError(f"Could not read the '{self._window_title}' window's screen position (is it minimised?).")
        return rect

    def _make_output_path(self) -> str:
        directory = Path(self._record_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return str(directory / f"clip_{timestamp}.mp4")

    def _build_output_params(self) -> dict:
        params: dict = {
            "-input_framerate": self._fps,
            "-c:v": self._codec,
            # Force 4:2:0 chroma subsampling on the encoded output. Without this,
            # FFmpeg converts our raw bgr24 frames to yuv444p (full chroma) for
            # libx264, producing a "High 4:4:4 Predictive" stream that Windows'
            # built-in Media Foundation H.264 decoder refuses to play ("unsupported
            # encoding settings") even though VLC/ffplay handle it fine. yuv420p is
            # the universally-compatible choice (Windows, mobile, web, QuickTime).
            "-pix_fmt": "yuv420p",
            # Improve chroma-downsampling quality in the auto-inserted swscale filter
            # that converts BGRA frames to yuv420p. accurate_rnd and full_chroma_int
            # reduce rounding/interpolation artifacts on sharp HUD/text edges compared
            # to ffmpeg's plain bicubic default (confirmed via -loglevel debug:
            # "[auto_scale_0] Setting 'sws_flags' to value '...'").
            "-sws_flags": _SWS_FLAGS,
            # Signal BT.709 color space in the H.264 VUI — prevents players from
            # applying a spurious matrix conversion (e.g. BT.601) when the stream
            # lacks explicit colorspace metadata. DXGI BGRA desktop capture is
            # sRGB/BT.709; these flags just make that explicit in the container.
            "-colorspace": "bt709",
            "-color_trc": "bt709",
            "-color_primaries": "bt709",
            # No audio source in this pipeline. -clones passes standalone flags
            # through WriteGear's dict2Args without appending a spurious value
            # (dict2Args always calls str() on values, so None/"" don't work).
            "-clones": ["-an"],
        }
        if self._codec == _SOFTWARE_CODEC:
            params["-preset"] = self._preset
            if self._bitrate:
                params["-b:v"] = self._bitrate
            else:
                params["-crf"] = self._crf

        elif self._codec == "h264_nvenc":
            # VBR + CQ engages NVENC's constant-quality mode (analogous to libx264's
            # CRF). Validated on RTX 5070 Ti: ffmpeg log shows "CQ(N*256) mode enabled"
            # with these params.  -b:v 0 lets the quality target run unconstrained by
            # a target average bitrate.  Spatial/temporal AQ steer more bits toward
            # visually complex regions (HUD, fine detail) within each frame.
            # _NVENC_QUALITY_PRESET (p6) is intentionally NOT self._preset — ffmpeg
            # accepts libx264 preset names for nvenc but they silently map to its
            # unrelated legacy "hp/hq" modes.
            params["-rc:v"] = "vbr"
            params["-preset"] = _NVENC_QUALITY_PRESET
            params["-spatial-aq"] = "1"
            params["-temporal-aq"] = "1"
            if self._bitrate:
                params["-b:v"] = self._bitrate
            else:
                params["-cq"] = self._crf
                params["-b:v"] = "0"

        elif self._codec == "h264_qsv":
            # -global_quality is a generic libavcodec AVOption that h264_qsv's
            # rate-control logic maps to ICQ (Intelligent Constant Quality) / LA-ICQ
            # when no explicit bitrate is set.  -look_ahead enables lookahead-assisted
            # ICQ for better bit distribution across frames.
            params["-look_ahead"] = "1"
            if self._bitrate:
                params["-b:v"] = self._bitrate
            else:
                params["-global_quality"] = self._crf

        elif self._codec == "h264_amf":
            # QVBR (Quality Variable Bitrate) is AMF's CRF-equivalent single knob;
            # -quality 2 switches AMF's internal mode-selection from "speed" (default)
            # to "quality" — the AMF SDK analogue of NVENC's p-preset.
            params["-quality"] = "2"
            if self._bitrate:
                params["-b:v"] = self._bitrate
            else:
                params["-rc:v"] = "qvbr"
                params["-qvbr_quality_level"] = self._crf

        else:
            # Unknown or future hardware codec: fall back to explicit bitrate, which
            # every H.264 encoder accepts as a universal rate-control knob.
            params["-b:v"] = self._bitrate or _HARDWARE_FALLBACK_BITRATE

        if self._output_width and self._output_height:
            params["-output_dimensions"] = (self._output_width, self._output_height)
        return params

    def _capture_loop(self, region: tuple[int, int, int, int], out_path: str) -> None:
        """Background thread: pull frames from dxcam directly and encode via WriteGear.

        Drives `dxcam` ourselves rather than going through VidGear's `ScreenGear`
        wrapper — that wrapper passes our (left, top, width, height) region straight
        through as dxcam's (left, top, right, bottom) tuple (wrong for any window not
        anchored at the screen origin), starts its reader thread before dxcam's
        capture is running (a startup race that crashes on the first frame), and its
        crash handler references `ScreenShotError`, which is only defined when `mss`
        is installed (we don't depend on it) — turning that crash into a bare
        `NameError`. None of that is in play here.

        Runs until _stop_event is set. Stores the result (output path) or any
        exception on self so stop_recording() can surface it to the caller.
        """
        left, top, width, height = region
        dxcam_region = (left, top, left + width, top + height)  # dxcam.Region is (left, top, right, bottom)
        camera = None
        writer = None
        exc: BaseException | None = None
        total_frames = 0
        unique_frames = 0
        first_frame_ts: float | None = None
        loop_start = time.perf_counter()
        # Audio capture state
        audio_thread: threading.Thread | None = None
        audio_frames: list = []
        audio_stop = threading.Event()
        # When audio is enabled, WriteGear writes to a temp file; the final mux
        # produces out_path. When disabled, WriteGear writes directly to out_path.
        video_write_path = out_path.replace(".mp4", "_video.mp4") if self._audio_capture else out_path
        audio_pcm_path = out_path.replace(".mp4", "_audio.pcm")
        try:
            # BGRA is the *native* DXGI surface format — requesting it lets dxcam skip its
            # internal cv2.cvtColor step entirely (cv2_processor only converts when asked
            # for something other than BGRA). WriteGear inspects frame.shape[-1] and
            # auto-derives -pix_fmt bgra for 4-channel input, so no output-params change
            # is needed; FFmpeg's swscale still does bgra → yuv420p exactly as before.
            camera = dxcam.create(output_color="BGRA")
            camera.start(region=dxcam_region, target_fps=self._fps, video_mode=True)
            # WriteGear collects FFmpeg params via **kwargs (not an `output_params=` dict
            # argument) — passing a dict directly would be absorbed as a single literal
            # "output_params" key, producing a malformed FFmpeg command line that exits
            # immediately and breaks the stdin pipe on the first frame write.
            writer = WriteGear(output=video_write_path, compression_mode=True, **self._build_output_params())

            prev_ts: float | None = None
            while not self._stop_event.is_set():
                # with_timestamp=True: dxcam returns the DXGI presentation timestamp
                # (QueryPerformanceCounter ticks / frequency → seconds) stored when the
                # frame was captured.  Duplicate frames — produced by dxcam's video_mode
                # when the source doesn't deliver a new frame within a timer tick — carry
                # the identical timestamp as their predecessor (capture_runtime.py:83:
                # frame_ticks = int(self.frame_time_ticks[previous_idx])).  Comparing
                # consecutive timestamps is therefore a ground-truth duplicate detector.
                # copy=False zero-copy guarantee is unaffected by with_timestamp=True.
                result = camera.get_latest_frame(copy=False, with_timestamp=True)
                if result is None:
                    break  # camera was stopped out from under us
                frame, ts = result
                total_frames += 1
                if prev_ts is None or ts != prev_ts:
                    unique_frames += 1
                prev_ts = ts
                writer.write(frame)
                if total_frames == 1:
                    first_frame_ts = time.perf_counter()
                    self._first_frame_event.set()
                    if self._audio_capture:
                        if not _AUDIO_DEPS_AVAILABLE:
                            logger.warning(
                                "soundcard is not installed — audio capture skipped. "
                                "Run: uv add soundcard"
                            )
                        else:
                            audio_stop.clear()
                            audio_thread = threading.Thread(
                                target=_audio_capture_fn,
                                args=(self._audio_device, audio_frames, audio_stop),
                                name="audio-capture",
                                daemon=True,
                            )
                            audio_thread.start()

        except Exception as caught:
            self._first_frame_event.set()  # unblock start_recording() on early failure
            logger.exception("Capture loop failed: %s", caught)
            exc = caught
        finally:
            if first_frame_ts is not None:
                capture_elapsed = time.perf_counter() - first_frame_ts
                dup_pct = 100.0 * (1 - unique_frames / total_frames) if total_frames > 0 else 0.0
                effective_fps = unique_frames / capture_elapsed if capture_elapsed > 0 else 0.0
                logger.info(
                    "Init overhead: %.2fs | Clip stats: %d total / %d unique frames "
                    "(%.1f%% duplicates) — effective %.1f fps (target %d fps, %.1fs capture)",
                    first_frame_ts - loop_start,
                    total_frames, unique_frames, dup_pct, effective_fps, self._fps, capture_elapsed,
                )
            # Stop audio before flushing the video encoder — both streams need to
            # finish before the mux step can run.
            audio_stop.set()
            if audio_thread is not None:
                audio_thread.join(timeout=5.0)
            if camera is not None:
                try:
                    camera.stop()
                except Exception:
                    logger.warning("Error stopping dxcam capture.", exc_info=True)
                try:
                    # Releases the cached DXCamera instance so dxcam.create() hands
                    # back a fresh camera (with the new region) on the next recording
                    # instead of a stale, still-"capturing" cached one.
                    camera.release()
                except Exception:
                    logger.warning("Error releasing dxcam camera.", exc_info=True)
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    logger.warning("Error closing WriteGear.", exc_info=True)
            # Mux audio into the video file when audio was successfully captured.
            if self._audio_capture and audio_frames and exc is None:
                try:
                    ffmpeg_bin = get_valid_ffmpeg_path("", sys.platform == "win32")
                    _write_audio_pcm(audio_frames, audio_pcm_path)
                    _mux_video_audio(ffmpeg_bin, video_write_path, audio_pcm_path, out_path)
                    _try_unlink(video_write_path)
                    _try_unlink(audio_pcm_path)
                    logger.info("Audio mux complete → %s", out_path)
                except Exception as mux_exc:
                    logger.warning("Audio mux failed (%s) — keeping video-only file.", mux_exc)
                    _try_unlink(audio_pcm_path)
                    if video_write_path != out_path:
                        _try_rename(video_write_path, out_path)
            elif video_write_path != out_path:
                # audio_capture was True but nothing to mux (no frames or encode error)
                _try_rename(video_write_path, out_path)

            with self._state_lock:
                self._recording = False
                if exc is not None:
                    self._thread_exc = exc
                else:
                    self._output_path = out_path
