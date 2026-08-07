"""Generate the SBC action reference and typing stub from embedded mutations."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def actions_from_module(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"ACTION_DEFINITIONS_JSON = r'''(?P<json>.*?)'''", source, re.S)
    if not match:
        raise ValueError(f"could not find ACTION_DEFINITIONS_JSON in {path}")
    return json.loads(match.group("json"))["mutations"]


def python_type(graphql_type: str, is_list: bool) -> str:
    basic = {"String": "str", "Int": "int", "Float": "float", "Boolean": "bool", "json": "Any"}
    value = basic.get(graphql_type, "Any")
    return f"list[{value}]" if is_list else value


def render_reference(actions: list[dict[str, Any]]) -> str:
    rows = ["# Generated SBC Action Reference", "", "Generated from the embedded BBB action schema.", ""]
    for action in actions:
        arguments = action["arguments"]
        signature = ", ".join(
            f"{snake_case(arg['name'])}: {python_type(arg['type'], arg['isList'])}"
            + ("" if arg["required"] else " | None = None")
            for arg in arguments
        )
        rows.extend([f"## `{snake_case(action['name'])}({signature})`", "", f"BBB mutation: `{action['name']}`.", ""])
    return "\n".join(rows)


def render_stub(actions: list[dict[str, Any]]) -> str:
    rows = ["# Generated from SBC's embedded BBB action schema.", "from typing import Any", "", "class Actions:"]
    for action in actions:
        arguments = action["arguments"]
        required = [arg for arg in arguments if arg["required"]]
        optional = [arg for arg in arguments if not arg["required"]]
        params = ["self"]
        params.extend(f"{snake_case(arg['name'])}: {python_type(arg['type'], arg['isList'])}" for arg in required)
        params.extend(f"{snake_case(arg['name'])}: {python_type(arg['type'], arg['isList'])} | None = ..." for arg in optional)
        signature = ", ".join(params)
        rows.append(f"    def {snake_case(action['name'])}({signature}) -> dict[str, Any]: ...")
    return "\n".join(rows) + "\n"


def generate(mutations: Path, reference: Path, stub: Path) -> None:
    actions = actions_from_module(mutations)
    reference.parent.mkdir(parents=True, exist_ok=True)
    stub.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text(render_reference(actions), encoding="utf-8")
    stub.write_text(render_stub(actions), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutations", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--stub", type=Path, required=True)
    args = parser.parse_args()
    generate(args.mutations, args.reference, args.stub)


if __name__ == "__main__":
    main()
