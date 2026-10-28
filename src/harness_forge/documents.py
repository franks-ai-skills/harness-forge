# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Bounded loading, closed-schema validation and canonical digests.

Every document Forge reads goes through ``load_document``: a size and
nesting limit, a strict YAML loader (no YAML 1.1 booleans, timestamps,
duplicate keys, aliases or Python tags) and a closed JSON Schema with no
remote references. Schemas are Draft-07, validated with the bundled
fastjsonschema; packaging/fastjsonschema-audit.md says why every schema
passes ``schema_problem`` before it is compiled.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import math
import re
import types
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import fastjsonschema
import yaml
from markdown_it import MarkdownIt
from markdown_it import helpers as _helpers
from markdown_it.helpers.parse_link_destination import _Result
from markdown_it.rules_block.lheading import lheading as _lheading_rule
from markdown_it.rules_block.paragraph import paragraph as _paragraph_rule
from markdown_it.rules_inline.html_inline import isLinkClose, isLinkOpen

from harness_forge.bundle import VENDOR

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_ERRORS = 50

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_PREFIX = "urn:harness-forge:schema:"
DRAFT7 = "http://json-schema.org/draft-07/schema#"
_DRAFT7_IDS = (DRAFT7, "http://json-schema.org/draft-07/schema")
_VALIDATION_ERRORS = (fastjsonschema.JsonSchemaValueException, fastjsonschema.JsonSchemaValuesException)
# Keywords whose value is a subschema, a list of subschemas or a map of names to subschemas.
_SUBSCHEMA = ("items", "additionalItems", "contains", "additionalProperties", "propertyNames", "if", "then",
              "else", "not", "allOf", "anyOf", "oneOf")
_SUBSCHEMA_MAPS = ("properties", "patternProperties", "definitions")
KB_SCHEMAS = ("rule", "rules", "predicate", "catalog", "selection", "coverage")

_AMBIGUOUS_BOOL = re.compile(r"^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF)$")
_YAML_12_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader without YAML 1.1 booleans, timestamps or duplicate keys."""


_StrictLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _YAML_12_BOOL, list("tTfF"))


def _construct_str(loader: _StrictLoader, node: yaml.Node) -> str:
    value = loader.construct_scalar(node)
    if node.style is None and _AMBIGUOUS_BOOL.match(value):
        raise ValueError(f"ambiguous YAML scalar {value!r}; quote it or use true/false")
    return value


def _construct_mapping(loader: _StrictLoader, node: yaml.Node, deep: bool = False) -> dict:
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise ValueError(f"duplicate YAML key {key!r}")
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor("tag:yaml.org,2002:str", _construct_str)
_StrictLoader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)


def parse_yaml(text: str) -> Any:
    """Parse one YAML document under Forge's strict rules."""
    depth = 0
    try:
        for event in yaml.parse(text, Loader=_StrictLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("YAML anchors and aliases are not allowed")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError(f"YAML nesting deeper than {MAX_DEPTH}")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        return yaml.load(text, Loader=_StrictLoader)
    except (yaml.YAMLError, TypeError) as error:  # TypeError: an unhashable key, such as a sequence
        raise ValueError(f"invalid YAML: {error}") from error


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError(f"JSON nesting deeper than {MAX_DEPTH}")
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        _json_depth(child, depth + 1)


def parse_json(text: str) -> Any:
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error
    _json_depth(value)
    return value


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return the YAML frontmatter and the body of a Markdown record."""
    if not text.startswith("---\n"):
        raise ValueError("Markdown record has no YAML frontmatter")
    end = text.find("\n---\n", 3)
    if end == -1:
        raise ValueError("Markdown frontmatter is not closed")
    return text[4 : end + 1], text[end + 5 :]


@lru_cache(maxsize=1)
def _metaschema() -> tuple[dict, Any]:
    """The bundled Draft-07 metaschema and its validator; formats on, so regular expressions are checked."""
    meta = parse_json((VENDOR / "draft-07.schema.json").read_text(encoding="utf-8"))
    return meta, fastjsonschema.compile(meta, use_default=False, use_formats=True)


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return all(_finite(child) for child in children)


_NO_KEY = object()


def _non_string_key(value: Any) -> Any:
    """The first mapping key that is not a string, such as YAML's null or 7, else _NO_KEY."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                return key
            found = _non_string_key(child)
            if found is not _NO_KEY:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _non_string_key(child)
            if found is not _NO_KEY:
                return found
    return _NO_KEY


def schema_problem(schema: Any, *, own_ids: frozenset[str] | None = None) -> str | None:
    """Why ``schema`` must not be compiled, or None (packaging/fastjsonschema-audit.md).

    ``own_ids`` is set only for Forge's own schemas, which alone may declare
    ``$id``; references must name one of Forge's schema ids or a local
    ``#`` fragment.
    """
    keys = _non_string_key(schema)
    if keys is not _NO_KEY:
        return f"schema object keys must be strings, found {keys!r}"
    meta, check = _metaschema()
    try:
        check(schema)
    except _VALIDATION_ERRORS as error:
        return "not a valid Draft-07 schema: " + "; ".join(_messages(error)[:3])
    if not _finite(schema):
        return "a number in the schema is not finite"
    if isinstance(schema, dict) and schema.get("$schema", DRAFT7) not in _DRAFT7_IDS:
        return f"$schema {schema['$schema']!r} is not Draft-07; Forge validates Draft-07 only"
    ids = own_ids if own_ids is not None else frozenset(_schemas_by_id())
    vocabulary = set(meta["properties"])
    problems: list[str] = []

    def walk(node: Any, where: str) -> None:
        if not isinstance(node, dict):
            return
        unknown = sorted(set(node) - vocabulary)
        if unknown:
            problems.append(f"{where}: keywords outside Draft-07: {', '.join(unknown)}")
        if where != "(root)" and "$schema" in node:
            problems.append(f"{where}: $schema is allowed only at the root")
        if "$id" in node and own_ids is None:
            problems.append(f"{where}: only Forge's own schemas declare $id")
        reference = node.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#") and reference.split("#", 1)[0] not in ids:
            problems.append(f"{where}: $ref {reference!r} is not one of Forge's schemas")
        dependencies = node.get("dependencies", {})
        names = [*node.get("properties", {}), *node.get("required", []), *dependencies,
                 *(name for value in dependencies.values() if isinstance(value, list) for name in value)]
        braced = sorted({name for name in names if "{" in name or "}" in name})
        if braced:
            problems.append(f"{where}: property names must not contain braces: {', '.join(map(repr, braced))}")
        for key in _SUBSCHEMA:
            value = node.get(key)
            for index, child in enumerate(value) if isinstance(value, list) else ((None, value),):
                walk(child, f"{where}/{key}" + (f"/{index}" if index is not None else ""))
        for key in _SUBSCHEMA_MAPS:
            for name, child in node.get(key, {}).items():
                walk(child, f"{where}/{key}/{name}")
        for name, child in dependencies.items():
            walk(child, f"{where}/dependencies/{name}")

    walk(schema, "(root)")
    return problems[0] if problems else None


@lru_cache(maxsize=1)
def _schemas_by_id() -> dict[str, dict]:
    schemas = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = parse_json(path.read_text(encoding="utf-8"))
        name = path.name.removesuffix(".schema.json")
        if schema.get("$id") != SCHEMA_PREFIX + name:
            raise RuntimeError(f"{path.name}: $id must be {SCHEMA_PREFIX + name}")
        schemas[schema["$id"]] = schema
    ids = frozenset(schemas)
    for schema_id, schema in schemas.items():
        problem = schema_problem(schema, own_ids=ids)
        if problem:
            raise RuntimeError(f"{schema_id}: {problem}")
    return schemas


def _schemas() -> dict[str, dict]:
    return {schema_id.removeprefix(SCHEMA_PREFIX): schema for schema_id, schema in _schemas_by_id().items()}


def schema_names() -> list[str]:
    return sorted(_schemas())


def schema_named(name: str) -> dict:
    """One Forge schema, for checking a value against a part of it."""
    return _schemas()[name]


class _LocalOnly(dict):
    """fastjsonschema's reference handlers: every URI scheme is claimed, so it never falls back to
    urllib.request.urlopen, and only Forge's own schema ids resolve."""

    def __contains__(self, scheme: object) -> bool:
        return True

    def __getitem__(self, scheme: str):
        return _resolve_schema


def _resolve_schema(uri: str) -> dict:
    schema = _schemas_by_id().get(uri.split("#", 1)[0])
    if schema is None:
        raise ValueError(f"schema reference {uri!r} is not one of Forge's schemas")
    # fastjsonschema's resolver rewrites the references of what it is given, so it gets a copy.
    return json.loads(json.dumps(schema))


@lru_cache(maxsize=256)
def _compiled(canonical: str):
    schema = json.loads(canonical)  # a fresh object: compiling rewrites its references
    registered = _schemas_by_id().get(schema.get("$id")) if isinstance(schema, dict) else None
    # Only Forge's registered schema itself is treated as Forge's own; naming its $id is not enough.
    own = frozenset(_schemas_by_id()) if registered is not None and \
        json.dumps(registered, sort_keys=True) == canonical else None
    problem = schema_problem(schema, own_ids=own)
    if problem:
        raise ValueError(f"schema refused: {problem}")
    if isinstance(schema, dict):
        # fastjsonschema picks its generator from $schema and defaults to a later draft: pin Draft-07.
        schema = {**schema, "$schema": DRAFT7}
    try:
        return fastjsonschema.compile(schema, handlers=_LocalOnly(), use_default=False, use_formats=False,
                                      fast_fail=False)
    except Exception as error:  # fastjsonschema raises several types for schemas it cannot compile
        raise ValueError(f"schema cannot be compiled: {type(error).__name__}: {str(error)[:300]}") from error


def _messages(error: Exception) -> list[str]:
    """``location: message`` lines, location relative to the checked value (``(root)`` for itself)."""
    lines = set()
    for item in getattr(error, "errors", None) or [error]:
        name = getattr(item, "name", None) or "data"
        location = (name[4:] if name.startswith("data") else name).lstrip(".") or "(root)"
        message = str(getattr(item, "message", item))
        message = message[len(name):].strip() if message.startswith(name) else message
        lines.add(f"{location}: {message[:300]}")
    return sorted(lines)


def schema_errors(value: Any, schema: dict) -> list[str]:
    keys = _non_string_key(schema)
    if keys is not _NO_KEY:
        raise ValueError(f"schema refused: schema object keys must be strings, found {keys!r}")
    validator = _compiled(json.dumps(schema, sort_keys=True))
    try:
        validator(value)
    except _VALIDATION_ERRORS as error:
        return _messages(error)[:MAX_ERRORS]
    return []


def validate(value: Any, schema: str) -> None:
    """Raise ValueError unless ``value`` satisfies the named Forge schema."""
    schemas = _schemas()
    if schema not in schemas:
        raise ValueError(f"unknown schema {schema!r}")
    messages = schema_errors(value, schemas[schema])
    if messages:
        raise ValueError(f"{schema}: " + "; ".join(messages))


def _read_bounded(path: Path, root: Path | None) -> str:
    if ".." in path.parts:
        raise ValueError(f"{path}: parent-directory segments are not allowed")
    if path.is_symlink():
        raise ValueError(f"{path}: symbolic links are not allowed")
    if root is not None and not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{path}: outside {root}")
    if not path.is_file():
        raise ValueError(f"{path}: not a regular file")
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{path}: larger than {MAX_DOCUMENT_BYTES} bytes")
    data = path.read_bytes()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{path}: larger than {MAX_DOCUMENT_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{path}: not UTF-8") from error


def safe_path(root: Path, relative: str) -> Path:
    """root/relative for writing: no parent segments, no linked directory on the way."""
    root = Path(root).resolve()
    parts = relative.split("/")
    if relative.startswith("/") or ".." in parts or "" in parts:
        raise ValueError(f"{relative}: not a relative path inside {root}")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{current.relative_to(root)} is a symbolic link; refusing to go through it")
    if not current.resolve().is_relative_to(root):
        raise ValueError(f"{relative} escapes {root}")
    return current


def load_document(path: Path, schema: str, *, root: Path | None = None) -> Any:
    """Load a YAML, JSON or Markdown-frontmatter document and validate it.

    ``root``, when given, must contain the resolved path.
    """
    path = Path(path)
    if schema not in _schemas():
        raise ValueError(f"unknown schema {schema!r}")
    text = _read_bounded(path, root)
    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            value = parse_yaml(text)
        elif suffix == ".json":
            value = parse_json(text)
        elif suffix == ".md":
            value = parse_yaml(split_frontmatter(text)[0])
        else:
            raise ValueError(f"unsupported document type {suffix!r}")
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    try:
        validate(value, schema)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    return value


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"not canonical JSON: {error}") from error


def canonical_digest(value: Any) -> str:
    """sha256 of canonical JSON: sorted keys, compact separators, UTF-8."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- Harness KB validation -------------------------------------------------

_CAPPED_EVIDENCE = ("Advisory", "Practitioner", "Inference")


def heading_anchors(text: str, levels: range = range(1, 7)) -> set[str]:
    """GitHub-style anchors of Markdown headings outside code fences."""
    anchors = set()
    for _, level, title in markdown_headings(text):
        if level in levels:
            anchors.add(re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-"))
    return anchors


def predicate_facts(predicate: dict) -> list[tuple[str, list]]:
    """Return (fact, compared values) pairs used by a predicate."""
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        return [pair for item in operand for pair in predicate_facts(item)]
    if operator == "not":
        return predicate_facts(operand)
    fact, value = operand
    return [(fact, value if operator == "in" else [value])]


class _KbCheck:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.errors: list[str] = []
        self._anchors: dict[str, set[str] | None] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def load(self, relative: str, schema: str) -> Any:
        try:
            return load_document(self.root / relative, schema, root=self.root)
        except ValueError as error:
            self.error(str(error).replace(str(self.root) + "/", ""))
            return None

    def page_anchors(self, page: str) -> set[str] | None:
        if page not in self._anchors:
            path = self.root / page
            try:
                inside = path.resolve().is_relative_to(self.root.resolve())
                self._anchors[page] = (
                    heading_anchors(_read_bounded(path, self.root)) if inside and path.is_file() else None
                )
            except UnreadableMarkdown as error:
                self.error(f"{page}: {error}")
                self._anchors[page] = None
            except ValueError:
                self._anchors[page] = None
        return self._anchors[page]

    def check_source(self, owner: str, source: str) -> None:
        if source.startswith("https://"):
            return
        page, _, anchor = source.partition("#")
        anchors = self.page_anchors(page)
        if anchors is None:
            self.error(f"{owner}: cited page {page} is not in the KB")
        elif anchor and anchor not in anchors:
            self.error(f"{owner}: cited section {page}#{anchor} does not exist")

    def check_predicates(self, owner: str, predicates: list[dict], facts: dict[str, list]) -> None:
        for predicate in predicates:
            for fact, values in predicate_facts(predicate):
                if fact not in facts:
                    self.error(f"{owner}: predicate uses undeclared fact {fact!r}")
                    continue
                for value in values:
                    if value not in facts[fact]:
                        self.error(f"{owner}: fact {fact!r} has no value {value!r}")


def validate_kb(root: Path) -> list[str]:
    """Check a Harness KB checkout or snapshot; return its errors."""
    check = _KbCheck(Path(root).resolve())
    root = check.root
    if not root.is_dir():
        return [f"{root}: not a directory"]

    for name in KB_SCHEMAS:
        copy = root / "schemas" / f"{name}.schema.json"
        if copy.is_symlink() or not copy.is_file():
            # Every schema copy is required: a missing one would also be missing from a pinned snapshot.
            check.error(f"schemas/{copy.name}: missing, or not a regular file")
        elif copy.read_bytes() != (SCHEMA_DIR / copy.name).read_bytes():
            check.error(f"schemas/{copy.name}: differs from Forge's schema")

    catalog = check.load("checks/catalog.yaml", "catalog") or {"checkers": []}
    checkers = {}
    for checker in catalog["checkers"]:
        if checker["id"] in checkers:
            check.error(f"checks/catalog.yaml: duplicate checker id {checker['id']}")
        problem = schema_problem(checker["params"])
        if problem:
            check.error(f"checks/catalog.yaml: {checker['id']}: invalid params schema: {problem[:200]}")
            continue
        checkers[checker["id"]] = checker

    selection = check.load("rules/selection.yaml", "selection") or {"facts": [], "questions": [], "rules": []}
    facts = {fact["id"]: fact["values"] for fact in selection["facts"]}
    for question in selection["questions"]:
        if question["id"] not in facts:
            check.error(f"rules/selection.yaml: question {question['id']} is not a declared fact")
        else:
            for choice in question["choices"]:
                if choice not in facts[question["id"]]:
                    check.error(f"rules/selection.yaml: question {question['id']} offers undeclared value {choice!r}")

    rule_files = {"rules/selection.yaml": selection["rules"]}
    for path in sorted((root / "rules").glob("*.yaml")):
        relative = path.relative_to(root).as_posix()
        if relative in ("rules/selection.yaml", "rules/coverage.yaml"):
            continue
        rule_files[relative] = check.load(relative, "rules") or []

    seen: dict[str, str] = {}
    prerequisites: list[tuple[str, str]] = []
    for relative, rules in rule_files.items():
        for rule in rules:
            rule_id = rule["id"]
            owner = f"{relative}: {rule_id}"
            if rule_id in seen:
                check.error(f"{owner}: duplicate rule id (also in {seen[rule_id]})")
            seen[rule_id] = relative
            if not rule_id.startswith(rule["concept"] + "."):
                check.error(f"{owner}: id must start with its concept {rule['concept']!r}")
            if rule["severity"] == "error" and rule["evidence"] in _CAPPED_EVIDENCE and "severity_rationale" not in rule:
                check.error(f"{owner}: {rule['evidence']} evidence caps severity at warning without severity_rationale")
            is_selection = relative == "rules/selection.yaml"
            if rule["check"] == "script":
                checker = checkers.get(rule["checker"])
                if checker is None:
                    check.error(f"{owner}: checker {rule['checker']} is not in the catalog")
                elif (rule["checker"] == "selection.match") != is_selection:
                    check.error(f"{owner}: selection.match is reserved for rules/selection.yaml")
                else:
                    params = rule.get("params", {})
                    messages = schema_errors(params, checker["params"])
                    if messages:
                        check.error(f"{owner}: invalid params for {rule['checker']}: {'; '.join(messages)}")
                    elif is_selection:
                        check.check_predicates(owner, [params["when"]], facts)
                        control = params["implies"].get("control")
                        if isinstance(control, dict):
                            prerequisites.extend((owner, rule_id) for rule_id in control.get("prerequisites", []))
            elif is_selection:
                check.error(f"{owner}: selection rules must use checker selection.match")
            check.check_predicates(owner, rule["applies_to"].get("when", []), facts)
            for source in rule["sources"]:
                check.check_source(owner, source)
            for position in rule.get("positions", []):
                for source in position["sources"]:
                    check.check_source(owner, source)

    for owner, rule_id in prerequisites:
        if rule_id not in seen:
            check.error(f"{owner}: control prerequisite {rule_id} is not a rule")

    preference_ids = [rule["id"] for rule in selection["rules"]
                      if isinstance(rule.get("params", {}).get("implies"), dict) and "concept" in rule["params"]["implies"]]
    if preference_ids:
        policy = root / "policy" / "selection.md"
        policy_text = _read_bounded(policy, root) if policy.is_file() else None
        if policy_text is None:
            check.error("policy/selection.md: missing; it documents the preference order")
        else:
            for rule_id in preference_ids:
                if f"`{rule_id}`" not in policy_text:
                    check.error(f"policy/selection.md: preference rule {rule_id} has no documented rationale")

    coverage = check.load("rules/coverage.yaml", "coverage") or {"pages": []}
    covered_rules = set()
    for page in coverage["pages"]:
        if check.page_anchors(page["page"]) is None:
            check.error(f"rules/coverage.yaml: page {page['page']} is not in the KB")
            continue
        sections = {section["section"] for section in page["sections"]}
        page_text = _read_bounded(root / page["page"], root)
        for anchor in sorted(heading_anchors(page_text, range(2, 4)) - sections):
            check.error(f"rules/coverage.yaml: uncovered section {page['page']}#{anchor}")
        for section in page["sections"]:
            check.check_source("rules/coverage.yaml", f"{page['page']}#{section['section']}")
            for item in section["items"]:
                for rule_id in item.get("rules", []):
                    covered_rules.add(rule_id)
                    if rule_id not in seen:
                        check.error(f"rules/coverage.yaml: {page['page']}#{section['section']}: unknown rule {rule_id}")
    for rule_id, relative in seen.items():
        if relative != "rules/selection.yaml" and rule_id not in covered_rules:
            check.error(f"{relative}: rule {rule_id} is not in rules/coverage.yaml")
    return check.errors


_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r|$)")
# Block and inline nesting beyond this is not parsed: markdown-it skips the rest of that container.
_MAX_NESTING = 20
_CODE_BLOCKS = ("fence", "code_block", "html_block")
_CACHED_BYTES = 64 * 1024

# markdown-it-py's HTML patterns use Python's Unicode \s and re.IGNORECASE, so it read </scrıpt> as </script>
# and a form feed as whitespace, and it starts a declaration only with an uppercase letter. Forge replaces its
# two HTML rules with CommonMark 0.31.2's: ASCII tag names, spaces and tabs (inline also line endings).
_BLOCK_TAGS = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|"
               "dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|"
               "hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
               "section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul")
_RAW_TAGS = "pre|script|style|textarea"
_ATTRIBUTE = r"""(?:[ \t\n]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t\n]*=[ \t\n]*(?:[^ \t\n"'=<>`]+|'[^']*'|"[^"]*"))?)"""
_OPEN_TAG = rf"<[A-Za-z][A-Za-z0-9-]*{_ATTRIBUTE}*[ \t\n]*/?>"
_CLOSE_TAG = r"</[A-Za-z][A-Za-z0-9-]*[ \t\n]*>"
# Start pattern, end pattern (None: a blank line) and whether the block can interrupt a paragraph.
_HTML_BLOCKS = (
    (re.compile(rf"<(?:{_RAW_TAGS})(?:[ \t>]|$)", re.I | re.A), re.compile(rf"</(?:{_RAW_TAGS})>", re.I | re.A), True),
    (re.compile(r"<!--"), re.compile(r"-->"), True),
    (re.compile(r"<\?"), re.compile(r"\?>"), True),
    (re.compile(r"<![A-Za-z]"), re.compile(r">"), True),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>"), True),
    (re.compile(rf"</?(?:{_BLOCK_TAGS})(?:[ \t]|/?>|$)", re.I | re.A), None, True),
    (re.compile(rf"(?!<(?:{_RAW_TAGS})(?![A-Za-z0-9-]))(?:{_OPEN_TAG}|{_CLOSE_TAG})[ \t]*$", re.I | re.A), None, False),
)
_INLINE_TAG = re.compile(rf"{_OPEN_TAG}|{_CLOSE_TAG}", re.A)
_TOO_DEEP = "forge_too_deep"


def _html_block(state, start: int, end: int, silent: bool) -> bool:
    """markdown-it-py's html_block rule with CommonMark 0.31.2's start and end conditions."""
    position = state.bMarks[start] + state.tShift[start]
    if state.is_code_block(start) or state.src[position:position + 1] != "<":
        return False
    text = state.src[position:state.eMarks[start]]
    kind = next((kind for kind in _HTML_BLOCKS if kind[0].match(text)), None)
    if kind is None:
        return False
    if silent:
        return kind[2]
    closer = kind[1]
    line = start + 1
    if not (closer.search(text) if closer else False):
        while line < end and state.sCount[line] >= state.blkIndent:
            text = state.src[state.bMarks[line] + state.tShift[line]:state.eMarks[line]]
            if closer.search(text) if closer else not text:
                line += bool(text)
                break
            line += 1
    state.line = line
    token = state.push("html_block", "", 0)
    token.map = [start, line]
    token.content = state.getLines(start, line, state.blkIndent, True)
    return True


def _find(state, needle: str, start: int) -> int:
    """``state.src.find(needle, start)``, remembered per inline text: positions only grow, so an unfinished
    comment or instruction is searched for once, not once per opener."""
    cache = state.env.setdefault("forge_inline_finds", {})
    known = cache.get(needle)
    if known is not None and known[0] is state.src and known[1] <= start and (known[2] == -1 or known[2] >= start):
        return known[2]
    found = state.src.find(needle, start)
    cache[needle] = (state.src, start, found)
    return found


def _html_inline(state, silent: bool) -> bool:
    """markdown-it-py's html_inline rule with CommonMark 0.31.2's raw HTML, found in linear time."""
    source, position, maximum = state.src, state.pos, state.posMax
    if source[position] != "<" or position + 2 >= maximum:
        return False
    end = -1
    if source.startswith(("<!-->", "<!--->"), position):
        end = position + (5 if source.startswith("<!-->", position) else 6)
    elif source.startswith("<!--", position):
        end = _find(state, "-->", position + 4) + 3
    elif source.startswith("<?", position):
        end = _find(state, "?>", position + 2) + 2
    elif source.startswith("<![CDATA[", position):
        end = _find(state, "]]>", position + 9) + 3
    elif re.match(r"<![A-Za-z]", source[position:position + 3]):
        end = _find(state, ">", position + 2) + 1
    elif match := _INLINE_TAG.match(source, position, maximum):
        end = match.end()
    if end <= position + 2 or end > maximum:
        return False
    if not silent:
        token = state.push("html_inline", "", 0)
        token.content = source[position:end]
        state.linkLevel += isLinkOpen(token.content)
        state.linkLevel -= isLinkClose(token.content)
    state.pos = end
    return True


_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
# Backslash escapes of ASCII punctuation and entity references, as CommonMark 0.31.2 resolves them.
_ESCAPE_OR_ENTITY = re.compile(r"\\([!-/:-@\[-`{-~])|&(?:#([0-9]{1,7})|#[xX]([0-9a-fA-F]{1,6})|([A-Za-z][A-Za-z0-9]{0,31}));")


def _resolve(text: str) -> str:
    """Escapes and entity references resolved; a numeric reference to 0, a surrogate or past U+10FFFF is U+FFFD."""
    def replace(match: re.Match) -> str:
        if match.group(1):
            return match.group(1)
        if match.group(4):
            return html.entities.html5.get(match.group(4) + ";", match.group(0))
        code = int(match.group(2), 10) if match.group(2) else int(match.group(3), 16)
        return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "\ufffd"
    return _ESCAPE_OR_ENTITY.sub(replace, text)


def _destination(text: str, position: int, maximum: int) -> _Result:
    """CommonMark 0.31.2's link destination: a backslash escapes ASCII punctuation only, so it cannot carry a
    control character or a line ending into the destination (markdown-it-py lets it escape any character)."""
    result, start = _Result(), position
    if text[position:position + 1] == "<":
        position += 1
        while position < maximum:
            character = text[position]
            if character in "\n<":
                return result
            if character == ">":
                result.ok, result.pos, result.str = True, position + 1, _resolve(text[start + 1:position])
                return result
            position += 2 if character == "\\" and text[position + 1:position + 2] in _PUNCTUATION else 1
        return result
    depth = 0
    while position < maximum:
        character = text[position]
        if ord(character) <= 0x20 or character == "\x7f":
            break
        if character == "\\" and position + 1 < maximum and text[position + 1] in _PUNCTUATION:
            position += 2
            continue
        if character == "(":
            depth += 1
            if depth > 32:
                return result
        elif character == ")":
            if depth == 0:
                break
            depth -= 1
        position += 1
    if position > start and depth == 0:
        result.ok, result.pos, result.str = True, position, _resolve(text[start:position])
    return result


def _blank(text: str, position: int, line_ending: bool) -> int:
    """Past spaces and tabs, and with ``line_ending`` past one line ending and the spaces and tabs after it."""
    while position < len(text) and text[position] in " \t":
        position += 1
    if line_ending and text[position:position + 1] == "\n":
        position = _blank(text, position + 1, False)
    return position


def _definition(text: str, position: int) -> tuple[int, str, str, str] | None:
    """(end, label, destination, title) of the link reference definition at ``position``, or None."""
    if text[position:position + 1] != "[":
        return None
    index = position + 1
    while index < len(text) and text[index] != "]":
        if text[index] == "[":
            return None
        index += 2 if text[index] == "\\" and text[index + 1:index + 2] in _PUNCTUATION else 1
    label = text[position + 1:index]
    if index >= len(text) or text[index + 1:index + 2] != ":" or len(label) > 999 or not label.strip(" \t\n"):
        return None
    start = _blank(text, index + 2, True)
    destination = _destination(text, start, len(text))
    if not destination.ok:
        return None
    after = destination.pos
    title_start = _blank(text, after, True)
    if title_start > after and title_start < len(text):
        title = _helpers.parseLinkTitle(text, title_start, len(text), None)
        end = _blank(text, title.pos, False) if title.ok else -1
        if title.ok and text[end:end + 1] in ("", "\n"):
            return end + 1, label, destination.str, _resolve(text[title_start + 1:title.pos - 1])
    end = _blank(text, after, False)
    if text[end:end + 1] in ("", "\n"):
        return end + 1, label, destination.str, ""
    return None


def _definitions(state) -> None:
    """Core rule before inline parsing: CommonMark takes link reference definitions from the start of each
    paragraph, so they keep the paragraph's container, lazy continuation and end (markdown-it-py's block rule
    ended the paragraph at a definition). The first definition of a label wins."""
    references = state.env.setdefault("references", {})
    for index, token in enumerate(state.tokens):
        opener = state.tokens[index - 1] if index else None
        if token.type != "inline" or opener is None or not (
                opener.type == "paragraph_open" or (opener.type == "heading_open" and opener.markup in ("=", "-"))):
            continue
        # CommonMark's raw paragraph content drops each line's initial spaces and tabs; markdown-it keeps them.
        token.content = "\n".join(line.lstrip(" \t") for line in token.content.split("\n"))
        position = 0
        while found := _definition(token.content, position):
            position, label, destination, title = found
            references.setdefault(_normalize_label(label), {"href": destination, "title": title})
        if position and opener.type == "heading_open" and not token.content[position:].strip(" \t\n"):
            # CommonMark reads the underline as text then; markdown-it-py made the definition a heading.
            raise UnreadableMarkdown(f"line {opener.map[0] + 1}: a setext underline below only link reference "
                                     "definitions")
        # The block now starts below its definitions, one source line per content line.
        consumed = token.content.count("\n", 0, position)
        for block in (opener, token):
            if block.map and consumed:
                block.map = [block.map[0] + consumed, block.map[1]]
        token.content = token.content[position:]


def _normalize_label(label: str) -> str:
    """CommonMark's label normalization: Unicode case fold, spaces, tabs and line endings trimmed and collapsed.
    markdown-it-py collapses Python's Unicode whitespace and folds with lower().upper(), which matches ı with I."""
    return re.sub(r"[ \t\n]+", " ", label.casefold().strip(" \t\n"))


def _ascii_trimmed(rule):
    """markdown-it-py's paragraph or setext rule with its content trimmed of spaces, tabs and line endings only:
    its Unicode strip() also removed a non-breaking space, which CommonMark keeps (and which can keep text
    from being a link reference definition)."""
    def trimmed(state, start: int, end: int, silent: bool) -> bool:
        count = len(state.tokens)
        matched = rule(state, start, end, silent)
        if matched and not silent and len(state.tokens) >= count + 2:
            opener, inline = state.tokens[count], state.tokens[count + 1]
            last = state.line if opener.type == "paragraph_open" else state.line - 1  # a setext underline
            inline.content = state.getLines(start, last, state.blkIndent, False).strip(" \t\n")
        return matched
    return trimmed


def _watch(method):
    """Notes a call at the nesting limit, where markdown-it skips the content instead of parsing it."""
    def watched(state, *arguments):
        if state.level >= _MAX_NESTING:
            state.env[_TOO_DEEP] = True
        return method(state, *arguments)
    return watched


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"maxNesting": _MAX_NESTING})
    # Destinations as written, escapes resolved: a relative link is a file path, and Forge classifies schemes.
    parser.normalizeLink = lambda url: url
    parser.validateLink = lambda url: True
    # The same alternative chains as markdown-it's own rule: an HTML block can interrupt these.
    parser.block.ruler.at("html_block", _html_block, {"alt": ["paragraph", "reference", "blockquote"]})
    parser.block.ruler.disable("reference")
    for name, rule in (("paragraph", _paragraph_rule), ("lheading", _lheading_rule)):
        parser.block.ruler.at(name, _ascii_trimmed(rule))
    parser.core.ruler.before("inline", "forge_definitions", _definitions)
    parser.helpers = types.SimpleNamespace(parseLinkLabel=_helpers.parseLinkLabel, parseLinkDestination=_destination,
                                           parseLinkTitle=_helpers.parseLinkTitle)
    parser.inline.ruler.at("html_inline", _html_inline)
    # Definitions and the links that use them share one normalization; markdown-it-py's link and image rules
    # look it up by name in their modules (which the package shadows with the rule functions, hence importlib).
    for name in ("markdown_it.rules_inline.link", "markdown_it.rules_inline.image"):
        importlib.import_module(name).normalizeReference = _normalize_label
    parser.block.tokenize = _watch(parser.block.tokenize)
    parser.inline.tokenize = _watch(parser.inline.tokenize)
    parser.inline.skipToken = _watch(parser.inline.skipToken)
    return parser


def fold_name(name: str) -> str:
    """A file name folded at least as widely as a case- and normalization-insensitive file system folds it
    (ſ is s, fullwidth Ａ is a): names equal after folding can open the same file."""
    return unicodedata.normalize("NFKC", name).casefold()


class UnreadableMarkdown(ValueError):
    """Text markdown-it-py would read differently from CommonMark or not at all; checks fail and writers refuse on it."""


_PARSER = _markdown_parser()


def _parse(markdown: str) -> tuple[tuple, dict]:
    """CommonMark tokens and reference definitions; UnreadableMarkdown where markdown-it-py would misread."""
    if len(markdown) <= _CACHED_BYTES:
        return _parse_cached(markdown)
    return _parse_text(markdown)


def _parse_text(markdown: str) -> tuple[tuple, dict]:
    env: dict = {}
    tokens = tuple(_PARSER.parse(markdown, env))
    if env.get(_TOO_DEEP):
        raise UnreadableMarkdown(f"blocks or inline elements nested {_MAX_NESTING} or more levels deep")
    return tokens, env.get("references", {})


_parse_cached = lru_cache(maxsize=32)(_parse_text)
_parse.cache_clear = _parse_cached.cache_clear
_parse.cache_info = _parse_cached.cache_info


def _line_offsets(markdown: str) -> list[tuple[int, str]]:
    """(offset, line without its ending) per line, split as CommonMark splits: at \\r\\n, \\n or \\r."""
    lines, offset = [], 0
    for match in _LINE.finditer(markdown):
        raw = match.group(0)
        if not raw and lines:
            break
        lines.append((offset, raw.rstrip("\r\n")))
        offset += len(raw)
        if not raw:
            break
    return lines


def markdown_lines(markdown: str) -> list[tuple[int, str, bool]]:
    """(offset, line without its ending, inside code) for each line, as CommonMark (markdown-it-py) reads it.

    Code is every line of a fenced or indented code block or an HTML block,
    also inside list items and block quotes.
    """
    tokens, _ = _parse(markdown)
    code = {line for token in tokens if token.type in _CODE_BLOCKS and token.map for line in range(*token.map)}
    return [(offset, line, number in code) for number, (offset, line) in enumerate(_line_offsets(markdown))]


def markdown_prose(markdown: str) -> str:
    """The text with every code line left empty, so that line numbers stay."""
    return "\n".join("" if code else line for _, line, code in markdown_lines(markdown))


def markdown_headings(markdown: str) -> list[tuple[int, int, str]]:
    """(offset, level, text) of each ATX or setext heading outside list items, block quotes and code.

    A heading inside a container is part of that container, not a section of the document.
    """
    tokens, _ = _parse(markdown)
    offsets = [offset for offset, _ in _line_offsets(markdown)]
    return [(offsets[token.map[0]], int(token.tag[1]), tokens[index + 1].content)
            for index, token in enumerate(tokens) if token.type == "heading_open" and token.level == 0]


def markdown_fence_contents(markdown: str) -> list[tuple[int, int]]:
    """(start, end) offsets of each fenced code block's content, as CommonMark parses it, at any nesting level.

    A fence closes only at a line of the opening fence's character, at least as long as it; an unclosed fence
    runs to the end of the text.
    """
    tokens, _ = _parse(markdown)
    lines = _line_offsets(markdown)
    ranges = []
    for token in tokens:
        if token.type != "fence" or not token.map:
            continue
        first, last = token.map
        start = lines[first + 1][0] if first + 1 < len(lines) else len(markdown)
        end = len(markdown)
        if last - 1 > first and last - 1 < len(lines):
            closing = lines[last - 1][1].strip()
            marker = token.markup
            if closing and set(closing) == {marker[0]} and len(closing) >= len(marker):
                end = lines[last - 1][0]
        ranges.append((start, max(start, end)))
    return ranges


def markdown_links(markdown: str) -> list[str]:
    """Destinations of every link, image and reference definition, as written with escapes resolved."""
    tokens, references = _parse(markdown)
    links = [child.attrs.get("href") or child.attrs.get("src") for token in tokens for child in token.children or ()
             if child.type in ("link_open", "image")]
    return [link for link in links if link] + [reference["href"] for reference in references.values()]


def markdown_section(markdown: str, heading: str) -> tuple[int, int] | None:
    """Start and end offsets of the ``## heading`` section: from its heading to the next heading of level two or higher."""
    headings = markdown_headings(markdown)
    start = next((offset for offset, level, text in headings if level == 2 and text == heading), None)
    if start is None:
        return None
    return start, next((offset for offset, level, _ in headings if offset > start and level <= 2), len(markdown))
