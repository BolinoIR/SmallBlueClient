"""Local live transcription powered by the optional ``faster-whisper`` extra."""
from __future__ import annotations

import json
import tempfile
import threading
import wave
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Iterable

from ..audio import AudioFrame
from ..types import TranscriptionModel, enum_value


class TranscriptionUnavailableError(RuntimeError):
    """Raised when the optional local transcription runtime is unavailable."""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One timestamped transcription result from a BBB audio track."""

    text: str
    start: float
    end: float
    user_id: str | None = None
    user_name: str | None = None
    language: str | None = None
    probability: float | None = None
    mixed: bool = False
    final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    separator = "." if vtt else ","
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


class LiveTranscription:
    """Background transcriber attached to :attr:`client.audio`.

    Audio is grouped into short independent WAV chunks.  This keeps memory
    bounded and gives a useful live transcript even when a BBB media stream is
    long-lived or reconnects.  Call :meth:`flush` before :meth:`stop` to force
    the final partial chunk through the model.
    """

    def __init__(
        self,
        controller: "TranscriptionController",
        *,
        model: TranscriptionModel | str = TranscriptionModel.BASE,
        language: str | None = None,
        chunk_seconds: float = 5.0,
        users: Iterable[str] | None = None,
        device: str = "auto",
        compute_type: str = "default",
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        if chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be positive")
        self.controller = controller
        self.model_name = enum_value(model)
        self.language = language
        self.chunk_seconds = chunk_seconds
        self.user_ids = set(users or ())
        self.device = device
        self.compute_type = compute_type
        self.engine_factory = engine_factory
        self._engine: Any | None = None
        self._buffers: dict[str, list[AudioFrame]] = defaultdict(list)
        self._durations: dict[str, float] = defaultdict(float)
        self._jobs: Queue[tuple[str, list[AudioFrame]] | None] = Queue()
        self._segments: list[TranscriptSegment] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def segments(self) -> tuple[TranscriptSegment, ...]:
        with self._lock:
            return tuple(self._segments)

    def start(self) -> "LiveTranscription":
        if self._active:
            return self
        self._engine = self._load_engine()
        self._active = True
        self._stop.clear()
        self.controller.audio.add_listener(self._receive)
        self._thread = threading.Thread(target=self._run, name="sbc-transcription", daemon=True)
        self._thread.start()
        return self

    def _load_engine(self) -> Any:
        if self.engine_factory is not None:
            return self.engine_factory(self.model_name, device=self.device, compute_type=self.compute_type)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionUnavailableError(
                "local transcription needs the optional dependency: pip install SmallBlueClient[transcription]"
            ) from exc
        return WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def _key(self, frame: AudioFrame) -> str:
        return frame.user_id or ("conference-mix" if frame.mixed else "unknown-user")

    def _receive(self, frame: AudioFrame) -> None:
        if not self._active or (self.user_ids and frame.user_id not in self.user_ids):
            return
        key = self._key(frame)
        with self._lock:
            self._buffers[key].append(frame)
            self._durations[key] += frame.duration
            if self._durations[key] >= self.chunk_seconds:
                payload = self._buffers.pop(key)
                self._durations.pop(key, None)
                self._jobs.put((key, payload))

    def flush(self) -> None:
        """Queue buffered partial audio immediately."""
        with self._lock:
            for key, frames in tuple(self._buffers.items()):
                if frames:
                    self._jobs.put((key, frames))
            self._buffers.clear()
            self._durations.clear()

    @staticmethod
    def _write_wav(frames: list[AudioFrame]) -> Path:
        first = frames[0]
        if any((item.sample_rate, item.channels) != (first.sample_rate, first.channels) for item in frames):
            frames = [item for item in frames if (item.sample_rate, item.channels) == (first.sample_rate, first.channels)]
        handle = tempfile.NamedTemporaryFile(prefix="sbc-transcript-", suffix=".wav", delete=False)
        handle.close()
        path = Path(handle.name)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(first.channels)
            output.setsampwidth(2)
            output.setframerate(first.sample_rate)
            for frame in frames:
                output.writeframesraw(frame.pcm)
        return path

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                return
            _, frames = job
            if not frames:
                continue
            path = self._write_wav(frames)
            try:
                result = self._engine.transcribe(str(path), language=self.language, vad_filter=True)
                source_segments, info = result
                base = frames[0].timestamp
                for item in source_segments:
                    text = str(getattr(item, "text", "")).strip()
                    if not text:
                        continue
                    probability = getattr(item, "avg_logprob", None)
                    segment = TranscriptSegment(
                        text=text, start=base + float(getattr(item, "start", 0.0)),
                        end=base + float(getattr(item, "end", 0.0)),
                        user_id=frames[0].user_id, user_name=frames[0].user_name,
                        language=getattr(info, "language", self.language), probability=probability,
                        mixed=frames[0].mixed,
                    )
                    with self._lock:
                        self._segments.append(segment)
                    self.controller._emit(segment)
            except Exception as exc:
                self.controller.client.emit("error", exc)
            finally:
                path.unlink(missing_ok=True)

    def stop(self, *, flush: bool = True) -> tuple[TranscriptSegment, ...]:
        if not self._active:
            return self.segments
        if flush:
            self.flush()
        self._active = False
        self.controller.audio.remove_listener(self._receive)
        # Work queued before the sentinel remains in FIFO order.
        self._jobs.put(None)
        if self._thread:
            self._thread.join(timeout=30)
        self._thread = None
        return self.segments

    def export(self, destination: str | Path, *, format: str = "srt") -> Path:
        return self.controller.export(destination, format=format, segments=self.segments)

    def __enter__(self) -> "LiveTranscription":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


class TranscriptionController:
    """Create local live transcripts from :attr:`SBCClient.audio`."""

    def __init__(self, client: Any, audio: Any) -> None:
        self.client = client
        self.audio = audio
        self._sessions: list[LiveTranscription] = []

    def start(
        self,
        *,
        model: TranscriptionModel | str = TranscriptionModel.BASE,
        language: str | None = None,
        chunk_seconds: float = 5.0,
        users: Iterable[str] | None = None,
        device: str = "auto",
        compute_type: str = "default",
    ) -> LiveTranscription:
        """Start a local faster-whisper worker and return its session handle."""
        self.audio.start()
        session = LiveTranscription(
            self, model=model, language=language, chunk_seconds=chunk_seconds,
            users=users, device=device, compute_type=compute_type,
        ).start()
        self._sessions.append(session)
        return session

    def _emit(self, segment: TranscriptSegment) -> None:
        self.client.emit("transcript_segment", segment)
        self.client.emit("transcript_final", segment)

    def export(
        self,
        destination: str | Path,
        *,
        format: str = "srt",
        segments: Iterable[TranscriptSegment] = (),
    ) -> Path:
        """Export transcript segments as ``srt``, ``vtt``, ``txt``, or ``json``."""
        path = Path(destination)
        format = format.lower().lstrip(".")
        values = list(segments)
        if format == "json":
            path.write_text(json.dumps([item.to_dict() for item in values], indent=2, ensure_ascii=False), encoding="utf-8")
        elif format == "txt":
            path.write_text("\n".join(
                f"[{_format_timestamp(item.start)}] {item.user_name or item.user_id or 'Speaker'}: {item.text}"
                for item in values
            ) + ("\n" if values else ""), encoding="utf-8")
        elif format in {"srt", "vtt"}:
            vtt = format == "vtt"
            lines = ["WEBVTT", ""] if vtt else []
            for index, item in enumerate(values, 1):
                lines.extend([
                    str(index),
                    f"{_format_timestamp(item.start, vtt=vtt)} --> {_format_timestamp(item.end, vtt=vtt)}",
                    f"{item.user_name or item.user_id or 'Speaker'}: {item.text}",
                    "",
                ])
            path.write_text("\n".join(lines), encoding="utf-8")
        else:
            raise ValueError("transcript format must be srt, vtt, txt, or json")
        return path

    def close(self) -> None:
        for session in tuple(self._sessions):
            session.stop()
        self._sessions.clear()


__all__ = [
    "LiveTranscription", "TranscriptSegment", "TranscriptionController", "TranscriptionUnavailableError",
]
