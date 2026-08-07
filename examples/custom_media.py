"""Publish ``test.mp3`` as the BBB microphone directly from Python."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")
MEDIA = Path(__file__).with_name("test.mp3")


def main() -> None:
    # ``listen_only=False`` joins muted full audio so the clip can start with
    # minimal delay. The extension is not involved after loading this session.
    client = sbc.client(SESSION, listen_only=False)
    try:
        print("BBB media backend:", client.media.credentials())
        client.media.audio.prepare(MEDIA)
        client.media.audio.play(MEDIA, loop=True)
        print("Audio status:", client.media.status()["audio"])
        input("Playing test.mp3. Press Enter to stop... ")
    except sbc.MediaConnectionError as error:
        print(f"Audio connection failed: {error}")
    except EOFError:
        pass
    finally:
        client.media.audio.stop()
        client.close()


if __name__ == "__main__":
    main()
