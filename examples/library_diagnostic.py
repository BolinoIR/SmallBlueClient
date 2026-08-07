"""Exercise SmallBlueClient against a real BBB session and write a JSON report.

This is a capability probe, not a destructive meeting-management script. It
performs every available read operation, starts every built-in event stream,
validates all embedded BBB mutation definitions locally, and can optionally
run a small set of reversible write probes.  It never calls ``meetingEnd``.

Examples::

    python examples/library_diagnostic.py examples/1.sbc
    python examples/library_diagnostic.py examples/1.sbc --writes --send-chat
    python examples/library_diagnostic.py examples/1.sbc --event-seconds 15
"""
from __future__ import annotations

import argparse
import inspect
import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import sbc
from sbc.core.utils import PUBLIC_CHAT_ID


@dataclass(slots=True)
class Result:
    name: str
    status: str
    detail: str
    duration_ms: int


class Diagnostic:
    def __init__(self, client: sbc.SBCClient) -> None:
        self.client = client
        self.results: list[Result] = []

    def probe(self, name: str, callback: Callable[[], Any]) -> Any | None:
        started = time.perf_counter()
        try:
            value = callback()
        except Exception as error:
            self.results.append(Result(name, "failed", f"{type(error).__name__}: {error}", self._elapsed(started)))
            print(f"[FAIL] {name}: {type(error).__name__}: {error}")
            return None
        detail = self._describe(value)
        self.results.append(Result(name, "passed", detail, self._elapsed(started)))
        print(f"[ OK ] {name}: {detail}")
        return value

    def skipped(self, name: str, detail: str) -> None:
        self.results.append(Result(name, "skipped", detail, 0))
        print(f"[SKIP] {name}: {detail}")

    @staticmethod
    def _elapsed(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    @staticmethod
    def _describe(value: Any) -> str:
        if isinstance(value, (list, tuple, set)):
            return f"{type(value).__name__} ({len(value)} item(s))"
        if isinstance(value, dict):
            return "dict: " + ", ".join(sorted(value)[:8])
        text = repr(value)
        return text if len(text) <= 180 else text[:177] + "..."

    def verify_reads(self) -> None:
        """Run every high-level read operation available in SBC."""
        print("\n=== Read/controller probes ===")
        reads: tuple[tuple[str, Callable[[], Any]], ...] = (
            ("session.validate", self.client.session.validate),
            ("meeting.info", self.client.meeting.info),
            ("users.list", self.client.users.list),
            ("chat.public_history", self.client.chat.public_history),
            ("chat.private_history", self.client.chat.private_history),
            ("presentations.list", self.client.presentations.list),
            ("presentation.current", self.client.presentation.current),
            ("polls.list", self.client.polls.list),
            ("breakouts.list", self.client.breakouts.list),
            ("cameras.list", self.client.cameras.list),
            ("captions.transcript", self.client.captions.transcript),
            ("recording.status", self.client.recording.status),
            ("whiteboard.current", self.client.whiteboard.current),
            ("guests.list", self.client.guests.list),
            ("screenshare.current", self.client.screenshare.current),
            ("media.credentials", self.client.media.credentials),
            ("media.status", self.client.media.status),
        )
        for name, callback in reads:
            self.probe(name, callback)

    @staticmethod
    def _placeholder(argument: Any) -> Any:
        """Return a locally valid placeholder for an action-schema argument."""
        if argument.is_list:
            return []
        if argument.type == "Boolean":
            return False
        if argument.type == "Int":
            return 0
        if argument.type == "Float":
            return 0.0
        if argument.type == "json":
            return {}
        if argument.type in {"BreakoutRoom", "GuestUserApprovalStatus", "MediaGroupParticipant", "MediaGroupStateEntry"}:
            return {}
        return "SBC_DIAGNOSTIC"

    def verify_registry(self) -> None:
        """Compile all 109 source-derived BBB mutation definitions locally."""
        print("\n=== Embedded mutation registry ===")
        valid = 0
        failed: list[str] = []
        for name in self.client.actions.names:
            mutation = self.client.actions.schema(name)
            variables = {
                argument.name: self._placeholder(argument)
                for argument in mutation.arguments
                if argument.required
            }
            try:
                document, normalized = self.client.actions.build(name, **variables)
                assert name in document and normalized == variables
                valid += 1
            except Exception as error:
                failed.append(f"{name}: {type(error).__name__}: {error}")
        status = "passed" if not failed else "failed"
        detail = f"{valid}/{len(self.client.actions.names)} mutations compile locally"
        if failed:
            detail += "; " + " | ".join(failed[:5])
        self.results.append(Result("actions.registry", status, detail, 0))
        print(f"[{' OK ' if status == 'passed' else 'FAIL'}] actions.registry: {detail}")

        # Record controller methods that need room-specific IDs, streams,
        # plugin names, or file paths. They cannot be honestly server-tested
        # with invented values.
        for controller_name in (
            "chat", "users", "meeting", "presentation", "presentations", "polls",
            "breakouts", "cameras", "captions", "notes", "recording", "whiteboard",
            "guests", "timer", "external_video", "plugins", "media_groups", "settings",
            "locks", "screenshare", "reactions", "media",
        ):
            controller = getattr(self.client, controller_name)
            methods = [
                f"{name}{inspect.signature(getattr(controller, name))}"
                for name in dir(controller)
                if not name.startswith("_") and callable(getattr(controller, name))
            ]
            self.skipped(f"surface.{controller_name}", f"scenario-dependent methods: {', '.join(methods)}")

    def verify_event_streams(self, seconds: float) -> None:
        """Open every standard SBC event stream for a short live health check."""
        if seconds <= 0:
            self.skipped("events", "event probe disabled (--event-seconds 0)")
            return
        print(f"\n=== Event stream probe ({seconds:g}s) ===")
        events = (
            sbc.Event.USER_JOINED, sbc.Event.CHAT_MESSAGE, sbc.Event.USER_TALKING,
            sbc.Event.PRESENTATION_CHANGED, sbc.Event.MEETING_ENDED,
            sbc.Event.POLL_UPDATED, sbc.Event.BREAKOUT_UPDATED, sbc.Event.TIMER_UPDATED,
            "current_user_updated", "screenshare_started",
        )
        errors: list[str] = []
        observed: list[str] = []
        for event in events:
            self.client.on(event, lambda *_args, event=str(event): observed.append(event))
        self.client.on("error", lambda error: errors.append(f"{type(error).__name__}: {error}"))

        worker_error: list[Exception] = []

        def run() -> None:
            try:
                self.client.run()
            except Exception as error:  # The report must survive an event failure.
                worker_error.append(error)

        worker = threading.Thread(target=run, name="sbc-diagnostic-events", daemon=True)
        started = time.perf_counter()
        worker.start()
        time.sleep(seconds)
        self.client.close()
        worker.join(timeout=5)
        detail = f"streams opened; {len(observed)} event(s) observed"
        if errors or worker_error:
            detail += "; " + " | ".join(errors + [f"{type(item).__name__}: {item}" for item in worker_error])
            self.results.append(Result("events", "failed", detail, self._elapsed(started)))
            print(f"[FAIL] events: {detail}")
        else:
            self.results.append(Result("events", "passed", detail, self._elapsed(started)))
            print(f"[ OK ] events: {detail}")

    def verify_reversible_writes(self, *, send_chat: bool) -> None:
        """Optionally test mutations that affect only the saved SBC user."""
        print("\n=== Reversible write probes ===")
        self.probe("actions.chat_set_last_seen", self.client.actions.chat_set_last_seen)
        self.probe("actions.chat_set_typing", lambda: self.client.actions.chat_set_typing(chat_id=PUBLIC_CHAT_ID))
        self.probe("reactions.set_away", lambda: self.client.reactions.set_away(True))
        self.probe("reactions.clear_away", lambda: self.client.reactions.set_away(False))
        self.probe("actions.user_send_activity_sign", self.client.actions.user_send_activity_sign)
        if send_chat:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.probe("chat.send", lambda: self.client.chat.send(f"SBC diagnostic write probe — {stamp}"))
        else:
            self.skipped("chat.send", "pass --send-chat to send one clearly labelled public diagnostic message")

    def write_report(self, path: Path) -> None:
        counts = {status: sum(result.status == status for result in self.results) for status in ("passed", "failed", "skipped")}
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sbc_version": sbc.__version__,
            "meeting": {
                "id": self.client.session.meeting_id,
                "name": self.client.session.meeting_name,
                "server": self.client.session.server,
                "role": self.client.session.role,
            },
            "summary": counts,
            "results": [asdict(result) for result in self.results],
        }
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport: {path.resolve()}")
        print("Summary: " + ", ".join(f"{count} {status}" for status, count in counts.items()))


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SBC capability diagnostic against one BBB session.")
    parser.add_argument("session", nargs="?", type=Path, default=Path(__file__).with_name("1.sbc"), help="path to the .sbc session")
    parser.add_argument("--event-seconds", type=float, default=8, help="seconds to keep live event streams open (default: 8)")
    parser.add_argument("--writes", action="store_true", help="run reversible mutations for the saved SBC user")
    parser.add_argument("--send-chat", action="store_true", help="with --writes, send one labelled diagnostic message")
    parser.add_argument("--report", type=Path, default=Path("sbc-diagnostic-report.json"), help="JSON report destination")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    if args.send_chat and not args.writes:
        raise SystemExit("--send-chat requires --writes")
    sbc.enable_logging("INFO")
    client = sbc.client(args.session)
    diagnostic = Diagnostic(client)
    try:
        diagnostic.verify_reads()
        diagnostic.verify_registry()
        diagnostic.verify_event_streams(args.event_seconds)
        # The event probe closes its transport. Recreate an independent client
        # before optional write probes.
        if args.writes:
            client = sbc.client(args.session)
            diagnostic.client = client
            diagnostic.verify_reversible_writes(send_chat=args.send_chat)
    finally:
        client.close()
    diagnostic.write_report(args.report)


if __name__ == "__main__":
    main()
