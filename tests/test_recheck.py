"""Re-checking the sources of earlier accepted requirements after a new deep pass (issue #24)."""

import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.output import output_schema
from harness_forge.contracts import load_decision, write_decision
from harness_forge.documents import _schemas, load_document, schema_errors
from harness_forge.fetch import FetchError
from harness_forge.research import load_revision
from tests.test_cli_live import Cli
from tests.test_pipeline import SOURCE, staged_request
from tests.test_research import PAGE
from tests.test_verify import skill_decision
from tests.test_workflow import ResearchRunner, finding

VERSIONS = "https://docs.example-vendor.com/repro/versions"
VERSIONS_QUOTE = "Always record the reproducer version."
VERSIONS_PAGE = f"Versions. {VERSIONS_QUOTE} Older versions are not supported."
OTHER = "https://docs.example-vendor.com/repro/other"
OTHER_QUOTE = "Reports need a minimal example."
OTHER_PAGE = f"Reports. {OTHER_QUOTE} Long logs are not read."
NOTES = "https://docs.example-vendor.com/repro/notes"
NOTES_QUOTE = "Attach the full crash log."
NOTES_PAGE = f"Notes. {NOTES_QUOTE} Screenshots are optional. Never paste access tokens."


def requirement(name, sources, kind="must", statement="The checklist names the platform"):
    return {"id": f"idea.repro.a.{name}", "concept": "idea", "kind": kind, "severity": "error", "check": "judged",
            "statement": statement, "applies_to": {"harnesses": ["claude-code"]}, "evidence": "Vendor",
            "sources": sources, "contested": False}


PLATFORM = requirement("platform", [SOURCE, VERSIONS])
VERSION = requirement("version", [VERSIONS], statement="The checklist names the reproducer version")
SECRETS = requirement("secrets", [NOTES], kind="must-not", statement="The checklist asks for access tokens")


def versions_finding(**changes):
    return finding(**{"claim": "The reproducer version must always be recorded", "source": VERSIONS,
                      "quote": VERSIONS_QUOTE, "context": VERSIONS_PAGE, **changes})


def notes_finding(**changes):
    return finding(**{"claim": "A report attaches the full crash log", "source": NOTES, "quote": NOTES_QUOTE,
                      "context": NOTES_PAGE, **changes})


def other_finding(**changes):
    return finding(**{"claim": "Reports need a minimal example", "source": OTHER, "quote": OTHER_QUOTE,
                      "context": OTHER_PAGE, **changes})


class RecheckCase(unittest.TestCase):
    """A new deep pass that misses earlier sources, then a re-check limited to them."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="forge-recheck-")
        self.root = Path(self.directory.name)
        record = skill_decision(status="decided")
        record.record["accepted_revision"] = None
        self.decision = write_decision(self.root, record, slug="repro", date="2026-10-10")
        (self.root / "README.md").write_text("# Repro\n", encoding="utf-8")
        self.fetched = []
        self.runner = ResearchRunner([finding(), versions_finding(), notes_finding()])
        self.cli = Cli(self.runner, self.root)
        self.cli.module.FETCH = self.fetch
        _, workers, _ = self.cli("workers", "--harness", "claude-code")
        self.evidence = self.cli.write("workers.json", workers)
        self.revision = self.root / ".harness/research/repro.rules.yaml"
        self.runs = 0

    def tearDown(self):
        self.directory.cleanup()

    def fetch(self, url):
        self.fetched.append(url)
        pages = {SOURCE: PAGE, VERSIONS: VERSIONS_PAGE, OTHER: OTHER_PAGE, NOTES: NOTES_PAGE}
        if url not in pages:
            raise FetchError(f"no page for {url}")
        return pages[url]

    def research(self, *extra):
        self.runs += 1
        recorded = ["--revision", self.revision] if self.revision.exists() else []
        relay = self.cli.confirm(self.decision, f"intake-{self.runs}", *recorded)
        return self.cli("research", "--decision", self.decision, "--pass", "deep", "--part", "A", "--harness",
                        "claude-code", "--evidence", self.evidence, "--relay", relay, "--root", self.root,
                        "--slug", "repro", *recorded, *extra)

    def accept(self, *requirements):
        name = f"proposed-{self.runs}-{len(requirements)}.json"
        proposed = self.cli.write(name, {"requirements": list(requirements)})
        choices = self.cli.write(f"choices-{name}", {item["id"]: "accept" for item in requirements})
        return self.cli("accept", "--decision", self.decision, "--part", "A", "--proposed", proposed,
                        "--choices", choices, "--root", self.root, "--slug", "repro")

    def accepted_then_lost(self):
        """Three requirements accepted; a later deep pass finds only an unrelated source."""
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        self.runner.findings = [other_finding()]
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)

class RecheckTest(RecheckCase):
    def test_a_lost_source_is_named_with_the_re_check(self):
        self.accepted_then_lost()
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 2)
        self.assertIn(f"cites {SOURCE}, which no kept finding of part A's deep research has", stderr)
        self.assertIn("research --recheck", stderr)

    def test_a_re_check_restores_the_provenance_of_earlier_requirements(self):
        self.accepted_then_lost()
        digest = load_revision(self.revision).digest
        self.runner.findings = [
            {**finding(), "requirement_id": PLATFORM["id"]},
            # The quote is not on the page: the source stays unsupported.
            {**versions_finding(quote="Never record the version.", context="Never record the version."),
             "requirement_id": PLATFORM["id"]},
            # A true claim on another subject at the right page restores the source; the user judges its relation.
            {**notes_finding(claim="Screenshots are optional in a report", quote="Screenshots are optional."),
             "requirement_id": SECRETS["id"]},
            # Not a lost source of the named requirement, no binding, an unknown requirement.
            {**other_finding(), "requirement_id": VERSION["id"]},
            other_finding(),
            {**finding(), "requirement_id": "idea.repro.a.unknown"},
        ]
        self.fetched.clear()
        code, output, stderr = self.research("--write", "--recheck")
        self.assertEqual(code, 0, stderr)

        request = next(staged_request(call["stdin"]) for call in reversed(self.runner.worker_calls)
                       if staged_request(call["stdin"])["role"] == "research")
        self.assertEqual(request["inputs"]["recheck"], [
            {"requirement_id": PLATFORM["id"], "kind": "must", "statement": PLATFORM["statement"],
             "sources": [SOURCE, VERSIONS]},
            {"requirement_id": VERSION["id"], "kind": "must", "statement": VERSION["statement"], "sources": [VERSIONS]},
            {"requirement_id": SECRETS["id"], "kind": "must-not", "statement": SECRETS["statement"],
             "sources": [NOTES]}])
        self.assertEqual(self.fetched, [SOURCE, VERSIONS, NOTES], "dropped bindings are never fetched")
        self.assertEqual(len(output["dropped"]), 4)
        reasons = " ".join(item["reason"] for item in output["dropped"])
        for expected in (f"{OTHER} is not a source {VERSION['id']} lost", "names no requirement",
                         "idea.repro.a.unknown is not a requirement to re-check"):
            self.assertIn(expected, reasons)

        by_id = {item["requirement_id"]: item for item in output["recheck"]}
        platform, version, secrets = by_id[PLATFORM["id"]], by_id[VERSION["id"]], by_id[SECRETS["id"]]
        # Partial recovery of a requirement with two sources: not re-supported.
        self.assertFalse(platform["re_supported"])
        self.assertEqual([s["source"] for s in platform["sources"]], [SOURCE, VERSIONS])
        recovered = platform["sources"][0]["findings"][0]
        self.assertEqual((recovered["quote"], recovered["requirement_id"]), (finding()["quote"], PLATFORM["id"]))
        self.assertEqual(platform["sources"][1]["findings"], [])
        self.assertFalse(version["re_supported"])
        # The unrelated claim restores the source, shown bound to its requirement and beside its statement.
        self.assertTrue(secrets["re_supported"])
        self.assertEqual((secrets["kind"], secrets["statement"]), ("must-not", SECRETS["statement"]))
        self.assertEqual(secrets["sources"][0]["findings"][0]["claim"], "Screenshots are optional in a report")

        # Nothing is accepted on the user's behalf, and the earlier pass's findings stay.
        self.assertEqual(load_revision(self.revision).digest, digest)
        record = load_document(self.root / ".harness/research/repro.md", "research")
        self.assertEqual([f["source"] for f in record["findings"]], [OTHER, SOURCE, NOTES])
        self.assertNotIn("requirement_id", record["findings"][1])
        self.assertEqual(len([b for b in record["briefs"] if b["pass"] == "deep"]), 1)

        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 2)
        self.assertIn(f"cites {VERSIONS}", stderr)
        code, _, stderr = self.accept(SECRETS)
        self.assertEqual(code, 0, stderr)

    def test_a_re_check_needs_something_to_re_check(self):
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        calls = len(self.runner.worker_calls)
        code, _, stderr = self.research("--recheck")
        self.assertEqual(code, 2)
        self.assertIn("no accepted requirements of part A to re-check", stderr)
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.research("--recheck")
        self.assertEqual(code, 2)
        self.assertIn("every source of part A's accepted requirements has a kept finding", stderr)
        self.assertEqual(len(self.runner.worker_calls), calls, "nothing launches")

    def test_a_re_check_runs_under_the_parts_current_brief_only(self):
        self.accepted_then_lost()
        record = load_decision(self.decision)
        record.part("A")["goal"] = "Write a security checklist"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        calls = len(self.runner.worker_calls)
        code, _, stderr = self.research("--recheck")
        self.assertEqual(code, 2)
        self.assertIn("no deep research for part A under its current brief", stderr)
        self.assertEqual(len(self.runner.worker_calls), calls)

    def test_a_re_check_is_a_deep_pass_of_one_part(self):
        self.accepted_then_lost()
        relay = self.cli.confirm(self.decision, "survey", "--revision", self.revision)
        code, _, stderr = self.cli("research", "--decision", self.decision, "--pass", "survey", "--harness",
                                   "claude-code", "--evidence", self.evidence, "--relay", relay, "--root", self.root,
                                   "--slug", "repro", "--revision", self.revision, "--recheck")
        self.assertEqual(code, 2)
        self.assertIn("--recheck needs --pass deep and --part", stderr)

    def test_an_ordinary_pass_drops_a_finding_bound_to_a_requirement(self):
        self.runner.findings = [{**finding(), "requirement_id": PLATFORM["id"]}, versions_finding()]
        code, output, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        self.assertEqual([f["source"] for f in output["kept"]], [VERSIONS])
        self.assertIn("only a re-check binds findings to requirements", output["dropped"][0]["reason"])


class EnforcedRunner(ResearchRunner):
    """A research worker held to the output schema the CLIs enforce, as a real worker is."""

    def __call__(self, command, *, cwd, env, stdin, timeout):
        if stdin and "```json" in stdin and staged_request(stdin)["role"] == "research":
            errors = schema_errors({"findings": self.findings}, output_schema("research"))
            if errors:
                raise AssertionError(f"the enforced output schema refuses this answer: {errors}")
        return super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)


class EnforcedSchemaTest(RecheckCase):
    """The re-check through a worker that can only answer what `--json-schema` / `--output-schema` allow."""

    def setUp(self):
        super().setUp()
        self.runner = EnforcedRunner([{**f, "requirement_id": None}
                                      for f in (finding(), versions_finding(), notes_finding())])
        self.cli.module.RUNNER = self.runner

    def test_the_enforced_schema_lets_every_finding_name_its_requirement_or_none(self):
        item = output_schema("research")["properties"]["findings"]["items"]
        self.assertIn("requirement_id", item["required"])
        self.assertEqual(item["properties"]["requirement_id"]["type"], ["string", "null"])
        # What the CLI lets a worker answer is exactly what Forge accepts from it.
        response = _schemas()["worker-response"]
        research = next(branch["then"] for branch in response["allOf"]
                        if branch["if"]["properties"].get("role", {}).get("const") == "research")
        accepted = research["properties"]["output"]["properties"]["findings"]["items"]["properties"]
        self.assertEqual(set(item["properties"]), set(accepted))

    def test_ordinary_and_re_check_passes_work_through_the_enforced_schema(self):
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        self.runner.findings = [{**other_finding(), "requirement_id": None}]
        code, output, stderr = self.research("--write")
        self.assertEqual((code, len(output["kept"])), (0, 1), stderr)
        self.runner.findings = [{**finding(), "requirement_id": PLATFORM["id"]},
                                {**versions_finding(), "requirement_id": VERSION["id"]},
                                {**notes_finding(), "requirement_id": SECRETS["id"]},
                                {**versions_finding(), "requirement_id": None}]
        self.fetched.clear()
        code, output, stderr = self.research("--write", "--recheck")
        self.assertEqual(code, 0, stderr)
        self.assertTrue(all(item["re_supported"] for item in output["recheck"]), output["recheck"])
        self.assertEqual([d["reason"] for d in output["dropped"]], ["the re-check finding names no requirement"])
        self.assertEqual(self.fetched, [SOURCE, VERSIONS, NOTES], "a finding without a requirement is never fetched")
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)


if __name__ == "__main__":
    unittest.main()
