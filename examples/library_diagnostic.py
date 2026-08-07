"""Exercise SmallBlueClient against a BBB session and produce a detailed report.

The default run is non-destructive: it validates the session, exercises every
read controller, opens the event streams, and compiles every embedded BBB
mutation locally. ``--writes`` adds a short list of reversible self-actions.

For complete server-side mutation coverage, generate a plan, fill it with
real meeting-specific values, explicitly enable only the actions you want,
then execute it. ``meetingEnd`` is permanently excluded.

Examples::

    python examples/library_diagnostic.py examples/1.sbc
    python examples/library_diagnostic.py examples/1.sbc --writes --send-chat
    python examples/library_diagnostic.py --list-actions
    python examples/library_diagnostic.py --generate-action-plan action-plan.json
    python examples/library_diagnostic.py examples/1.sbc --action-plan action-plan.json --execute-plan
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# ``python examples/library_diagnostic.py`` otherwise puts ``examples/``
# before the checkout on sys.path and can accidentally test an older globally
# installed SmallBlueClient. Always test this repository when run from source.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sbc
from sbc.core.utils import PUBLIC_CHAT_ID


SECRET_KEYS = frozenset({
    "authorization", "auth_token", "cookie", "cookies", "password",
    "session_token", "token", "x-session-token",
})
NEVER_EXECUTE = frozenset({"meetingEnd"})
DESTRUCTIVE_ACTION_TOKENS = (
    "clear", "delete", "destroy", "eject", "end", "leave", "remove",
    "setMuted", "setRole", "setLocked", "setPresenter", "setProps",
    "setPolicy", "setRecording", "setWebcam", "transfer",
)


@dataclass(slots=True)
class Result:
    """One probe result included in the console and JSON report."""

    name: str
    status: str
    detail: str
    duration_ms: int
    category: str
    value: Any | None = None


def _redact(value: Any, *, key: str | None = None) -> Any:
    """Make an arbitrary SBC value JSON-safe without leaking credentials."""
    if key and key.lower().replace("-", "_") in SECRET_KEYS:
        return "<redacted>"
    if is_dataclass(value):
        return _redact(asdict(value), key=key)
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _redact(vars(value), key=key)
    return repr(value)


def _action_safety(name: str) -> str:
    """Classify an action for a human reviewing an execution plan."""
    if name in NEVER_EXECUTE:
        return "excluded"
    lowered = name.lower()
    if any(token.lower() in lowered for token in DESTRUCTIVE_ACTION_TOKENS):
        return "state-changing"
    if name in {"userSendActivitySign", "chatSetLastSeen", "chatSetTyping", "userSetAway"}:
        return "reversible-self"
    return "context-dependent"


class Diagnostic:
    """Runs probes and keeps full, safely serializable result data."""

    def __init__(self, client: sbc.SBCClient, *, full_details: bool = False) -> None:
        self.client = client
        self.full_details = full_details
        self.results: list[Result] = []
        self.action_inventory: list[dict[str, Any]] = []

    def probe(self, name: str, callback: Callable[[], Any], *, category: str) -> Any | None:
        started = time.perf_counter()
        try:
            value = callback()
        except Exception as error:
            result = Result(
                name, "failed", f"{type(error).__name__}: {error}", self._elapsed(started), category,
                {"exception": type(error).__name__, "message": str(error)},
            )
            self.results.append(result)
            print(f"[FAIL] {name}: {result.detail}")
            return None
        serialized = _redact(value)
        detail = self._describe(serialized)
        self.results.append(Result(name, "passed", detail, self._elapsed(started), category, serialized))
        print(f"[ OK ] {name}: {detail}")
        if self.full_details:
            print(json.dumps(serialized, indent=2, ensure_ascii=False, default=str))
        return value

    def skipped(self, name: str, detail: str, *, category: str, value: Any | None = None) -> None:
        self.results.append(Result(name, "skipped", detail, 0, category, _redact(value)))
        print(f"[SKIP] {name}: {detail}")

    @staticmethod
    def _elapsed(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    @staticmethod
    def _describe(value: Any) -> str:
        if isinstance(value, list):
            return f"list ({len(value)} item(s))"
        if isinstance(value, dict):
            return "dict: " + ", ".join(sorted(value)[:8])
        text = repr(value)
        return text if len(text) <= 180 else text[:177] + "..."

    def verify_reads(self) -> None:
        """Run every no-write high-level controller currently exposed by SBC."""
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
            self.probe(name, callback, category="read")

    @staticmethod
    def _placeholder(argument: Any) -> Any:
        """Return a locally valid placeholder for one action schema argument."""
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

    def inventory_actions(self) -> list[dict[str, Any]]:
        """Generate detailed metadata for every embedded BBB mutation."""
        inventory: list[dict[str, Any]] = []
        for name in self.client.actions.names:
            mutation = self.client.actions.schema(name)
            variables = {
                argument.name: self._placeholder(argument)
                for argument in mutation.arguments
                if argument.required
            }
            try:
                document, normalized = self.client.actions.build(name, **variables)
                compile_result: dict[str, Any] = {"ok": True, "variables": normalized, "document": document}
            except Exception as error:
                compile_result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            inventory.append({
                "name": name,
                "safety": _action_safety(name),
                "arguments": [
                    {"name": argument.name, "type": argument.type, "required": argument.required, "is_list": argument.is_list}
                    for argument in mutation.arguments
                ],
                "local_compile": compile_result,
            })
        self.action_inventory = inventory
        return inventory

    def verify_registry(self) -> None:
        """Compile every source-derived mutation and list controller coverage."""
        print("\n=== Embedded mutation registry ===")
        inventory = self.inventory_actions()
        failed = [item for item in inventory if not item["local_compile"]["ok"]]
        detail = f"{len(inventory) - len(failed)}/{len(inventory)} mutations compile locally"
        if failed:
            detail += "; " + " | ".join(f"{item['name']}: {item['local_compile']['error']}" for item in failed[:5])
        status = "passed" if not failed else "failed"
        self.results.append(Result("actions.registry", status, detail, 0, "registry", inventory))
        print(f"[{' OK ' if status == 'passed' else 'FAIL'}] actions.registry: {detail}")

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
            self.skipped(
                f"surface.{controller_name}",
                f"scenario-dependent methods: {', '.join(methods)}",
                category="surface",
                value={"methods": methods},
            )

    def verify_event_streams(self, seconds: float) -> None:
        """Open standard event streams and record every observed event type."""
        if seconds <= 0:
            self.skipped("events", "event probe disabled (--event-seconds 0)", category="events")
            return
        print(f"\n=== Event stream probe ({seconds:g}s) ===")
        events: tuple[sbc.Event | str, ...] = (
            sbc.Event.USER_JOINED, sbc.Event.CHAT_MESSAGE, sbc.Event.USER_TALKING,
            sbc.Event.PRESENTATION_CHANGED, sbc.Event.MEETING_ENDED,
            sbc.Event.POLL_UPDATED, sbc.Event.BREAKOUT_UPDATED, sbc.Event.TIMER_UPDATED,
            "current_user_updated", "screenshare_started",
        )
        errors: list[str] = []
        observed: list[dict[str, Any]] = []
        for event in events:
            self.client.on(event, lambda *_args, event=str(event): observed.append({
                "event": event, "observed_at": datetime.now(timezone.utc).isoformat(),
            }))
        self.client.on("error", lambda error: errors.append(f"{type(error).__name__}: {error}"))
        worker_error: list[Exception] = []

        def run() -> None:
            try:
                self.client.run()
            except Exception as error:  # The report must survive a stream failure.
                worker_error.append(error)

        worker = threading.Thread(target=run, name="sbc-diagnostic-events", daemon=True)
        started = time.perf_counter()
        worker.start()
        time.sleep(seconds)
        self.client.close()
        worker.join(timeout=5)
        payload = {"requested_events": [str(event) for event in events], "observed": observed, "errors": errors}
        detail = f"streams opened; {len(observed)} event(s) observed"
        if errors or worker_error:
            detail += "; " + " | ".join(errors + [f"{type(item).__name__}: {item}" for item in worker_error])
            self.results.append(Result("events", "failed", detail, self._elapsed(started), "events", payload))
            print(f"[FAIL] events: {detail}")
        else:
            self.results.append(Result("events", "passed", detail, self._elapsed(started), "events", payload))
            print(f"[ OK ] events: {detail}")

    def verify_reversible_writes(self, *, send_chat: bool) -> None:
        """Test writes that only affect the saved user and can be reversed."""
        print("\n=== Reversible write probes ===")
        # Call through the registry here as well as exposing chat.mark_read in
        # the public API. It verifies the exact BBB runtime mutation fields.
        self.probe(
            "chat.mark_read",
            lambda: self.client.actions.chatSetLastSeen(
                chatId=PUBLIC_CHAT_ID,
                lastSeenAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
            category="write",
        )
        self.probe("actions.chat_set_typing", lambda: self.client.actions.chat_set_typing(chat_id=PUBLIC_CHAT_ID), category="write")
        self.probe("reactions.set_away", lambda: self.client.reactions.set_away(True), category="write")
        self.probe("reactions.clear_away", lambda: self.client.reactions.set_away(False), category="write")
        self.probe("actions.user_send_activity_sign", self.client.actions.user_send_activity_sign, category="write")
        if send_chat:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.probe("chat.send", lambda: self.client.chat.send(f"SBC diagnostic write probe — {stamp}"), category="write")
        else:
            self.skipped("chat.send", "pass --send-chat to send one clearly labelled public diagnostic message", category="write")

    def execute_action_plan(self, actions: Iterable[dict[str, Any]]) -> None:
        """Execute only explicitly enabled entries from a user-reviewed plan."""
        print("\n=== Explicit action-plan probes ===")
        known = set(self.client.actions.names)
        for entry in actions:
            name = entry.get("name")
            if not isinstance(name, str):
                self.skipped("plan.invalid", "entry missing string name", category="plan", value=entry)
                continue
            if name in NEVER_EXECUTE:
                self.skipped(f"plan.{name}", "permanently excluded from this diagnostic", category="plan", value=entry)
                continue
            if name not in known:
                self.skipped(f"plan.{name}", "not in this SBC action registry", category="plan", value=entry)
                continue
            if not entry.get("enabled", False):
                self.skipped(f"plan.{name}", "disabled in action plan", category="plan", value=entry)
                continue
            variables = entry.get("variables", {})
            if not isinstance(variables, dict):
                self.skipped(f"plan.{name}", "variables must be a JSON object", category="plan", value=entry)
                continue
            self.probe(f"plan.{name}", lambda name=name, variables=variables: self.client.actions.call(name, **variables), category="plan")

    def write_report(self, path: Path, *, arguments: argparse.Namespace) -> None:
        counts = {status: sum(result.status == status for result in self.results) for status in ("passed", "failed", "skipped")}
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sbc_version": sbc.__version__,
            "command": {key: _redact(value) for key, value in vars(arguments).items()},
            "meeting": {
                "id": self.client.session.meeting_id,
                "name": self.client.session.meeting_name,
                "server": self.client.session.server,
                "role": self.client.session.role,
            },
            "summary": counts,
            "results": [asdict(result) for result in self.results],
            "action_inventory": self.action_inventory,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"\nReport: {path.resolve()}")
        print("Summary: " + ", ".join(f"{count} {status}" for status, count in counts.items()))


def _plan_entry(name: str, mutation: Any) -> dict[str, Any]:
    variables = {
        argument.name: "<replace-with-real-value>" if not argument.is_list else ["<replace-with-real-value>"]
        for argument in mutation.arguments
        if argument.required
    }
    return {
        "name": name,
        "enabled": False,
        "safety": _action_safety(name),
        "variables": variables,
        "notes": "Fill variables with valid values from this meeting, then set enabled to true.",
    }


def write_action_plan(path: Path) -> None:
    """Create a disabled, reviewable template for all 109 server mutations."""
    from sbc.operations import load_registry  # Imported only for this no-session command.

    registry = load_registry()
    plan = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": (
            "All entries start disabled. Review each action and replace placeholders with real meeting values. "
            "Run with --action-plan PATH --execute-plan. meetingEnd is permanently skipped."
        ),
        "actions": [_plan_entry(name, mutation) for name, mutation in registry.items()],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Action plan template: {path.resolve()} ({len(registry)} actions; all disabled)")


def print_actions() -> None:
    """Print the complete mutation catalog with signatures and safety class."""
    from sbc.operations import load_registry

    for name, mutation in load_registry().items():
        signature = ", ".join(
            f"{argument.name}: {argument.graphql_type}{'' if argument.required else ' (optional)'}"
            for argument in mutation.arguments
        )
        print(f"{name} [{_action_safety(name)}]({signature})")


def load_action_plan(path: Path) -> list[dict[str, Any]]:
    """Validate and load a JSON plan without ever silently enabling actions."""
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Unable to read action plan {path}: {error}") from error
    actions = plan.get("actions") if isinstance(plan, dict) else None
    if not isinstance(actions, list) or not all(isinstance(item, dict) for item in actions):
        raise SystemExit("Action plan must be a JSON object containing an actions array.")
    return actions


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a detailed SBC capability diagnostic against one BBB session.")
    parser.add_argument("session", nargs="?", type=Path, default=Path(__file__).with_name("1.sbc"), help="path to the .sbc session")
    parser.add_argument("--event-seconds", type=float, default=8, help="seconds to keep live event streams open (default: 8)")
    parser.add_argument("--writes", action="store_true", help="run the reversible self-action probes")
    parser.add_argument("--send-chat", action="store_true", help="with --writes, send one labelled public diagnostic message")
    parser.add_argument("--no-auto-join", action="store_true", help="do not recover the saved user into the meeting")
    parser.add_argument("--full-details", action="store_true", help="also print complete safe probe payloads to the console")
    parser.add_argument("--report", type=Path, default=Path("sbc-diagnostic-report.json"), help="detailed JSON report destination")
    parser.add_argument("--list-actions", action="store_true", help="print all embedded mutations and exit (no session needed)")
    parser.add_argument("--generate-action-plan", type=Path, metavar="PATH", help="write a disabled all-action server-test plan and exit")
    parser.add_argument("--action-plan", type=Path, metavar="PATH", help="reviewed JSON action plan to use with --execute-plan")
    parser.add_argument("--execute-plan", action="store_true", help="execute enabled actions from --action-plan; meetingEnd is always excluded")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    if args.list_actions:
        print_actions()
        return
    if args.generate_action_plan:
        write_action_plan(args.generate_action_plan)
        return
    if args.send_chat and not args.writes:
        raise SystemExit("--send-chat requires --writes")
    if args.execute_plan and not args.action_plan:
        raise SystemExit("--execute-plan requires --action-plan PATH")
    if args.action_plan and not args.execute_plan:
        raise SystemExit("--action-plan requires --execute-plan; plans never run implicitly")
    if args.event_seconds < 0:
        raise SystemExit("--event-seconds must be zero or greater")

    action_plan = load_action_plan(args.action_plan) if args.action_plan else []
    sbc.enable_logging("INFO")
    client = sbc.client(args.session, auto_join=not args.no_auto_join)
    diagnostic = Diagnostic(client, full_details=args.full_details)
    try:
        diagnostic.verify_reads()
        diagnostic.verify_registry()
        diagnostic.verify_event_streams(args.event_seconds)
        # Event probing closes the shared transport. Use an independent client
        # for all writes and explicit plan actions.
        if args.writes or action_plan:
            client = sbc.client(args.session, auto_join=not args.no_auto_join)
            diagnostic.client = client
            if args.writes:
                diagnostic.verify_reversible_writes(send_chat=args.send_chat)
            if action_plan:
                diagnostic.execute_action_plan(action_plan)
    finally:
        client.close()
    diagnostic.write_report(args.report, arguments=args)


if __name__ == "__main__":
    main()
