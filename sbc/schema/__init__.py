"""Versioned, source-generated BBB GraphQL schema catalogs."""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..types import StringEnum


# A source checkout uses this small fallback until its release build generates
# ``_schema_generated.py``. Installed wheels always contain the full catalog.
_FALLBACK_TABLES = (
    "meeting", "user_current", "chat_message_private", "chat_message_public", "user",
    "pres_annotation_curr", "pres_annotation_history_curr", "user_camera", "breakoutRoom_user",
    "chat_user", "meeting_componentFlags", "pres_page", "pres_page_curr", "user_clientSettings",
    "user_voice", "meeting_lockSettings", "poll", "poll_option", "poll_response", "poll_user",
    "breakoutRoom", "timer", "pres_page_cursor", "pres_presentation", "caption_activeLocales",
    "meeting_usersPolicies", "chat", "pluginDataChannelEntry", "pluginDataChannelEntry_public",
    "user_connectionStatus", "current_time", "externalVideo", "layout", "meeting_breakoutPolicies",
    "meeting_group", "user_guest", "meeting_recording", "meeting_recordingPolicies", "meeting_voiceSettings",
    "user_typing_private", "user_typing_public", "pres_page_writers", "screenshare", "sharedNotes",
    "sharedNotes_session", "user_connectionStatusHistory", "user_connectionStatusReport", "user_reaction",
    "user_reaction_current", "user_welcomeMsgs", "meeting_clientSettings", "caption", "meeting_learningDashboard",
    "meeting_clientPluginSettings", "pollUserCurrent", "pres_presentation_uploadToken", "sharedNotes_diff",
    "user_metadata", "user_voice_activity", "notification", "user_transcriptionError", "plugin",
    "user_presenceLog", "breakoutRoom_createdLatest", "user_livekit", "user_session", "user_session_current",
)


def _parse_markdown(source: Path) -> dict[str, tuple[str, ...]]:
    current: str | None = None
    fields_section = False
    rows: dict[str, list[str]] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## Type: (?P<name>[^\s]+)\s*$", line)
        if heading:
            current = heading.group("name")
            rows[current] = []
            fields_section = False
            continue
        if current is None:
            continue
        if line == "### Fields:":
            fields_section = True
            continue
        if line.startswith("### "):
            fields_section = False
            continue
        field = re.match(r"^- `(?P<field>[^`]+)`", line) if fields_section else None
        if field:
            rows[current].append(field.group("field"))
    return {name: tuple(values) for name, values in rows.items()}

try:  # Present in a built wheel; absent in an unbuilt development checkout.
    from .._schema_generated import SCHEMA_VERSION, TABLE_FIELDS, TABLES
except ImportError:
    SCHEMA_VERSION = "3.0"
    _checkout_source = Path(__file__).resolve().parent.parent / "bigbluebutton-3.0.32" / "bbb-graphql-server" / "bbb-graphql-schema.md"
    _checkout_catalog = _parse_markdown(_checkout_source) if _checkout_source.is_file() else {}
    TABLES = tuple(_checkout_catalog) or _FALLBACK_TABLES
    TABLE_FIELDS: Mapping[str, tuple[str, ...]] = _checkout_catalog or {name: () for name in TABLES}

BBB_TABLE_NAMES = tuple(TABLES)


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


BBBTable = Enum(
    "BBBTable",
    {re.sub(r"[^A-Z0-9]+", "_", name.upper()): name for name in BBB_TABLE_NAMES},
    type=StringEnum,
    module=__name__,
)

# Prefix generated table events so they never collide with high-level events.
TABLE_EVENTS = tuple(f"table_{_snake_case(name)}_changed" for name in BBB_TABLE_NAMES)


class SchemaCatalog:
    """A BBB-versioned table/field catalog and subscription builder."""

    def __init__(self, *, version: str = SCHEMA_VERSION, tables: tuple[str, ...] = BBB_TABLE_NAMES,
                 fields: Mapping[str, tuple[str, ...]] = TABLE_FIELDS) -> None:
        self.version = version
        self.table_names = tuple(tables)
        self._fields = {name: tuple(fields.get(name, ())) for name in self.table_names}
        self.tables = BBBTable if self.table_names == BBB_TABLE_NAMES else tuple(self.table_names)

    @classmethod
    def from_markdown(cls, source: str | Path, *, version: str) -> "SchemaCatalog":
        """Load BBB 2.7, 3.0, or a future schema directly from its source file."""
        parsed = _parse_markdown(Path(source))
        if not parsed:
            raise ValueError(f"no BBB GraphQL types found in {source}")
        return cls(version=version, tables=tuple(parsed), fields=parsed)

    def fields(self, table: BBBTable | str) -> tuple[str, ...]:
        table_name = self._table_name(table)
        return self._fields[table_name]

    def default_selection(self, table: BBBTable | str) -> str:
        fields = self.fields(table)
        if not fields:
            raise ValueError(
                f"{self._table_name(table)!r} has no generated field data in this source checkout; "
                "pass fields explicitly or build/install the package"
            )
        return " ".join(fields)

    def subscription(self, table: BBBTable | str, fields: str | None = None, *, name: str = "SBCStream", arguments: str = "") -> str:
        table_name = self._table_name(table)
        selection = fields.strip() if fields else self.default_selection(table_name)
        if not selection:
            raise ValueError("subscription fields cannot be empty")
        args = f"({arguments})" if arguments.strip() else ""
        return f"subscription {name}{{{table_name}{args}{{{selection}}}}}"

    def watch(self, client: Any, table: BBBTable | str, fields: str | None, handler, *, name: str = "SBCStream", arguments: str = "", variables: dict[str, Any] | None = None) -> None:
        client.watch(self.subscription(table, fields, name=name, arguments=arguments), handler, variables=variables)

    def event_name(self, table: BBBTable | str) -> str:
        return f"table_{_snake_case(self._table_name(table))}_changed"

    def watch_event(self, client: Any, table: BBBTable | str, fields: str | None = None, *, event: str | None = None, name: str = "SBCStream", arguments: str = "", variables: dict[str, Any] | None = None) -> str:
        """Subscribe to a table and emit a generated ``table_*_changed`` event."""
        table_name = self._table_name(table)
        event = event or self.event_name(table_name)

        def dispatch(data: dict[str, Any]) -> None:
            client.emit(event, data.get(table_name, []))

        self.watch(client, table_name, fields, dispatch, name=name, arguments=arguments, variables=variables)
        return event

    def _table_name(self, table: BBBTable | str) -> str:
        table_name = table.value if isinstance(table, Enum) else str(table)
        if table_name not in self.table_names:
            raise ValueError(f"{table_name!r} is not in BBB {self.version}'s source schema")
        return table_name


class SchemaCatalogs:
    """Registry for bundled and caller-provided BBB schema versions."""

    def __init__(self) -> None:
        self._catalogs: dict[str, SchemaCatalog] = {SCHEMA_VERSION: SchemaCatalog()}

    def get(self, version: str = SCHEMA_VERSION) -> SchemaCatalog:
        try:
            return self._catalogs[version]
        except KeyError as exc:
            raise KeyError(f"BBB schema {version!r} is not loaded; use catalogs.load(...)") from exc

    def load(self, source: str | Path, *, version: str) -> SchemaCatalog:
        catalog = SchemaCatalog.from_markdown(source, version=version)
        self._catalogs[version] = catalog
        return catalog

    @property
    def versions(self) -> tuple[str, ...]:
        return tuple(self._catalogs)


schema = SchemaCatalog()
catalogs = SchemaCatalogs()
