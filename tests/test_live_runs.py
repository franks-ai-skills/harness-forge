"""The automated live runner with scripted workers: no model, no network, no user input."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from tests.test_adapters import PARENT, FakeRunner
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
                                        "runtime", "runtime", "runtime"])

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
        self.assertEqual(summary["not_applicable_confirmed"],
                         {"by": LIVE.TEST_RELAY, "rules": [{"part_id": "A", "rule_id": rule, "reason": "It writes text only."}]})
        self.assertEqual(summary["parts"][0]["harness_result"], "verified")

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
        if "--safe-mode" in command:
            events = [init(["debug", "verify"])]
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
        self.assertIn("--safe-mode", runner.worker_calls[0]["command"])
        results = summary["results"]
        self.assertEqual(len(results), 12)
        by_mode = {}
        for result in results:
            by_mode.setdefault(result["mode"] if result["expected"] == "activates" else "near-miss", []).append(
                result["result"])
        self.assertEqual(by_mode, {"explicit": ["pass"] * 4, "implicit": ["fail"] * 4, "near-miss": ["pass"] * 4})
        self.assertTrue(summary["clean"])
        self.assertEqual(len(runner.worker_calls[1]["files"]),
                         sum(1 for skill in FORGE_SKILLS for p in (FORGE / "skills" / skill).rglob("*") if p.is_file()))

    def test_a_user_skill_makes_the_run_unclean(self):
        summary = self.run_smoke(RoutingRunner(extra_skills=["my-own-skill"]))
        self.assertFalse(summary["clean"])
        self.assertTrue(all(result["result"] == "not-run" for result in summary["results"]))


if __name__ == "__main__":
    unittest.main()
