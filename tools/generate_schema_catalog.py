"""Generate SBC's embedded versioned GraphQL catalog from BBB source.

This script uses only the BBB ``bbb-graphql-schema.md`` source file.  It is
called by ``setup.py build_py`` and can also generate a catalog for an older or
future BBB checkout:

    python tools/generate_schema_catalog.py path/to/bbb-graphql-schema.md \
        --version 2.7 --output sbc/_schema_generated_2_7.py
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


TYPE_HEADING = re.compile(r"^## Type: (?P<name>[^\s]+)\s*$")
FIELD = re.compile(r"^- `(?P<field>[^`]+)`")


def snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def class_name(table: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", snake_case(table)) if part) + "Row"


def parse_schema(source: Path) -> dict[str, tuple[str, ...]]:
    """Return BBB table names mapped to their scalar source-schema fields."""
    tables: dict[str, list[str]] = {}
    current: str | None = None
    fields_section = False
    for line in source.read_text(encoding="utf-8").splitlines():
        heading = TYPE_HEADING.match(line)
        if heading:
            current = heading.group("name")
            tables[current] = []
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
        if fields_section and (field := FIELD.match(line)):
            tables[current].append(field.group("field"))
    return {name: tuple(fields) for name, fields in tables.items()}


def render(version: str, tables: dict[str, tuple[str, ...]], source_name: str) -> str:
    rows: list[str] = [
        '"""Generated from BigBlueButton GraphQL schema source; do not edit by hand."""',
        "from __future__ import annotations",
        "from typing import Any, NotRequired, TypedDict",
        "",
        f'SCHEMA_VERSION = {version!r}',
        f'SOURCE_FILE = {source_name!r}',
        f"TABLES = {tuple(tables)!r}",
        "TABLE_FIELDS = {",
    ]
    rows.extend(f"    {name!r}: {fields!r}," for name, fields in tables.items())
    rows.extend(["}", f"TABLE_EVENTS = {tuple(f'table_{snake_case(name)}_changed' for name in tables)!r}", ""])
    for table, fields in tables.items():
        rows.append(f"class {class_name(table)}(TypedDict, total=False):")
        if fields:
            rows.extend(f"    {snake_case(field)}: NotRequired[Any]" for field in fields)
        else:
            rows.append("    _empty: NotRequired[Any]")
        rows.append("")
    rows.append("TABLE_MODELS = {")
    rows.extend(f"    {table!r}: {class_name(table)}," for table in tables)
    rows.extend(["}", ""])
    return "\n".join(rows)


def generate(source: Path, output: Path, version: str) -> Path:
    tables = parse_schema(source)
    if not tables:
        raise ValueError(f"no BBB GraphQL types found in {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(version, tables, source.name), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--version", required=True, help="BBB major/minor version, e.g. 2.7 or 3.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(generate(args.source, args.output, args.version))


if __name__ == "__main__":
    main()
