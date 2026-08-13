"""Show active BBB speakers and local live transcription on a text-board share.

Install the optional local speech engine first:

    pip install "SmallBlueClient[transcription]"

Run with a session path, or set ``SESSION`` below:

    python examples/live_transcript_textboard.py meeting.sbc

BBB's regular SFU listener supplies a conference mix.  The active speaker names
come from BBB voice-activity events; the transcript is generated locally from
that same live mix.  Deployments which expose individual media tracks also add
the track identity directly to each transcript segment.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import sbc


# A session path can be supplied on the command line.  Keep the default local
# so the example remains easy to customise without changing library code.
SESSION = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("11.sbc")
RECORDINGS = Path(__file__).with_name("recordings") / "live-transcript"
IDLE = "Waiting for someone to speak…"


def transcription_model() -> str:
    """Return the installed large-v3-turbo snapshot without downloading."""
    cache_root = Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache" / "huggingface" / "hub"))
    repositories = (
        "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
        "models--Systran--faster-whisper-large-v3-turbo",
    )
    for repository in repositories:
        snapshots = cache_root / repository / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in snapshots.iterdir():
            if (snapshot / "config.json").is_file() and (snapshot / "model.bin").is_file():
                return str(snapshot)
    raise RuntimeError("No local faster-whisper large-v3-turbo snapshot was found.")


def main() -> None:
    with sbc.client(SESSION, listen_only=True) as client:
        board = client.screenshare.textboard(
            IDLE,
            title="Live speakers and transcript",
            width=1280,
            height=720,
            language="auto",
        )
        board_live = False
        speakers: dict[str, str] = {}
        transcript_lines: list[str] = []
        lock = threading.RLock()

        def redraw() -> None:
            with lock:
                names = ", ".join(sorted(speakers.values())) or "Nobody is speaking"
                transcript = "\n".join(transcript_lines[-5:]) or IDLE
                board.set_text(f"Speaking now: {names}\n\n{transcript}")

        @client.on(sbc.Event.USER_TALKING)
        def user_talking(user: sbc.User) -> None:
            with lock:
                speakers[user.id] = user.name
            redraw()

        @client.on(sbc.Event.USER_STOPPED_TALKING)
        def user_stopped_talking(user: sbc.User) -> None:
            with lock:
                speakers.pop(user.id, None)
            redraw()

        @client.on(sbc.Event.USER_LEFT)
        def user_left(user: sbc.User) -> None:
            with lock:
                speakers.pop(user.id, None)
            redraw()

        @client.on(sbc.Event.TRANSCRIPT_SEGMENT)
        def transcript_segment(segment: sbc.TranscriptSegment) -> None:
            # Per-user LiveKit tracks carry ``segment.user_name``. The normal
            # BBB SFU mix does not, so use the voice-activity speaker list.
            with lock:
                label = segment.user_name or ", ".join(sorted(speakers.values())) or "Live transcript"
                transcript_lines.append(f"{label}: {segment.text}")
                del transcript_lines[:-5]
            redraw()
            # This confirms that a final transcript was emitted independently
            # from the screen-share rendering path.
            print(f"Transcript: {label}: {segment.text}")

        transcript = None
        recorder = None
        try:
            # Start the visual share first. Model initialization can take time
            # and must not make the board appear to be missing after joining.
            client.screenshare.start(board)
            board_live = True
            # The standard BBB SFU listener is a conference mix, so this saves
            # one lossless WAV file. Backends with participant-labelled tracks
            # automatically create separate participant files instead.
            recorder = client.audio.record(RECORDINGS, format="wav", separate_tracks=True)
            print(f"Saving incoming audio tracks to: {RECORDINGS.resolve()}")
            model = transcription_model()
            print(f"Text board is live. Loading local transcription model: {model}")
            transcript = client.transcription.start(
                model=model,
                language=None,
                chunk_seconds=3,
            )
            print(
                "Live transcription is ready. After three seconds of received speech, "
                "the terminal prints 'Transcribing...' followed by 'Transcript: ...'. "
                "Press Ctrl+C to stop."
            )
            client.run()
        except KeyboardInterrupt:
            pass
        finally:
            if transcript is not None:
                transcript.stop()
                transcript.export(RECORDINGS / "transcript.txt", format="txt")
                transcript.export(RECORDINGS / "transcript.srt", format="srt")
            if recorder is not None:
                paths = recorder.stop()
                print("Saved audio:", ", ".join(str(path) for path in paths.values()) or "no audio frames received")
            if board_live:
                client.screenshare.stop()


if __name__ == "__main__":
    main()
