"""Conformance of the candidate YAML and JSON Schema libraries.

These tests assess third-party behavior that Forge relies on; they are not
an independent validator. ``strict_yaml_load`` shows that the candidate
YAML library can be configured to meet the design's scalar rules; T4's
document loader owns the production configuration.
"""

import json
import re
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

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


def _validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((FIXTURES / "schemas" / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    # An empty registry with no retrieve function: references outside the
    # schema itself are unresolvable instead of fetched.
    return Draft202012Validator(schema, registry=Registry())


def validate_fixture(name: str) -> bool:
    manifest = json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))
    return _validator(manifest[name]).is_valid(_instance(name))


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

    def test_remote_references_are_not_retrieved(self):
        with unittest.mock.patch.object(
            urllib.request, "urlopen", side_effect=AssertionError("remote retrieval attempted")
        ) as urlopen:
            with self.assertRaises(Unresolvable):
                validate_fixture("remote-ref")
        urlopen.assert_not_called()


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
