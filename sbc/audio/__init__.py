"""Incoming BBB audio capture, per-track recording, and live frame access.

The capture layer is deliberately transport-neutral.  BBB's media backend
adapters feed decoded frames into :class:`AudioController`; applications can
also feed frames themselves when using an installation-specific media bridge.
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import time
import wave
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A decoded signed-16-bit PCM audio frame from a BBB media track."""

    pcm: bytes
    sample_rate: int
    channels: int
    user_id: str | None = None
    user_name: str | None = None
    timestamp: float = 0.0
    mixed: bool = False
    source: str = "bbb-webrtc-sfu"

    @property
    def duration(self) -> float:
        return len(self.pcm) / max(1, self.sample_rate * self.channels * 2)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pcm_bytes"] = len(self.pcm)
        data.pop("pcm")
        return data


@dataclass(frozen=True, slots=True)
class AudioTrackInfo:
    """Metadata for an incoming audio track exposed by a BBB backend."""

    user_id: str | None
    user_name: str | None
    mixed: bool
    source: str
    sample_rate: int = 48_000
    channels: int = 1
    frames_received: int = 0
    seconds_received: float = 0.0


def _safe_name(value: str | None, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip(".-")
    return text[:80] or fallback


class AudioRecorder:
    """Write incoming tracks to one WAV file per participant by default.

    ``format='wav'`` is dependency-free and lossless.  Other formats are
    encoded through PyAV when their codec is available in the local FFmpeg
    build.  A ``manifest.json`` is always written beside the recordings.
    """

    def __init__(
        self,
        controller: "AudioController",
        destination: str | Path,
        *,
        format: str = "wav",
        separate_tracks: bool = True,
    ) -> None:
        self.controller = controller
        self.destination = Path(destination)
        self.format = format.lower().lstrip(".")
        self.separate_tracks = separate_tracks
        self._writers: dict[str, wave.Wave_write] = {}
        self._paths: dict[str, Path] = {}
        self._staging_paths: dict[str, Path] = {}
        self._parameters: dict[str, tuple[int, int]] = {}
        self._frames: dict[str, int] = defaultdict(int)
        self._seconds: dict[str, float] = defaultdict(float)
        self._lock = threading.RLock()
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def paths(self) -> dict[str, Path]:
        return dict(self._paths)

    def start(self) -> "AudioRecorder":
        if self._active:
            return self
        if self.format not in {"wav", "wave", "mp3", "flac", "ogg", "opus"}:
            raise ValueError("audio format must be wav, mp3, flac, ogg, or opus")
        self.destination.mkdir(parents=True, exist_ok=True)
        self._active = True
        self.controller.add_listener(self._write)
        return self

    def _key(self, frame: AudioFrame) -> str:
        if not self.separate_tracks:
            return "conference-mix"
        return frame.user_id or ("conference-mix" if frame.mixed else "unknown-user")

    def _open(self, key: str, frame: AudioFrame) -> wave.Wave_write:
        if key in self._writers:
            return self._writers[key]
        label = _safe_name(frame.user_name, key)
        extension = "wav" if self.format == "wave" else self.format
        path = self.destination / f"{label}-{_safe_name(key, 'track')}.{extension}"
        staging = path if extension == "wav" else self.destination / f".{path.stem}.capture.wav"
        writer = wave.open(str(staging), "wb")
        writer.setnchannels(frame.channels)
        writer.setsampwidth(2)
        writer.setframerate(frame.sample_rate)
        self._writers[key] = writer
        self._paths[key] = path
        self._staging_paths[key] = staging
        self._parameters[key] = (frame.sample_rate, frame.channels)
        return writer

    def _write(self, frame: AudioFrame) -> None:
        if not self._active:
            return
        key = self._key(frame)
        with self._lock:
            parameters = self._parameters.get(key)
            if parameters and parameters != (frame.sample_rate, frame.channels):
                # WAV cannot change its stream format midway.  This can happen
                # after an SFU renegotiation, so put the new segment in its own
                # track instead of corrupting the first file.
                key = f"{key}-segment-{len(self._writers) + 1}"
            writer = self._open(key, frame)
            writer.writeframesraw(frame.pcm)
            self._frames[key] += 1
            self._seconds[key] += frame.duration

    @staticmethod
    def _transcode(source: Path, destination: Path, format: str) -> None:
        """Encode a completed WAV with the FFmpeg codecs shipped by PyAV."""
        import av

        codec = {"mp3": "mp3", "flac": "flac", "ogg": "libopus", "opus": "libopus"}[format]
        input_container = None
        output_container = None
        try:
            input_container = av.open(str(source))
            input_stream = next(stream for stream in input_container.streams if stream.type == "audio")
            output_container = av.open(str(destination), mode="w", format="ogg" if format == "opus" else format)
            try:
                # Fixed-rate planar float PCM is accepted by every lossy codec
                # SBC exposes, including libmp3lame on Windows builds.
                output_stream = output_container.add_stream(codec, rate=48_000)
            except Exception:
                if codec != "libopus":
                    raise
                output_stream = output_container.add_stream("opus", rate=48_000)
            output_stream.layout = "mono"
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=48_000)
            for frame in input_container.decode(input_stream):
                for resampled in resampler.resample(frame):
                    for packet in output_stream.encode(resampled):
                        output_container.mux(packet)
            for resampled in resampler.resample(None):
                for packet in output_stream.encode(resampled):
                    output_container.mux(packet)
            for packet in output_stream.encode(None):
                output_container.mux(packet)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            if output_container is not None:
                output_container.close()
            if input_container is not None:
                input_container.close()

    def stop(self) -> dict[str, Path]:
        if not self._active:
            return self.paths
        self._active = False
        self.controller.remove_listener(self._write)
        with self._lock:
            for writer in self._writers.values():
                writer.close()
            try:
                for key, source in self._staging_paths.items():
                    destination = self._paths[key]
                    if source != destination:
                        self._transcode(source, destination, self.format)
                        source.unlink(missing_ok=True)
            finally:
                # On an unavailable local codec, don't leave hidden staging
                # recordings locked on Windows. The visible output is removed
                # by ``_transcode`` and the caller receives the codec error.
                for key, source in self._staging_paths.items():
                    if source != self._paths[key]:
                        source.unlink(missing_ok=True)
            manifest = {
                "format": "wav" if self.format == "wave" else self.format,
                "separate_tracks": self.separate_tracks,
                "created_at": time.time(),
                "tracks": [
                    {
                        "key": key,
                        "file": path.name,
                        "frames": self._frames[key],
                        "seconds": round(self._seconds[key], 3),
                        "sample_rate": self._parameters[key][0],
                        "channels": self._parameters[key][1],
                    }
                    for key, path in self._paths.items()
                ],
            }
            (self.destination / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return self.paths

    def __enter__(self) -> "AudioRecorder":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


class AudioController:
    """Receive decoded BBB audio as live frames and recordings.

    On ``bbb-webrtc-sfu`` the normal listener endpoint supplies a conference
    mix, therefore it is exposed as ``mixed=True`` and cannot be truthfully
    split into individual speakers.  Backends which provide participant
    tracks (for example a LiveKit room) can call :meth:`ingest` with a user id
    and automatically receive independent per-user recordings.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self._listeners: list[Callable[[AudioFrame], None]] = []
        self._tracks: dict[str, AudioTrackInfo] = {}
        self._lock = threading.RLock()

    def add_listener(self, listener: Callable[[AudioFrame], None]) -> Callable[[AudioFrame], None]:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
        return listener

    def remove_listener(self, listener: Callable[[AudioFrame], None]) -> None:
        with self._lock:
            with __import__("contextlib").suppress(ValueError):
                self._listeners.remove(listener)

    def tracks(self) -> list[AudioTrackInfo]:
        with self._lock:
            return list(self._tracks.values())

    def record(
        self,
        destination: str | Path,
        *,
        format: str = "wav",
        separate_tracks: bool = True,
    ) -> AudioRecorder:
        """Start a recording and return its handle.

        Separate participant files are the default.  Use
        ``separate_tracks=False`` to persist one conference mix.
        """
        return AudioRecorder(self, destination, format=format, separate_tracks=separate_tracks).start()

    def ingest(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
        user_id: str | None = None,
        user_name: str | None = None,
        mixed: bool = False,
        source: str = "bbb-webrtc-sfu",
        timestamp: float | None = None,
    ) -> AudioFrame:
        """Feed a decoded signed-16-bit PCM frame from a media backend."""
        if sample_rate <= 0 or channels <= 0:
            raise ValueError("sample_rate and channels must be positive")
        if len(pcm) % (channels * 2):
            raise ValueError("PCM must contain complete signed-16-bit samples")
        frame = AudioFrame(
            pcm=bytes(pcm), sample_rate=sample_rate, channels=channels,
            user_id=user_id, user_name=user_name, mixed=mixed, source=source,
            timestamp=time.time() if timestamp is None else timestamp,
        )
        key = user_id or ("conference-mix" if mixed else "unknown-user")
        with self._lock:
            old = self._tracks.get(key)
            self._tracks[key] = AudioTrackInfo(
                user_id=user_id, user_name=user_name or (old.user_name if old else None),
                mixed=mixed, source=source, sample_rate=sample_rate, channels=channels,
                frames_received=(old.frames_received if old else 0) + 1,
                seconds_received=(old.seconds_received if old else 0.0) + frame.duration,
            )
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(frame)
            except Exception as exc:
                self.client.emit("error", exc)
        self.client.emit("audio_frame", frame)
        return frame

    def ingest_av_frame(
        self,
        frame: Any,
        *,
        user_id: str | None = None,
        user_name: str | None = None,
        mixed: bool = False,
        source: str = "bbb-webrtc-sfu",
    ) -> AudioFrame:
        """Convert a PyAV/aiortc audio frame and ingest it.

        This is used by SBC's native receive adapters and is public for custom
        BBB media integrations.
        """
        import numpy as np

        data = frame.to_ndarray()
        channels = len(frame.layout.channels) if getattr(frame, "layout", None) else 1
        # PyAV usually supplies shape=(channels, samples).  Convert it to the
        # packed interleaved layout expected by WAV and Whisper.
        if getattr(data, "ndim", 1) == 2 and data.shape[0] == channels:
            data = data.T.reshape(-1)
        data = np.asarray(data, dtype=np.int16)
        return self.ingest(
            data.tobytes(), sample_rate=int(frame.sample_rate or 48_000), channels=channels,
            user_id=user_id, user_name=user_name, mixed=mixed, source=source,
            timestamp=time.time(),
        )

    def frame_stream(self, *, maxsize: int = 256) -> Iterator[AudioFrame]:
        """Yield future frames synchronously until the iterator is closed."""
        frames: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=maxsize)

        def push(frame: AudioFrame) -> None:
            try:
                frames.put_nowait(frame)
            except queue.Full:
                with __import__("contextlib").suppress(queue.Empty):
                    frames.get_nowait()
                with __import__("contextlib").suppress(queue.Full):
                    frames.put_nowait(frame)

        self.add_listener(push)
        try:
            while True:
                item = frames.get()
                if item is None:
                    return
                yield item
        finally:
            self.remove_listener(push)

    async def frames(self, *, maxsize: int = 256):
        """Asynchronously iterate over future decoded audio frames."""
        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=maxsize)

        def push(frame: AudioFrame) -> None:
            def put() -> None:
                if frames.full():
                    with __import__("contextlib").suppress(asyncio.QueueEmpty):
                        frames.get_nowait()
                with __import__("contextlib").suppress(asyncio.QueueFull):
                    frames.put_nowait(frame)
            loop.call_soon_threadsafe(put)

        self.add_listener(push)
        try:
            while True:
                yield await frames.get()
        finally:
            self.remove_listener(push)


__all__ = ["AudioController", "AudioFrame", "AudioRecorder", "AudioTrackInfo"]
