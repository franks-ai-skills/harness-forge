"""Runtime diagnostics: each launch's raw output, kept outside the repository; results never depend on them."""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from harness_forge.diagnostics import Diagnostics
from harness_forge.runtime import BASELINE_PROMPT, run_skill_runtime
from tests.test_adapters import PARENT, FakeRunner
from tests.test_build_skill import NAME
from tests.test_runtime import SKILL_DIR, case, claude_run
from tests.test_verify import Scenario, skill_decision


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())
        self.files = {path: data for path, data in self.scenario.files.items() if path.startswith(SKILL_DIR + "/")}
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-diagnostics-")
        self.home = Path(self.temporary.name) / "diagnostics"

    def tearDown(self):
        self.temporary.cleanup()

    def run_cases(self, harness, stream, cases=None, **options):
        runner = options.pop("runner", None) or FakeRunner(worker=stream)
        options.setdefault("baseline_skills", frozenset())
        results = run_skill_runtime(self.scenario.run, harness, cases or [case()], files=self.files, runner=runner,
                                    environ=PARENT, **options)
        return results, runner

    def kept(self, diagnostics, *parts):
        directory = diagnostics.directory.joinpath(*parts)
        return json.loads((directory / "launch.json").read_text(encoding="utf-8")), directory

    def test_each_case_keeps_its_output_and_workspace_context(self):
        diagnostics = Diagnostics(self.home)
        stream = claude_run()
        (result,), _ = self.run_cases("claude-code", stream, cases=[case(fixtures={"notes.md": "a note"})],
                                      diagnostics=diagnostics)
        self.assertEqual(result.result, "pass", result.reason)
        self.assertEqual(diagnostics.problems, [])
        self.assertTrue(diagnostics.directory.name.endswith(f"-{self.scenario.run.run_id}-"
                                                            f"{diagnostics.directory.name.rsplit('-', 1)[1]}"))
        self.assertEqual(stat.S_IMODE(diagnostics.directory.stat().st_mode), 0o700)
        launch, directory = self.kept(diagnostics, "cases", "repro-explicit")
        self.assertEqual((directory / "stdout.txt").read_text(encoding="utf-8"), stream)
        self.assertTrue((directory / "stderr.txt").is_file())
        self.assertEqual((launch["outcome"], launch["reason"], launch["returncode"], launch["timed_out"]),
                         (result.result, result.reason, 0, False))
        self.assertIn("Prompt for explicit.", launch["stdin"], "the prompt as sent")
        self.assertIn("--tools", launch["command"])
        self.assertTrue(launch["cwd"])
        self.assertEqual(sorted(launch["workspace"]["files"]), sorted(self.files))
        self.assertEqual(list(launch["workspace"]["fixtures"]), ["notes.md"])

    def test_a_timed_out_case_keeps_what_it_printed(self):
        diagnostics = Diagnostics(self.home)
        partial = claude_run().splitlines()[0] + "\n"

        class Slow(FakeRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                return Completed(None, partial, "still working", True) if "--tools" in command else answer

        (result,), _ = self.run_cases("claude-code", None, runner=Slow(worker=partial), diagnostics=diagnostics)
        self.assertEqual(result.result, "not-run", result.reason)
        launch, directory = self.kept(diagnostics, "cases", "repro-explicit")
        self.assertTrue(launch["timed_out"])
        self.assertEqual((directory / "stdout.txt").read_text(encoding="utf-8"), partial)
        self.assertEqual((directory / "stderr.txt").read_text(encoding="utf-8"), "still working")

    def test_a_case_that_never_launched_keeps_why(self):
        diagnostics = Diagnostics(self.home)
        part = self.scenario.run.parts[0]
        part.update(outcome="reuse", reuse={"name": NAME, "source": "https://example.com/repro", "license": "MIT",
                                            "maintenance": "maintained", "path": SKILL_DIR, "skill_home": "plugin"})
        (result,), _ = self.run_cases("claude-code", claude_run(), diagnostics=diagnostics)
        launch, directory = self.kept(diagnostics, "cases", "repro-explicit")
        self.assertEqual((launch["outcome"], launch["reason"]), (result.result, result.reason))
        self.assertFalse((directory / "stdout.txt").exists())

    def test_baseline_launches_keep_their_output_apart_from_the_cases(self):
        """A case may be named like a baseline launch; neither overwrites the other."""
        class Baseline(FakeRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if stdin != BASELINE_PROMPT:
                    return answer
                if "disableBundledSkills" in command[command.index("--settings") + 1]:
                    return Completed(1, "", "usage limit at /home/someone", False)
                return Completed(0, claude_run(skills=("debug",), skill_call=None), "", False)

        diagnostics = Diagnostics(self.home)
        (result,), _ = self.run_cases("claude-code", None, runner=Baseline(worker=claude_run()), baseline_skills=None,
                                      cases=[case(id="bundled")], diagnostics=diagnostics)
        self.assertTrue(result.reason.endswith("the launch with bundled skills off exited with 1"), result.reason)
        self.assertNotIn("/home/someone", result.reason)
        user, directory = self.kept(diagnostics, "baselines", "user")
        self.assertEqual((user["returncode"], user["stdin"]), (1, BASELINE_PROMPT))
        self.assertEqual((directory / "stderr.txt").read_text(encoding="utf-8"), "usage limit at /home/someone")
        self.assertTrue((diagnostics.directory / "baselines" / "bundled" / "stdout.txt").is_file())
        self.assertEqual(self.kept(diagnostics, "cases", "bundled")[0]["reason"], result.reason)

    def test_unwritable_diagnostics_never_change_a_result(self):
        Path(self.temporary.name, "file").write_text("not a directory", encoding="utf-8")
        for home in (Path(self.temporary.name) / "file" / "diagnostics", None):
            with self.subTest(home=home):
                diagnostics = Diagnostics(home)
                (result,), _ = self.run_cases("claude-code", claude_run(), diagnostics=diagnostics)
                self.assertEqual(result.result, "pass", result.reason)
                self.assertIsNone(diagnostics.directory)
                self.assertEqual(len(diagnostics.problems), 0 if home is None else 1)

    def test_diagnostics_stay_out_of_the_repository(self):
        repository = Path(self.temporary.name)
        diagnostics = Diagnostics(repository / ".forge" / "diagnostics", repository)
        (result,), _ = self.run_cases("claude-code", claude_run(), diagnostics=diagnostics)
        self.assertEqual(result.result, "pass", result.reason)
        self.assertIsNone(diagnostics.directory)
        self.assertIn("not kept inside the repository", diagnostics.problems[0])
        self.assertFalse((repository / ".forge").exists())


if __name__ == "__main__":
    unittest.main()
