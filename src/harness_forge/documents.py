"""Bounded loading, closed-schema validation and canonical digests.

Every document Forge reads goes through ``load_document``: a size and
nesting limit, a strict YAML loader (no YAML 1.1 booleans, timestamps,
duplicate keys, aliases or Python tags) and a closed JSON Schema with no
remote references.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_ERRORS = 50

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_PREFIX = "urn:harness-forge:schema:"
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
    except yaml.YAMLError as error:
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
def _schemas() -> tuple[dict[str, dict], Registry]:
    schemas = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = parse_json(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        name = path.name.removesuffix(".schema.json")
        if schema.get("$id") != SCHEMA_PREFIX + name:
            raise RuntimeError(f"{path.name}: $id must be {SCHEMA_PREFIX + name}")
        schemas[name] = schema
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return schemas, registry


def schema_names() -> list[str]:
    return sorted(_schemas()[0])


def schema_errors(value: Any, schema: dict) -> list[str]:
    validator = Draft202012Validator(schema, registry=_schemas()[1])
    messages = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "(root)"
        messages.append(f"{location}: {error.message[:300]}")
        if len(messages) >= MAX_ERRORS:
            break
    return messages


def validate(value: Any, schema: str) -> None:
    """Raise ValueError unless ``value`` satisfies the named Forge schema."""
    schemas = _schemas()[0]
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


def load_document(path: Path, schema: str, *, root: Path | None = None) -> Any:
    """Load a YAML, JSON or Markdown-frontmatter document and validate it.

    ``root``, when given, must contain the resolved path.
    """
    path = Path(path)
    if schema not in _schemas()[0]:
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
    text = re.sub(r"^(```|~~~).*?^\1", "", text, flags=re.S | re.M)
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if match and len(match.group(1)) in levels:
            slug = re.sub(r"[^\w\- ]", "", match.group(2).lower()).strip().replace(" ", "-")
            anchors.add(slug)
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
        if copy.exists() and copy.read_bytes() != (SCHEMA_DIR / copy.name).read_bytes():
            check.error(f"schemas/{copy.name}: differs from Forge's schema")

    catalog = check.load("checks/catalog.yaml", "catalog") or {"checkers": []}
    checkers = {}
    for checker in catalog["checkers"]:
        if checker["id"] in checkers:
            check.error(f"checks/catalog.yaml: duplicate checker id {checker['id']}")
        try:
            Draft202012Validator.check_schema(checker["params"])
        except SchemaError as error:
            check.error(f"checks/catalog.yaml: {checker['id']}: invalid params schema: {error.message[:200]}")
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
