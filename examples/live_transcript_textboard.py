"""Show active BBB speakers and local live transcription on a text-board share.

Install the optional local speech engine first:

    pip install "SmallBlueClient[transcription]"

Run with a session path, or place ``meeting.sbc`` beside this file:

    python examples/live_transcript_textboard.py meeting.sbc

BBB's regular SFU listener supplies a conference mix.  The active speaker names
come from BBB voice-activity events; the transcript is generated locally from
that same live mix.  Deployments which expose individual media tracks also add
the track identity directly to each transcript segment.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import sbc


SESSION = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("meeting.sbc")
IDLE = "Waiting for someone to speak…"


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

        transcript = client.transcription.start(
            model=sbc.TranscriptionModel.BASE,
            language=None,
            chunk_seconds=3,
        )
        try:
            client.screenshare.start(board)
            board_live = True
            print("Text board is live. Press Ctrl+C to stop.")
            client.run()
        except KeyboardInterrupt:
            pass
        finally:
            transcript.stop()
            if board_live:
                client.screenshare.stop()


if __name__ == "__main__":
    main()
