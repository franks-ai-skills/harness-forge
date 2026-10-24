# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""The output shape each worker CLI enforces: `claude --json-schema`, `codex exec --output-schema`.

Derived from Forge's own schemas but structural only (types, enums, required
fields, no extra fields), because the CLIs accept a subset of JSON Schema
and Codex wants every property required. Forge still validates the answer
against its full schema afterwards. A research finding leaves its id, pass,
part and brief digest to Forge, which knows them for the brief it sent, and
adds the requirement a re-check finding is for.
"""

from __future__ import annotations

from harness_forge.documents import schema_named

_KEPT = ("type", "properties", "required", "additionalProperties", "items", "enum")
_STAMPED = ("id", "pass", "part_id", "brief_digest")


def _structural(schema: dict) -> dict:
    value = {key: schema[key] for key in _KEPT if key in schema}
    if "properties" in value:
        value["properties"] = {name: _structural(item) for name, item in value["properties"].items()}
        value["required"] = list(value["properties"])
        value["additionalProperties"] = False
    if "items" in value:
        value["items"] = _structural(value["items"])
    return value


def output_schema(role: str) -> dict:
    if role == "research":
        finding = dict(schema_named("research")["definitions"]["finding"])
        finding["properties"] = {name: item for name, item in finding["properties"].items() if name not in _STAMPED}
        # Every property is required, so a finding names its re-check requirement, or null outside a re-check.
        finding["properties"]["requirement_id"] = {"type": ["string", "null"]}
        return _structural({"type": "object", "properties": {"findings": {"type": "array", "items": finding}}})
    return _structural(schema_named("source-support" if role == "source-support" else "review"))
