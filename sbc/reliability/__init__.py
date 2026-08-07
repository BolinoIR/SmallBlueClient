"""Long-running health monitoring and evidence-rich SBC endurance reports."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.logging import get_logger


@dataclass(frozen=True, slots=True)
class ReliabilitySample:
    """One timestamped, credential-safe observation of a running client."""

    elapsed_seconds: float
    timestamp: str
    session: dict[str, Any]
    media: dict[str, Any]
    current_user: dict[str, Any] | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReliabilityReport:
    """Portable output of an :class:`EnduranceMonitor` run."""

    started_at: str
    duration_seconds: float
    interval_seconds: float
    samples: list[ReliabilitySample] = field(default_factory=list)
    recoveries: int = 0

    @property
    def healthy(self) -> bool:
        return bool(self.samples) and all(sample.error is None for sample in self.samples)

    def to_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "duration_seconds": self.duration_seconds,
                "interval_seconds": self.interval_seconds, "healthy": self.healthy,
                "recoveries": self.recoveries,
                "samples": [sample.to_dict() for sample in self.samples]}

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class EnduranceMonitor:
    """Monitor a client for minutes or hours without exposing its credentials.

    For a looping custom audio source, each poll also checks whether outbound
    RTP counters advance. A stale sender is replaced with a fresh SFU session.
    """

    def __init__(self, client: Any, *, interval: float = 30.0,
                 monitor_media: bool = True) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.client = client
        self.interval = interval
        self.monitor_media = monitor_media

    def sample(self, elapsed: float) -> tuple[ReliabilitySample, bool]:
        error = None
        recovered = False
        try:
            session = self.client.session.validate().to_dict()
            media = self.client.media.status()
            if self.monitor_media and media.get("audio") != "stopped":
                health = self.client.media.audio.health(stall_after=self.interval, recover=True)
                media["health"] = health.to_dict()
                recovered = health.recovered
            current = self.client.session.snapshot.get("current_user") or None
            if current:
                current = {key: value for key, value in current.items()
                           if key not in {"auth_token", "authToken"}}
        except Exception as exc:  # Reports must survive the issue they capture.
            session, media, current, error = {}, {}, None, f"{type(exc).__name__}: {exc}"
        return ReliabilitySample(round(elapsed, 3), datetime.now(timezone.utc).isoformat(),
                                 session, media, current, error), recovered

    def run(self, *, duration: float, on_sample: Any | None = None) -> ReliabilityReport:
        """Run synchronously for ``duration`` seconds and return all samples."""
        if duration <= 0:
            raise ValueError("duration must be positive")
        started = time.monotonic()
        report = ReliabilityReport(datetime.now(timezone.utc).isoformat(), duration, self.interval)
        while True:
            elapsed = time.monotonic() - started
            sample, recovered = self.sample(elapsed)
            report.samples.append(sample); report.recoveries += int(recovered)
            if on_sample is not None:
                on_sample(sample)
            if elapsed >= duration:
                break
            time.sleep(min(self.interval, duration - elapsed))
        get_logger().info("SBC endurance run complete: %s samples, %s recovery attempt(s)",
                          len(report.samples), report.recoveries)
        return report
