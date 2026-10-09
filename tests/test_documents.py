"""Document loading limits, scalar handling, digests and worker evidence."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.documents import (
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    canonical_digest,
    load_document,
    parse_yaml,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures"
EVIDENCE = FIXTURES / "worker-evidence"


def evidence(name: str = "valid") -> dict:
    return json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))


def worker_response(record: dict) -> dict:
    return {
        "role": "source-support",
        "run_id": "run-1",
        "request_digest": "1" * 64,
        "status": "completed",
        "output": {
            "verdicts": [{"finding_id": "F1", "verdict": "supported", "reasoning": "The quote states the claim."}]
        },
        "evidence": record,
    }


def report(record: dict) -> dict:
    return {
        "run_id": "run-1",
        "decision_digest": "2" * 64,
        "contract_digest": "3" * 64,
        "kb_version": "synthetic",
        "idea_digest": "4" * 64,
        "rules_digest": "5" * 64,
        "revision_digest": None,
        "confirmed_parts": [],
        "answers": {},
        "scope": "Extensive review (rarely needed)",
        "worker_violations": [],
        "symlinks": {},
        "overrides": [],
        "parts": [],
        "runtime_results": [],
        "worker_evidence": [record],
        "tool_versions": {"harness-forge": "0.0.0"},
        "file_hashes": {},
    }


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="forge-documents-")
        self.root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class AnswerScalarsAndHashesTest(TemporaryFiles):
    def test_answer_scalars_and_contract_hashes(self):
        first = {"part": "A", "answers": {"trigger": "user", "every_time": "usually"}}
        reordered = {"answers": {"every_time": "usually", "trigger": "user"}, "part": "A"}
        self.assertEqual(canonical_digest(first), canonical_digest(reordered))
        self.assertRegex(canonical_digest(first), r"^[0-9a-f]{64}$")
        changed = copy.deepcopy(first)
        changed["answers"]["trigger"] = "model"
        self.assertNotEqual(canonical_digest(first), canonical_digest(changed))
        with self.assertRaises(ValueError):
            parse_yaml("answers:\n  own_context: yes\n")
        self.assertEqual(parse_yaml('own_context: "yes"\n'), {"own_context": "yes"})
        self.assertEqual(parse_yaml("own_context: true\n"), {"own_context": True})

    def test_digest_rejects_non_json_values(self):
        with self.assertRaises(ValueError):
            canonical_digest({"value": float("nan")})
        with self.assertRaises(ValueError):
            canonical_digest({"value": {1, 2}})


class DocumentLimitsTest(TemporaryFiles):
    def test_document_limits(self):
        duplicate = self.write("rules/duplicate.yaml", "- id: skills.a\n  id: skills.b\n")
        with self.assertRaises(ValueError):
            load_document(duplicate, "rules")

        nested = self.write("deep.yaml", "value: " + "[" * (MAX_DEPTH + 2) + "]" * (MAX_DEPTH + 2) + "\n")
        with self.assertRaises(ValueError):
            parse_yaml(nested.read_text(encoding="utf-8"))

        large = self.write("rules/large.yaml", "# " + "x" * MAX_DOCUMENT_BYTES + "\n[]\n")
        with self.assertRaises(ValueError):
            load_document(large, "rules")

        bad_path = self.root / "rules" / ".." / "rules" / "duplicate.yaml"
        with self.assertRaises(ValueError):
            load_document(bad_path, "rule")

    def test_parse_errors_name_the_document(self):
        path = self.write("rules/anchored.yaml", "- &a {id: x}\n- *a\n")
        with self.assertRaisesRegex(ValueError, "anchored.yaml"):
            load_document(path, "rules")

    def test_symlinked_documents_are_rejected(self):
        target = self.write("outside.yaml", "[]\n")
        link = self.root / "rules" / "link.yaml"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            load_document(link, "rules")

    def test_unknown_schema_name_is_rejected(self):
        path = self.write("rules/empty.yaml", "[]\n")
        with self.assertRaises(ValueError):
            load_document(path, "../rules")

    def test_markdown_frontmatter_is_loaded(self):
        path = self.write("record.md", "---\n- id: skills.a\n  concept: skills\n---\nBody text\n")
        with self.assertRaises(ValueError):
            load_document(path, "rules")
        valid = self.write("valid.md", "---\n[]\n---\nBody text\n")
        self.assertEqual(load_document(valid, "rules"), [])
        no_frontmatter = self.write("plain.md", "Body only\n")
        with self.assertRaises(ValueError):
            load_document(no_frontmatter, "rules")


class WorkerEvidenceTest(unittest.TestCase):
    def test_valid_evidence_validates_in_worker_and_report_schemas(self):
        record = evidence()
        validate(record, "worker-evidence")
        validate(worker_response(record), "worker-response")
        validate(report(record), "report")

    def test_missing_bindings_and_certified_status_are_rejected(self):
        for name in ("missing-policy", "missing-configuration", "certified"):
            record = evidence(name)
            with self.subTest(name=name):
                for schema, value in (
                    ("worker-evidence", record),
                    ("worker-response", worker_response(record)),
                    ("report", report(record)),
                ):
                    with self.assertRaises(ValueError):
                        validate(value, schema)

    def test_unknown_tool_inventory_stays_unknown(self):
        record = evidence()
        validate(record, "worker-evidence")
        self.assertEqual(record["observed_tools"], {"status": "unknown"})
        with self.assertRaises(ValueError):
            validate(evidence("unknown-with-tools"), "worker-evidence")
        validate(evidence("observed-empty"), "worker-evidence")


class SharedTypesTest(unittest.TestCase):
    def test_worker_evidence_round_trips_through_its_schema(self):
        from harness_forge.models import WorkerEvidence

        record = evidence()
        self.assertEqual(WorkerEvidence.from_dict(record).to_dict(), record)
        with self.assertRaises(ValueError):
            WorkerEvidence.from_dict(evidence("certified"))

    def test_rule_keeps_optional_fields_absent(self):
        from harness_forge.documents import load_document
        from harness_forge.models import Rule

        rules = load_document(FIXTURES / "minimal-kb" / "rules" / "skills.yaml", "rules")
        judged = Rule.from_dict(rules[0])
        self.assertIsNone(judged.checker)
        self.assertEqual(judged.applicability, "script")
        self.assertEqual(judged.to_dict(), rules[0])
        self.assertEqual(Rule.from_dict(rules[1]).to_dict(), rules[1])


if __name__ == "__main__":
    unittest.main()
