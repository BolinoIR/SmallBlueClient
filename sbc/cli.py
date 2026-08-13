"""The dependency-free ``sbc`` command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core.client import SBCClient
from .core.session import SBCSession
from .reliability import EnduranceMonitor


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _safe_session(session: SBCSession) -> dict[str, Any]:
    """Display identity/endpoint metadata without leaking session credentials."""
    return {"version": session.version, "server": session.server,
            "websocket_url": session.websocket_url, "meeting_id": session.meeting_id,
            "meeting_name": session.meeting_name, "user_id": session.user_id,
            "user_name": session.user_name, "role": session.role,
            "protocol": session.protocol, "expires_at": str(session.expires_at) if session.expires_at else None,
            "requires_reexport": session.requires_reexport,
            "capture": {key: value for key, value in session.metadata.items()
                        if "token" not in key.lower() and "secret" not in key.lower()}}


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(_json(value))
    elif isinstance(value, dict):
        for key, item in value.items(): print(f"{key}: {item}")
    else: print(value)


def _client(args: argparse.Namespace) -> SBCClient:
    return SBCClient.from_file(args.session, connect=True, auto_join=not args.no_auto_join,
                               listen_only=not args.microphone)


def command_validate(args: argparse.Namespace) -> int:
    health = SBCSession.load(args.session).validate().to_dict()
    _emit(health, args.json)
    return 0 if health["valid"] else 1


def command_inspect(args: argparse.Namespace) -> int:
    _emit(_safe_session(SBCSession.load(args.session)), args.json)
    return 0


def command_diagnose(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        report = {"session": client.session.validate().to_dict(),
                  "meeting": repr(client.meeting.info()), "users": len(client.users.list()),
                  "media": client.media.status()}
        _emit(report, args.json)
        return 0
    finally:
        client.close()


def command_endurance(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        if args.audio:
            client.media.audio.play(args.audio, loop=True, gain_db=args.gain_db, fade_in=args.fade_in)
        monitor = EnduranceMonitor(client, interval=args.interval, monitor_media=not args.no_media)
        report = monitor.run(duration=args.minutes * 60)
        if args.output: report.save(args.output)
        _emit(report.to_dict(), args.json)
        return 0 if report.healthy else 2
    finally:
        client.close()


def command_transcribe(args: argparse.Namespace) -> int:
    """Capture BBB incoming audio and write local transcript artifacts."""
    client = _client(args)
    try:
        recording = client.audio.record(args.output, format=args.format, separate_tracks=not args.mix)
        transcription = client.transcription.start(
            model=args.model,
            language=args.language,
            chunk_seconds=args.chunk_seconds,
            device=args.device,
            compute_type=args.compute_type,
        )
        print(f"Capturing BBB audio for {args.minutes:g} minute(s). Press Ctrl+C to finish early.")
        try:
            import time
            time.sleep(args.minutes * 60)
        except KeyboardInterrupt:
            pass
        finally:
            transcription.stop()
            paths = recording.stop()
        transcript = transcription.export(Path(args.output) / f"transcript.{args.transcript_format}", format=args.transcript_format)
        _emit({"audio_tracks": {key: str(value) for key, value in paths.items()}, "transcript": str(transcript)}, args.json)
        return 0
    finally:
        client.close()


def command_run(args: argparse.Namespace) -> int:
    env = os.environ.copy(); env["SBC_SESSION"] = str(Path(args.session).resolve())
    return subprocess.call([sys.executable, args.script, args.session, *args.arguments], env=env)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sbc", description="SmallBlueClient tools")
    root.add_argument("--version", action="version", version="SmallBlueClient")
    commands = root.add_subparsers(dest="command", required=True)
    for name, action, help_text in (("validate", command_validate, "validate an exported .sbc session"),
                                    ("inspect", command_inspect, "show safe session metadata")):
        item = commands.add_parser(name, help=help_text); item.add_argument("session"); item.add_argument("--json", action="store_true"); item.set_defaults(action=action)
    for name, action, help_text in (("diagnose", command_diagnose, "query basic BBB client health"),
                                    ("endurance", command_endurance, "record long-running client/media reliability")):
        item = commands.add_parser(name, help=help_text); item.add_argument("session"); item.add_argument("--json", action="store_true"); item.add_argument("--no-auto-join", action="store_true"); item.add_argument("--microphone", action="store_true")
        if name == "endurance":
            item.add_argument("--minutes", type=float, default=5); item.add_argument("--interval", type=float, default=30)
            item.add_argument("--audio"); item.add_argument("--gain-db", type=float, default=0); item.add_argument("--fade-in", type=float, default=0)
            item.add_argument("--no-media", action="store_true"); item.add_argument("--output", default="sbc-endurance-report.json")
        item.set_defaults(action=action)
    item = commands.add_parser("transcribe", help="record BBB incoming audio and generate a local transcript")
    item.add_argument("session"); item.add_argument("--json", action="store_true")
    item.add_argument("--no-auto-join", action="store_true"); item.add_argument("--microphone", action="store_true")
    item.add_argument("--minutes", type=float, default=5); item.add_argument("--output", default="sbc-transcript")
    item.add_argument("--format", default="wav", choices=("wav", "mp3", "flac", "ogg", "opus"))
    item.add_argument("--transcript-format", default="srt", choices=("srt", "vtt", "txt", "json"))
    item.add_argument("--model", default="base"); item.add_argument("--language")
    item.add_argument("--chunk-seconds", type=float, default=5); item.add_argument("--device", default="auto")
    item.add_argument("--compute-type", default="default"); item.add_argument("--mix", action="store_true")
    item.set_defaults(action=command_transcribe)
    item = commands.add_parser("run", help="run a bot script with SBC_SESSION set")
    item.add_argument("script"); item.add_argument("session"); item.add_argument("arguments", nargs=argparse.REMAINDER); item.set_defaults(action=command_run)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.action(args))


if __name__ == "__main__":
    raise SystemExit(main())
