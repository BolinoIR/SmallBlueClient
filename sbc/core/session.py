from __future__ import annotations
import hashlib
import json
import zipfile
import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .exceptions import SessionError

FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SessionHealth:
    """A clear, serializable status report for an SBC session credential."""

    valid: bool
    expired: bool
    requires_reexport: bool
    expires_at: datetime | None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "expired": self.expired,
            "requires_reexport": self.requires_reexport,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reasons": list(self.reasons),
        }

@dataclass(slots=True)
class SBCSession:
    """Portable authenticated BBB connection captured by the SBC extension."""
    server: str
    websocket_url: str
    meeting_id: str | None = None
    meeting_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    role: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    connection_payload: dict[str, Any] = field(default_factory=dict)
    protocol: str = "graphql-transport-ws"
    metadata: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    version: int = FORMAT_VERSION

    @property
    def expires_at(self) -> datetime | None:
        """Best available credential expiry, if BBB exposed one in the session."""
        candidates: list[Any] = [
            self.metadata.get("expires_at"),
            self.metadata.get("expiresAt"),
            self.snapshot.get("expires_at"),
            self.snapshot.get("expiresAt"),
            self.snapshot.get("session_expires_at"),
            self.snapshot.get("sessionExpiresAt"),
            (self.snapshot.get("current_user") or {}).get("expiresAt"),
            (self.snapshot.get("current_user") or {}).get("expires_at"),
        ]
        for token in (
            (self.snapshot.get("livekit") or {}).get("token"),
            self.snapshot.get("livekit_token"),
            (self.snapshot.get("current_user") or {}).get("auth_token"),
        ):
            expiry = self._jwt_expiry(token)
            if expiry is not None:
                candidates.append(expiry)
        values = [self._as_datetime(value) for value in candidates]
        dates = [value for value in values if value is not None]
        return min(dates) if dates else None

    @property
    def requires_reexport(self) -> bool:
        return bool(self.metadata.get("needs_reexport") or self.snapshot.get("needs_reexport"))

    def mark_reexport_required(self, reason: str) -> None:
        """Remember that BBB rejected the captured browser credential."""
        self.metadata["needs_reexport"] = True
        self.metadata["reexport_reason"] = reason
        self.metadata["reexport_detected_at"] = datetime.now(timezone.utc).isoformat()
        self.snapshot["needs_reexport"] = True

    def validate(self, *, now: datetime | None = None) -> SessionHealth:
        """Validate local session structure and report credential health clearly."""
        now = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        if not self.server.startswith(("http://", "https://")):
            reasons.append("server must be an HTTP(S) URL")
        if not self.websocket_url.startswith(("ws://", "wss://")):
            reasons.append("websocket_url must be a WebSocket URL")
        headers = self.connection_payload.get("headers") or {}
        has_token = bool(headers.get("X-Session-Token"))
        has_cookie = bool(self.headers.get("Cookie"))
        if not has_token and not has_cookie:
            reasons.append("session has neither an X-Session-Token nor browser Cookie")
        expiry = self.expires_at
        expired = bool(expiry and expiry <= now)
        if expired:
            reasons.append("captured credential has expired")
            self.mark_reexport_required("credential expiry timestamp passed")
        if self.requires_reexport:
            reasons.append(str(self.metadata.get("reexport_reason", "BBB requires a newly exported session")))
        return SessionHealth(
            valid=not reasons,
            expired=expired,
            requires_reexport=self.requires_reexport,
            expires_at=expiry,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                return None
        return None

    @classmethod
    def _jwt_expiry(cls, token: Any) -> datetime | None:
        if not isinstance(token, str) or token.count(".") != 2:
            return None
        try:
            payload = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
            return cls._as_datetime(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
        except Exception:
            return None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SBCSession":
        # v0 extension exports used camelCase fields.
        aliases = {"websocketUrl": "websocket_url", "meetingId": "meeting_id", "meetingName": "meeting_name", "userId": "user_id", "userName": "user_name", "connectionPayload": "connection_payload"}
        normalized = {aliases.get(k, k): v for k, v in value.items()}
        required = ("server", "websocket_url")
        missing = [key for key in required if not normalized.get(key)]
        if missing:
            raise SessionError(f"session missing required field(s): {', '.join(missing)}")
        version = normalized.get("version", FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise SessionError(f"session format {version} is newer than this SBC version")
        allowed = {f.name for f in __import__('dataclasses').fields(cls)}
        return cls(**{key: value for key, value in normalized.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = self.metadata.get("created_at", datetime.now(timezone.utc).isoformat())
        return result

    @classmethod
    def load(cls, path: str | Path) -> "SBCSession":
        requested = Path(path).expanduser()
        path = requested
        if not path.is_absolute() and not path.exists():
            # A bare ``client("teacher.sbc")`` should work in the common project
            # layouts without users having to learn Python's process CWD rules.
            roots = (Path.cwd(), Path.cwd() / "sessions", Path.cwd() / "examples", Path(__file__).resolve().parent.parent / "sessions", Path(__file__).resolve().parent.parent / "examples")
            for root in roots:
                candidate = root / requested
                if candidate.exists():
                    path = candidate
                    break
        if not path.is_file():
            raise SessionError(f"session file not found: {requested}")
        try:
            raw = path.read_bytes()
            if raw.lstrip().startswith(b"{"):
                envelope = json.loads(raw)
                manifest, digest = envelope["session"], envelope["sha256"]
            else:
                with zipfile.ZipFile(path, "r") as archive:
                    manifest = json.loads(archive.read("session.json"))
                    digest = archive.read("sha256").decode("ascii").strip()
        except (OSError, KeyError, TypeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise SessionError(f"invalid SBC session file: {path}") from exc
        # Python archives use ASCII-escaped JSON. Chrome's session extractor
        # canonically sorts the same object but keeps Unicode text literal.
        # Verify either representation so exported display names in every
        # language remain portable without weakening the integrity check.
        canonical_payloads = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        )
        if not any(hashlib.sha256(payload).hexdigest() == digest for payload in canonical_payloads):
            raise SessionError("session integrity check failed")
        return cls.from_dict(manifest)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        if path.suffix.lower() != ".sbc":
            path = path.with_suffix(".sbc")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("session.json", encoded)
            archive.writestr("sha256", hashlib.sha256(encoded).hexdigest())
        return path
