"""The automated live runner with scripted workers: no model, no network, no user input."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from harness_forge.runtime import BASELINE_PROMPT
from tests.test_adapters import PARENT, FakeRunner, claude_stream
from tests.test_workflow import ResearchRunner, fetch_page, finding

FORGE = Path(__file__).resolve().parents[1]


def load_runner():
    specification = importlib.util.spec_from_file_location("live_runs", FORGE / "tools" / "live_runs.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LIVE = load_runner()
FORGE_SKILLS = list(LIVE.SKILLS)


class VerticalRunTest(unittest.TestCase):
    def run_vertical(self, runner):
        forge = LIVE.Forge()
        forge.module.RUNNER, forge.module.FETCH, forge.module.ENVIRON = runner, fetch_page, PARENT
        with tempfile.TemporaryDirectory(prefix="forge-vertical-") as directory:
            summary = LIVE.vertical("claude-code", Path(directory) / "run", forge=forge)
            on_disk = json.loads((Path(directory) / "run" / "summary.json").read_text(encoding="utf-8"))
            self.streams = [json.loads(path.read_text(encoding="utf-8"))
                            for path in sorted((Path(directory) / "run" / "streams").glob("*.json"))]
        return summary, on_disk

    def test_the_vertical_run_completes_without_input_and_says_who_confirmed(self):
        runner = ResearchRunner([finding()])
        summary, on_disk = self.run_vertical(runner)
        self.assertEqual(summary["status"], "completed", [s for s in summary["stages"] if s["exit"]])
        self.assertTrue(summary["clean"])
        self.assertEqual(summary["parts"][0]["harness_result"], "verified")
        self.assertIn("not by a user", summary["confirmation"])
        self.assertEqual(on_disk["status"], "completed")
        self.assertEqual(runner.roles, ["research", "source-support", "research", "source-support", "review",
                                        "baseline", "baseline", "runtime", "runtime", "runtime"])

    def test_every_worker_stream_is_kept_for_diagnosis(self):
        runner = ResearchRunner([finding()])
        self.run_vertical(runner)
        self.assertEqual(len(self.streams), len(runner.worker_calls))
        self.assertEqual([stream["stdin"] for stream in self.streams], [call["stdin"] for call in runner.worker_calls])
        for stream in self.streams:
            self.assertEqual(set(stream), {"command", "stdin", "returncode", "stdout", "stderr", "timed_out"})

    def test_unresolved_rules_are_confirmed_by_the_runner_and_say_so(self):
        from tests.test_cli_live import NotApplicableRunner
        rule = "skills.invocation.side-effects-user-invoked"
        summary, _ = self.run_vertical(NotApplicableRunner([finding()], rule))
        self.assertEqual([stage["stage"] for stage in summary["stages"]][-2:], ["verify", "verify with confirmations"])
        confirmed = summary["not_applicable_confirmed"]
        self.assertEqual(confirmed["by"], LIVE.TEST_RELAY)
        [entry] = confirmed["rules"]
        self.assertEqual(entry, {"part_id": "A", "rule_id": rule, "reason": "It writes text only.",
                                 "confirmation": entry["confirmation"]})
        self.assertRegex(entry["confirmation"], rf"^A:{rule}:[0-9a-f]{{16}}$")
        self.assertEqual(summary["parts"][0]["harness_result"], "verified")

    def test_a_failed_verification_is_not_a_completed_run(self):
        class GoalNotMet(ResearchRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin:
                    output = json.loads(json.loads(answer.stdout.splitlines()[-1])["result"])
                    for verdict in output["goal_verdicts"]:
                        verdict.update(verdict="not-met", reason="It leaves out the platform.")
                    return Completed(0, claude_stream(output, calls=("Read",)), "", False)
                return answer

        summary, on_disk = self.run_vertical(GoalNotMet([finding()]))
        self.assertEqual(summary["parts"][0]["harness_result"], "not verified")
        self.assertEqual(summary["status"], "not verified")
        self.assertEqual(on_disk["status"], "not verified")

    def test_no_kept_deep_finding_stops_before_acceptance(self):
        summary, _ = self.run_vertical(ResearchRunner([finding(quote="Not on the page.")]))
        self.assertIn("kept no finding", summary["status"])
        self.assertNotIn("accept", [stage["stage"] for stage in summary["stages"]])

    def test_an_api_key_login_stops_without_asking(self):
        from tests.test_adapters import CLAUDE_API_KEY
        summary, _ = self.run_vertical(FakeRunner(login=CLAUDE_API_KEY))
        self.assertTrue(summary["status"].startswith("stopped"))
        self.assertEqual([stage["stage"] for stage in summary["stages"]], ["workers"])


def init(skills):
    return {"type": "system", "subtype": "init", "tools": ["Skill", "Read"], "skills": skills,
            "plugins": [{"name": "cc-plugin-telemetry", "path": "builtin"}], "mcp_servers": [], "model": "m",
            "apiKeySource": "none"}


class FlowsRunTest(unittest.TestCase):
    """T12's flows on top of the vertical: a goal conflict, fixture drift with an override, break, fix, both scopes."""

    def run_flows(self, runner):
        forge = LIVE.Forge()
        forge.module.RUNNER, forge.module.FETCH, forge.module.ENVIRON = runner, fetch_page, PARENT
        with tempfile.TemporaryDirectory(prefix="forge-flows-") as directory:
            summary = LIVE.flows("claude-code", Path(directory) / "run", forge=forge)
            self.report = (Path(directory) / "run/repo/.harness/reports/repro.md").read_text(encoding="utf-8")
        return summary, forge

    def test_the_flows_run_covers_every_t12_flow_and_labels_its_fixtures(self):
        runner = ResearchRunner([finding()])
        summary, forge = self.run_flows(runner)
        self.assertEqual(summary["status"], "completed", [s for s in summary["stages"] if s["exit"]])
        self.assertEqual(summary["checks"], {
            "goal conflict asked": True, "goal adjusted and selected again": True, "drift recorded": True,
            "issue declined, nothing filed": True, "verified with the adopted override": True,
            "broken skill not verified": True, "review verified the repair": True,
            "review judged the repaired part and kept the other": True, "extensive review verified": True,
            "extensive review judged every part": True})
        self.assertTrue(all("FIXTURE" in text for text in summary["fixtures"].values()))
        self.assertEqual(runner.roles.count("review"), 4, "four verifications, each with a fresh reviewer")
        self.assertIn("- Review scope: Extensive review (rarely needed): judged A, B", self.report)
        runtime_parts = {stage["stage"]: [case["case_id"] for case in (stage["output"] or {}).get("runtime", [])]
                         for stage in summary["stages"] if stage["stage"].startswith("verify a Review")}
        self.assertEqual(len(next(iter(runtime_parts.values()))), 3, "the Review ran A's three cases only")
        self.assertEqual(forge.module.ISSUES[0]("skills.description.length"), [], "the issue search is a fixture")

    def test_the_review_check_reads_the_reviewer_requests_actually_sent(self):
        runner = ResearchRunner([finding()])
        forge = LIVE.Forge()
        forge.module.RUNNER, forge.module.FETCH, forge.module.ENVIRON = runner, fetch_page, PARENT
        original = forge.module.verify_live

        def judge_everything(*arguments, **options):
            return original(*arguments, **{**options, "scope": None, "kept": {}})

        forge.module.verify_live = judge_everything
        with tempfile.TemporaryDirectory(prefix="forge-flows-") as directory:
            summary = LIVE.flows("claude-code", Path(directory) / "run", forge=forge)
        self.assertFalse(summary["checks"]["review judged the repaired part and kept the other"])
        self.assertEqual(summary["status"], "flows did not hold")

    def test_a_flow_that_does_not_hold_is_not_completed(self):
        class BrokenRepair(ResearchRunner):
            """The reviewer fails the goal from the third verification on: the repair is never verified."""

            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "review" and self.roles.count("review") >= 3:
                    output = json.loads(json.loads(answer.stdout.splitlines()[-1])["result"])
                    for verdict in output["goal_verdicts"]:
                        verdict["verdict"] = "not-met"
                    return Completed(0, claude_stream(output, calls=("Read",)), "", False)
                return answer

        summary, _ = self.run_flows(BrokenRepair([finding()]))
        self.assertEqual(summary["status"], "flows did not hold")
        self.assertFalse(summary["checks"]["review verified the repair"])


class RoutingRunner(FakeRunner):
    """Baseline lists two built-ins; a slash prompt shows its command marker; nothing else activates."""

    def __init__(self, extra_skills=()):
        super().__init__()
        self.extra = list(extra_skills)

    def __call__(self, command, *, cwd, env, stdin, timeout):
        launched = len(self.worker_calls)
        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if len(self.worker_calls) == launched:
            return answer
        if stdin == BASELINE_PROMPT:
            # Bundled skills, which the second baseline launch turns off.
            events = [init([] if "disableBundledSkills" in " ".join(command) else ["debug", "verify"])]
        else:
            events = [init(FORGE_SKILLS + ["debug", "verify"] + self.extra)]
            if stdin.startswith("/"):
                name = stdin.split()[0][1:]
                events.append({"type": "user", "message": {"content": f"<command-name>/{name}</command-name>"}})
        events.append({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
        return Completed(0, "\n".join(json.dumps(e) for e in events) + "\n", "", False)


class RoutingSmokeTest(unittest.TestCase):
    def run_smoke(self, runner):
        with tempfile.TemporaryDirectory(prefix="forge-routing-") as directory:
            return LIVE.routing_smoke("claude-code", Path(directory), runner=runner, environ=PARENT)

    def test_twelve_cases_per_harness_against_the_safe_mode_baseline(self):
        runner = RoutingRunner()
        summary = self.run_smoke(runner)
        self.assertEqual(summary["baseline"]["skills"], ["debug", "verify"])
        self.assertEqual(runner.worker_calls[0]["stdin"], BASELINE_PROMPT)
        results = summary["results"]
        self.assertEqual(len(results), 12)
        by_mode = {}
        for result in results:
            by_mode.setdefault(result["mode"] if result["expected"] == "activates" else "near-miss", []).append(
                result["result"])
        self.assertEqual(by_mode, {"explicit": ["pass"] * 4, "implicit": ["fail"] * 4, "near-miss": ["pass"] * 4})
        self.assertTrue(summary["clean"])
        self.assertEqual(summary["status"], "completed with failures", "live_checks exits 1 on a failed case")
        self.assertEqual(len(runner.worker_calls[2]["files"]),  # after both baseline launches
                         sum(1 for skill in FORGE_SKILLS for p in (FORGE / "skills" / skill).rglob("*") if p.is_file()))

    def test_a_user_skill_makes_the_run_unclean(self):
        summary = self.run_smoke(RoutingRunner(extra_skills=["my-own-skill"]))
        self.assertFalse(summary["clean"])
        self.assertTrue(all(result["result"] == "not-run" for result in summary["results"]))



class InventoryGapTest(unittest.TestCase):
    """Codex reports no inventory: its runs are best-effort, and the summary says what stays unknown."""

    def test_an_unobservable_inventory_is_a_stated_gap_not_a_failure(self):
        observed = {"reason": "Skill tool called", "inventory": "observed"}
        hidden = {"reason": "read SKILL.md", "inventory": "not observable"}
        for results, clean, gap in (([observed], True, False), ([observed, hidden], None, True)):
            with self.subTest(clean=clean):
                summary = {"status": "completed"}
                LIVE.set_clean(summary, results)
                self.assertEqual(summary["clean"], clean)
                self.assertEqual("inventory_gap" in summary, gap)
                self.assertEqual(LIVE.exit_code(summary), 0)
        summary = {"status": "completed"}
        LIVE.set_clean(summary, [hidden])
        self.assertIn("cannot rule out", summary["inventory_gap"])
        unclean = {"status": "completed"}
        LIVE.set_clean(unclean, [{"reason": "unavailable: executable components stayed loaded: plugin x",
                                  "inventory": "observed"}])
        self.assertEqual((unclean["clean"], LIVE.exit_code(unclean)), (False, 1))
        self.assertEqual(LIVE.exit_code({"status": "flows did not hold", "clean": True}), 1)

if __name__ == "__main__":
    unittest.main()
