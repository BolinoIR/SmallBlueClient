"""Record BBB incoming audio and create a local live transcript.

Install the optional local engine first:

    pip install "SmallBlueClient[transcription]"

The normal BBB WebRTC SFU listener exposes a conference mix.  If a media
backend supplies individual participant tracks, SBC automatically stores and
labels one file/transcript stream per participant instead.
"""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("meeting.sbc")
OUTPUT = Path(__file__).with_name("recordings")


def main() -> None:
    with sbc.client(SESSION, listen_only=True) as client:
        recording = client.audio.record(OUTPUT, format="wav", separate_tracks=True)
        transcript = client.transcription.start(
            model="base",
            language=None,  # Detect automatically; use "fa", "en", etc. to force a language.
            chunk_seconds=5,
        )

        @client.on(sbc.Event.TRANSCRIPT_SEGMENT)
        def show(segment: sbc.TranscriptSegment) -> None:
            speaker = segment.user_name or segment.user_id or "Conference"
            print(f"[{speaker}] {segment.text}")

        print("Recording and transcribing. Press Enter to finish.")
        try:
            input()
        finally:
            transcript.stop()
            transcript.export(OUTPUT / "transcript.srt", format="srt")
            transcript.export(OUTPUT / "transcript.json", format="json")
            recording.stop()
            print(f"Saved audio tracks and transcript to {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
