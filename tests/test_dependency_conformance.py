"""Conformance of the bundled YAML and JSON Schema libraries as Forge uses them.

These tests assess third-party behavior that Forge relies on; they are not
an independent validator. ``strict_yaml_load`` shows that the bundled YAML
library can be configured to meet the design's scalar rules; the document
loader owns the production configuration. The schema tests go through
Forge's own validation (``schema_problem``, ``schema_errors``) on the
bundled fastjsonschema, including the restrictions and workarounds of
packaging/fastjsonschema-audit.md.
"""

import json
import re
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

import fastjsonschema
import yaml

from harness_forge import documents
from harness_forge.documents import schema_errors, schema_problem

FIXTURES = Path(__file__).parent / "fixtures" / "schema-conformance"

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


def _construct_str(loader, node):
    value = loader.construct_scalar(node)
    if node.style is None and _AMBIGUOUS_BOOL.match(value):
        raise ValueError(f"ambiguous YAML scalar {value!r}; quote it or use true/false")
    return value


def _construct_mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise ValueError(f"duplicate YAML key {key!r}")
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor("tag:yaml.org,2002:str", _construct_str)
_StrictLoader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)


def strict_yaml_load(text: str):
    try:
        return yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as error:
        raise ValueError(str(error)) from error


def _instance(name: str):
    return strict_yaml_load((FIXTURES / "instances" / f"{name}.yaml").read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate_fixture(name: str) -> bool:
    manifest = json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))
    return schema_errors(_instance(name), _schema(manifest[name])) == []


PREDICATE = "urn:harness-forge:schema:predicate"
# Malformed schemas that compiling alone accepts or that only a schema check rejects.
MALFORMED = [{"type": 5}, {"type": "strin"}, {"properties": []}, {"required": "x"}, {"required": [1]},
             {"pattern": "("}, {"minItems": -1}, {"maxLength": "3"}, {"additionalProperties": "no"},
             {"items": 5}, {"enum": "a"}, {"$ref": 5}, {"properties": {"a": 5}}, {"minimum": "1"},
             {"patternProperties": {"(": {}}}, {"uniqueItems": "yes"}, {"oneOf": []}, {"anyOf": {}},
             {"type": "object", "definitions": {"bad": {"required": [1]}}},
             {"type": "array", "items": {"$ref": "#/definitions/n"}, "definitions": {"n": {"minItems": -1}}}]


class SchemaConformanceTest(unittest.TestCase):
    def test_valid_rules_pass(self):
        self.assertTrue(validate_fixture("valid-script-rule"))
        self.assertTrue(validate_fixture("valid-judged-rule"))

    def test_unknown_checker_is_rejected(self):
        self.assertFalse(validate_fixture("unknown-checker"))

    def test_closed_objects_reject_unknown_keys(self):
        self.assertFalse(validate_fixture("unknown-top-level-key"))
        self.assertFalse(validate_fixture("unknown-param"))

    def test_local_reference_constraints_apply(self):
        self.assertFalse(validate_fixture("ref-pattern-violation"))

    def test_conditional_schemas_apply(self):
        self.assertFalse(validate_fixture("judged-rule-with-check"))
        self.assertFalse(validate_fixture("script-rule-without-check"))

    def test_wrong_scalar_types_are_rejected(self):
        self.assertFalse(validate_fixture("wrong-integer-type"))
        self.assertFalse(validate_fixture("float-version"))

    def test_remote_references_are_refused_before_any_retrieval(self):
        with unittest.mock.patch.object(urllib.request, "urlopen",
                                        side_effect=AssertionError("remote retrieval attempted")) as urlopen:
            with self.assertRaisesRegex(ValueError, "is not one of Forge's schemas"):
                validate_fixture("remote-ref")
        urlopen.assert_not_called()

    def test_reference_resolution_never_retrieves_even_without_the_schema_check(self):
        # The handlers claim every scheme, so fastjsonschema never falls back to urlopen (audit).
        with unittest.mock.patch.object(urllib.request, "urlopen",
                                        side_effect=AssertionError("remote retrieval attempted")) as urlopen:
            for reference in ("urn:other:thing", "urn:harness-forge:schema:nope", "http://example.invalid/s",
                              "https://example.invalid/s", "file:///etc/hosts", "data:application/json,{}",
                              "relative/other.json", "//example.invalid/s"):
                schema = {"$schema": documents.DRAFT7, "properties": {"a": {"$ref": reference}}}
                with self.subTest(reference), self.assertRaises(ValueError):
                    fastjsonschema.compile(schema, handlers=documents._LocalOnly(), use_default=False,
                                           use_formats=False)
        urlopen.assert_not_called()
        self.assertIn("ftp", documents._LocalOnly(), "every scheme is claimed")

    def test_a_referenced_schemas_own_references_resolve_against_it(self):
        # fastjsonschema 2.22.2 resolves "#" inside a referenced schema against the outer one; Forge's schemas
        # name themselves absolutely, so the predicate's nested "all" is checked against the predicate.
        open_outer = {"type": "object", "properties": {"p": {"$ref": PREDICATE}}}
        closed_outer = {**open_outer, "additionalProperties": False}
        for outer in (open_outer, closed_outer):
            self.assertNotEqual(schema_errors({"p": {"all": [{"eq": [1, 2, 3]}]}}, outer), [])
            self.assertEqual(schema_errors({"p": {"all": [{"eq": ["job", "x"]}]}}, outer), [])
        self.assertNotIn('"$ref": "#', json.dumps(documents.schema_named("predicate")))

    def test_every_malformed_schema_is_refused_before_compiling(self):
        for schema in MALFORMED:
            with self.subTest(schema):
                self.assertIsNotNone(schema_problem(schema))
                with self.assertRaisesRegex(ValueError, "schema refused"):
                    schema_errors({}, schema)

    def test_keywords_outside_draft_07_are_refused(self):
        for schema in ({"$defs": {"a": {}}}, {"type": "array", "prefixItems": [{"type": "string"}]},
                       {"unevaluatedProperties": False}, {"propertes": {"a": {}}}):
            with self.subTest(schema):
                self.assertIn("keywords outside Draft-07", schema_problem(schema))

    def test_braces_in_property_names_are_refused_but_not_in_patterns(self):
        for schema in ({"properties": {"{x}": {}}}, {"required": ["a}"]}, {"dependencies": {"{a": ["b"]}},
                       {"dependencies": {"a": ["{b}"]}}, {"items": {"properties": {"{data}": {}}}}):
            with self.subTest(schema):
                self.assertIn("must not contain braces", schema_problem(schema))
        self.assertIsNone(schema_problem({"patternProperties": {"^[a-z]{2,3}$": {"type": "string"}}}))

    def test_non_finite_numbers_are_refused(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            self.assertEqual(schema_problem({"minimum": value}), "a number in the schema is not finite")
            self.assertEqual(schema_problem({"enum": [1, value]}), "a number in the schema is not finite")

    def test_only_draft_07_is_validated(self):
        draft_04 = {"$schema": "http://json-schema.org/draft-04/schema#", "properties": {"x": {"const": 1}}}
        self.assertIn("is not Draft-07", schema_problem(draft_04))
        self.assertIn("$schema is allowed only at the root",
                      schema_problem({"properties": {"x": {"$schema": documents.DRAFT7, "type": "string"}}}))
        # Without $schema the schema is still compiled as Draft-07: const applies.
        self.assertNotEqual(schema_errors({"x": 2}, {"properties": {"x": {"const": 1}}}), [])
        self.assertEqual(schema_errors({"x": 1}, {"properties": {"x": {"const": 1}}}), [])

    def test_a_schema_that_names_a_forge_id_is_not_trusted_as_forges(self):
        forged = {**documents.schema_named("rule"), "properties": {"{x}": {}}}
        self.assertIsNotNone(schema_problem(forged))
        with self.assertRaisesRegex(ValueError, "schema refused"):
            schema_errors({}, forged)
        self.assertEqual(schema_errors({}, {**documents.schema_named("predicate")}) != [], True,
                         "the registered schema itself stays trusted")

    def test_non_string_keys_are_refused_clearly(self):
        for schema in ({"properties": {7: {"type": "string"}}}, {"enum": [{1: "a"}]},
                       {"properties": {None: {"type": "string"}}}, {"const": {None: 1}}):
            with self.subTest(schema):
                self.assertIn("schema object keys must be strings", schema_problem(schema))
                with self.assertRaisesRegex(ValueError, "keys must be strings"):
                    schema_errors({}, schema)

    def test_literal_objects_are_not_checked_as_schemas(self):
        self.assertIsNone(schema_problem({"const": {"$defs": 1, "{x}": 2}, "enum": [{"prefixItems": []}],
                                          "default": {"$id": "x"}, "examples": [{"unknown": 1}]}))

    def test_only_forges_own_schemas_declare_an_id(self):
        self.assertIn("only Forge's own schemas declare $id", schema_problem({"$id": "urn:x", "type": "object"}))
        self.assertEqual(schema_errors({"kind": "must"}, documents.schema_named("rule")) != [], True)

    def test_errors_are_relative_sorted_and_bounded(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}, "b.c": {"type": "integer"},
                                                   "list": {"type": "array", "items": {"type": "string"}}},
                  "required": ["z"]}
        self.assertEqual(schema_errors({"a": 1, "b.c": "x", "list": ["ok", 2]}, schema), [
            "(root): must contain ['z'] properties", "a: must be string", "b.c: must be integer",
            "list[1]: must be string"])
        many = {"type": "array", "items": {"type": "string"}}
        errors = schema_errors(list(range(200)), many)
        self.assertEqual(len(errors), documents.MAX_ERRORS)
        self.assertEqual(errors, sorted(errors))
        long_enum = {"enum": ["x" * 400]}
        self.assertLessEqual(len(schema_errors("y", long_enum)[0]), len("(root): ") + 300)

    def test_inputs_are_never_changed(self):
        schema = {"type": "object", "properties": {"a": {"type": "string", "default": "filled"}}}
        value = {"b": 1}
        self.assertEqual(schema_errors(value, schema), [])
        self.assertEqual(value, {"b": 1}, "no default is inserted")

    def test_a_trailing_newline_does_not_satisfy_an_anchored_pattern(self):
        # Stricter than jsonschema, which let Python's $ match before a final newline (#51).
        schema = {"type": "string", "pattern": "^[a-z]+$"}
        self.assertEqual(schema_errors("abc", schema), [])
        self.assertNotEqual(schema_errors("abc\n", schema), [])


class YamlConformanceTest(unittest.TestCase):
    def test_yaml_11_boolean_words_are_rejected(self):
        with self.assertRaises(ValueError):
            _instance("yes-scalar")
        with self.assertRaises(ValueError):
            _instance("norway-scalar")

    def test_quoted_yes_stays_a_string(self):
        self.assertEqual(strict_yaml_load('answer: "yes"'), {"answer": "yes"})

    def test_explicit_booleans_are_json_booleans(self):
        self.assertEqual(_instance("explicit-booleans"), {"enabled": True, "disabled": False})

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            _instance("duplicate-key")

    def test_python_tags_are_rejected(self):
        with self.assertRaises(ValueError):
            _instance("python-tag")

    def test_dates_stay_strings(self):
        self.assertEqual(_instance("date-scalar"), {"fetched": "2026-10-09"})


if __name__ == "__main__":
    unittest.main()
