"""Run a credential-safe five-minute BBB reliability check."""
from pathlib import Path
import sys

import sbc


SESSION = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("test.sbc")

with sbc.client(SESSION, listen_only=False) as client:
    # Optional: pass a second command-line argument to monitor a looping clip.
    if len(sys.argv) > 2:
        client.media.audio.play(sys.argv[2], loop=True, gain_db=-6, fade_in=0.2)
    report = sbc.EnduranceMonitor(client, interval=30).run(duration=300)
    report.save("sbc-endurance-report.json")
    print(f"Healthy={report.healthy}; recovery attempts={report.recoveries}")
