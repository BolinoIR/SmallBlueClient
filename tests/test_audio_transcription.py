"""Unit coverage for incoming audio capture and local transcription plumbing."""
from __future__ import annotations

import json
import asyncio
import tempfile
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sbc.audio import AudioController
from sbc.transcription import LiveTranscription, TranscriptSegment, TranscriptionController
from sbc.asyncio import _AsyncAudioController


class _Client:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, event: str, *args: object) -> None:
        self.events.append((event, args[0] if args else None))


class _MediaClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.starts = 0
        self.media = SimpleNamespace(start_audio_capture=self._start_audio_capture)

    def _start_audio_capture(self) -> None:
        self.starts += 1


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text, self.avg_logprob = start, end, text, -0.2


class _Engine:
    def transcribe(self, path: str, **_: object):
        with wave.open(path, "rb") as source:
            assert source.getnframes() > 0
        return iter([_Segment(0, 0.02, "hello BBB")]), SimpleNamespace(language="en")


class AudioCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _Client()
        self.audio = AudioController(self.client)

    def test_ingest_tracks_and_events(self) -> None:
        observed = []
        self.audio.add_listener(observed.append)
        frame = self.audio.ingest(
            b"\x00\x00" * 480, sample_rate=48_000, channels=1,
            user_id="u-1", user_name="Ada", source="livekit",
        )
        self.assertEqual(frame.duration, 0.01)
        self.assertEqual(observed, [frame])
        track = self.audio.tracks()[0]
        self.assertEqual(track.user_id, "u-1")
        self.assertEqual(track.frames_received, 1)
        self.assertIn(("audio_frame", frame), self.client.events)

    def test_ingest_av_frame_scales_normalized_float_pcm(self) -> None:
        """PyAV's decoded ``fltp`` frames must not become silent PCM."""
        frame = SimpleNamespace(
            # Planar mono: two normalised floating-point samples.
            to_ndarray=lambda: np.array([[0.5, -0.5]], dtype=np.float32),
            layout=SimpleNamespace(channels=(object(),)),
            sample_rate=48_000,
        )
        captured = self.audio.ingest_av_frame(frame, mixed=True)
        samples = np.frombuffer(captured.pcm, dtype=np.int16)
        self.assertGreater(samples[0], 16_000)
        self.assertLess(samples[1], -16_000)

    def test_recording_starts_capture_backend_once(self) -> None:
        client = _MediaClient()
        audio = AudioController(client)
        with tempfile.TemporaryDirectory() as temp:
            first = audio.record(temp)
            second = audio.record(temp)
            self.assertEqual(client.starts, 1)
            first.stop()
            second.stop()

    def test_wav_recording_creates_separate_tracks_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            recorder = self.audio.record(temp)
            self.audio.ingest(b"\x01\x00" * 160, sample_rate=16_000, channels=1, user_id="a", user_name="Ada")
            self.audio.ingest(b"\x02\x00" * 160, sample_rate=16_000, channels=1, user_id="b", user_name="Ben")
            paths = recorder.stop()
            self.assertEqual(set(paths), {"a", "b"})
            for path in paths.values():
                with wave.open(str(path), "rb") as source:
                    self.assertEqual(source.getframerate(), 16_000)
                    self.assertEqual(source.getnframes(), 160)
            manifest = json.loads((Path(temp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["separate_tracks"])
            self.assertEqual(len(manifest["tracks"]), 2)

    def test_conference_mix_can_be_recorded_as_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            recorder = self.audio.record(temp, separate_tracks=False)
            self.audio.ingest(b"\x00\x00" * 80, sample_rate=8_000, channels=1, mixed=True)
            self.audio.ingest(b"\x00\x00" * 80, sample_rate=8_000, channels=1, mixed=True)
            paths = recorder.stop()
            self.assertEqual(set(paths), {"conference-mix"})
            with wave.open(str(next(iter(paths.values()))), "rb") as source:
                self.assertEqual(source.getnframes(), 160)

    def test_pyav_output_formats_are_finalized(self) -> None:
        # This verifies the WAV staging/transcoding lifecycle used on Windows,
        # where an encoder failure must not leave a locked hidden WAV behind.
        with tempfile.TemporaryDirectory() as temp:
            recorder = self.audio.record(temp, format="mp3", separate_tracks=False)
            self.audio.ingest(b"\x00\x00" * 1600, sample_rate=16_000, channels=1, mixed=True)
            path = next(iter(recorder.stop().values()))
            self.assertEqual(path.suffix, ".mp3")
            self.assertGreater(path.stat().st_size, 0)
            self.assertFalse(any(Path(temp).glob("*.capture.wav")))

    def test_async_frame_stream_delivers_live_pcm(self) -> None:
        async def receive() -> object:
            stream = self.audio.frames()
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0)
            self.audio.ingest(b"\x00\x00" * 80, sample_rate=8_000, channels=1, mixed=True)
            value = await asyncio.wait_for(pending, timeout=1)
            await stream.aclose()
            return value

        frame = asyncio.run(receive())
        self.assertEqual(frame.sample_rate, 8_000)

    def test_async_audio_proxy_preserves_frame_iterator(self) -> None:
        proxy = _AsyncAudioController(self.audio)
        self.assertTrue(hasattr(proxy.frames(), "__aiter__"))


class TranscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _Client()
        self.audio = AudioController(self.client)
        self.controller = TranscriptionController(self.client, self.audio)

    def test_live_transcription_and_exports(self) -> None:
        session = LiveTranscription(
            self.controller, chunk_seconds=0.01,
            engine_factory=lambda *_args, **_kwargs: _Engine(),
        ).start()
        self.audio.ingest(
            b"\x00\x00" * 160, sample_rate=16_000, channels=1,
            user_id="speaker", user_name="Speaker",
        )
        deadline = time.monotonic() + 3
        while not session.segments and time.monotonic() < deadline:
            time.sleep(0.02)
        values = session.stop()
        self.assertEqual(values[0].text, "hello BBB")
        self.assertEqual(values[0].user_id, "speaker")
        self.assertTrue(any(event == "transcript_segment" for event, _ in self.client.events))
        with tempfile.TemporaryDirectory() as temp:
            for format in ("srt", "vtt", "txt", "json"):
                path = session.export(Path(temp) / f"transcript.{format}", format=format)
                self.assertTrue(path.read_text(encoding="utf-8"))

    def test_export_validates_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                self.controller.export(Path(temp) / "x.bad", format="bad", segments=[
                    TranscriptSegment("x", 0, 1),
                ])


if __name__ == "__main__":
    unittest.main()
