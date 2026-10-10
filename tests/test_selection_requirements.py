"""Every recommended mechanism and viable alternative meets the requirement table.

requirements() below is a hand-written table from the overview's "When
not" conditions and the hooks handler support, kept apart from the KB
rule ids: for confirmed answers it names each mechanism that cannot meet
a requirement on its own. Selection must rule each of them out, for new
builds and for reuse alike. This checks selection against that table;
it does not prove the table itself, which shares Forge's policy choices
(see policy/selection.md).
"""

import itertools
import unittest

from tests.test_selection import RULES
from harness_forge.selection import CONCEPTS, recommend

WHOLE_IDEA = {"reach": "repository", "plan_controls": "available", "opens_prs": False, "bypass_mode": False,
              "untrusted_repo": False, "lethal_trifecta": False}
FIXED = {"event_without_agent": False, "must_survive_compromise": "not-required", "who_disables": "contributor",
         "early_feedback": False}
VARIED = {
    "job": ["guidance", "procedure", "setting", "access-control", "integration", "check", "delegate", "bundle",
            "run-unattended", "one-step"],
    "every_time": ["always", "usually"],
    "irreversible": [True, False],
    "trigger": ["user", "model", "event"],
    "own_context": [True, False],
    "own_model": [True, False],
    "script_checkable": [True, False],
    "hook_event": ["tool-call", "permission-request", "prompt-submit", "turn-end", "session-or-compaction",
                   "claude-code-only"],
    "external_system": ["none", "cli", "no-cli"],
    "target_set": ["claude-code", "codex", "both"],
}
# The model, or instructions and skills it follows, start these on its own decision.
MODEL_STARTED = {"instructions", "skills", "subagents"}
# Shared events whose Claude Code handlers include prompt or agent (a model of its own).
MODEL_HANDLER_EVENTS = {"tool-call", "permission-request", "prompt-submit", "turn-end"}


def requirements(facts: dict) -> dict[str, str]:
    """Mechanisms that cannot meet a confirmed requirement alone, with the requirement."""
    unmet: dict[str, str] = {}

    def add(concepts, requirement):
        for concept in concepts:
            unmet.setdefault(concept, requirement)

    if facts["every_time"] == "always":
        add({"instructions", "skills", "commands", "subagents", "automation", "mcp"}, "must hold every time")
    if facts["irreversible"]:
        add(MODEL_STARTED, "the model must not start an irreversible action on its own")
    if facts["own_context"]:
        add({"instructions", "skills", "commands"}, "needs its own context")
    needs_model = not facts["script_checkable"] or (facts["own_context"] and facts["own_model"])
    if needs_model and facts["target_set"] != "claude-code":
        add({"hooks"}, "needs a model; portable hook handlers are commands and MCP tool calls")
    if needs_model and facts["hook_event"] not in MODEL_HANDLER_EVENTS:
        add({"hooks"}, "needs a model; prompt and agent handlers do not run at this event")
    if facts["target_set"] != "claude-code" and facts["hook_event"] == "claude-code-only":
        add({"hooks"}, "the event exists only in Claude Code")
    if facts["job"] == "access-control" and facts["every_time"] == "always":
        add({"hooks"}, "a hard limit on access")
    if facts["job"] == "procedure" and facts["trigger"] == "model":
        add({"commands"}, "the model starts it")
    if facts["job"] == "procedure" and facts["trigger"] == "event":
        add({"instructions", "skills", "commands", "subagents"}, "an event starts it")
    return unmet


def combinations(**limits):
    varied = {**VARIED, **limits}
    for values in itertools.product(*varied.values()):
        facts = dict(zip(varied, values))
        if facts["job"] != "integration" and facts["external_system"] != varied["external_system"][0]:
            continue  # only integrations ask about external systems
        if not facts["own_context"] and facts["own_model"] != varied["own_model"][0]:
            continue  # own_model refines own_context only
        yield facts


def run(facts: dict, candidates=()):
    whole_idea = {**WHOLE_IDEA, "target_set": facts["target_set"]}
    answers = {**FIXED, **{key: value for key, value in facts.items() if key != "target_set"}}
    return recommend(answers, whole_idea, RULES, list(candidates))


def candidate(concept: str) -> dict:
    return {"name": f"existing-{concept}", "source": "https://example.com/existing", "license": "MIT",
            "maintenance": "maintained", "fit": "confirmed", "concept": concept}


class RequirementSetTest(unittest.TestCase):
    def test_new_builds_meet_every_confirmed_requirement(self):
        for facts in combinations():
            actual = run(facts)
            unmet = requirements(facts)
            states = {entry["concept"]: entry["state"] for entry in actual.assessment}
            offered = [actual.mechanism] if actual.mechanism else []
            offered += [alternative["concept"] for alternative in actual.alternatives]
            with self.subTest(facts=facts):
                self.assertIsNone(actual.pending_question)
                for concept in offered:
                    self.assertNotIn(concept, unmet, f"{concept} offered although it {unmet.get(concept)}")
                if actual.outcome in ("harness-only", "outside-alongside", "reuse"):
                    for concept in unmet:
                        self.assertEqual(states[concept], "ruled out", f"{concept}: {unmet[concept]}")

    def test_reuse_meets_every_confirmed_requirement(self):
        for facts in combinations(external_system=["none"], target_set=["both"], hook_event=["tool-call"]):
            unmet = requirements(facts)
            for concept in CONCEPTS:
                actual = run(facts, [candidate(concept)])
                with self.subTest(facts=facts, candidate=concept):
                    if concept in unmet:
                        self.assertNotEqual(actual.outcome, "reuse")
                        self.assertEqual([conflict["candidate"] for conflict in actual.reuse_conflicts],
                                         [f"existing-{concept}"])
                        self.assertTrue(actual.reuse_conflicts[0]["rules"])
                    else:
                        self.assertEqual((actual.outcome, actual.mechanism), ("reuse", concept))
                        self.assertEqual(actual.reuse_conflicts, [])

    def test_an_eligible_candidate_is_reused_when_another_conflicts(self):
        facts = next(combinations(job=["procedure"], every_time=["always"], irreversible=[False], trigger=["model"],
                                  own_context=[False], own_model=[False], script_checkable=[True],
                                  hook_event=["tool-call"], external_system=["none"], target_set=["both"]))
        actual = run(facts, [candidate("skills"), candidate("hooks")])
        self.assertEqual((actual.outcome, actual.mechanism), ("reuse", "hooks"))
        self.assertEqual([conflict["candidate"] for conflict in actual.reuse_conflicts], ["existing-skills"])


class ReportedBypassesTest(unittest.TestCase):
    def base(self, **changes):
        facts = {"job": "procedure", "every_time": "usually", "irreversible": False, "trigger": "model",
                 "own_context": False, "own_model": False, "script_checkable": False, "hook_event": "tool-call",
                 "external_system": "none", "target_set": "both"}
        return {**facts, **changes}

    def states(self, recommendation):
        return {entry["concept"]: entry["state"] for entry in recommendation.assessment}

    def test_irreversible_model_started_procedure_is_not_moved_into_instructions(self):
        actual = run(self.base(irreversible=True))
        self.assertNotEqual(actual.mechanism, "instructions")
        self.assertEqual(self.states(actual)["instructions"], "ruled out")
        self.assertEqual(actual.outcome, "outside-instead")

    def test_irreversible_user_started_procedure_has_no_instructions_alternative(self):
        actual = run(self.base(irreversible=True, trigger="user"))
        self.assertEqual(actual.mechanism, "commands")
        self.assertNotIn("instructions", [a["concept"] for a in actual.alternatives])

    def test_own_context_leaves_no_main_context_alternative(self):
        actual = run(self.base(own_context=True))
        self.assertEqual(actual.mechanism, "subagents")
        self.assertEqual(actual.alternatives, [])
        for concept in ("skills", "instructions", "commands"):
            self.assertEqual(self.states(actual)[concept], "ruled out", concept)

    def test_judgment_check_for_claude_code_only_can_be_a_hook(self):
        actual = run(self.base(job="check", target_set="claude-code"))
        self.assertEqual((actual.outcome, actual.mechanism), ("harness-only", "hooks"))
        self.assertTrue(any("Claude Code only" in con for con in actual.cons), actual.cons)

    def test_judgment_rules_out_hooks_only_for_portable_targets(self):
        for target_set in ("codex", "both"):
            actual = run(self.base(job="check", target_set=target_set))
            entry = next(entry for entry in actual.assessment if entry["concept"] == "hooks")
            self.assertEqual(entry["state"], "ruled out")
            self.assertTrue(any("Claude Code only" in reason for reason in entry["reasons"]), entry["reasons"])

    def test_codex_subagent_is_no_way_around_the_irreversible_exclusion(self):
        actual = run(self.base(irreversible=True, own_context=True, target_set="codex"))
        self.assertNotEqual(actual.mechanism, "subagents")
        self.assertEqual(self.states(actual)["subagents"], "ruled out")

    def test_a_model_of_its_own_rules_out_portable_hooks(self):
        facts = self.base(job="check", every_time="always", script_checkable=True, own_context=True, own_model=True)
        for target_set, expected in (("codex", None), ("both", None), ("claude-code", "hooks")):
            actual = run({**facts, "target_set": target_set})
            self.assertEqual(actual.mechanism, expected, target_set)
        actual = run({**facts, "own_model": False})
        self.assertEqual(actual.mechanism, "hooks")

    def test_the_event_decides_whether_a_judgment_hook_can_run(self):
        facts = self.base(job="check", target_set="claude-code")
        self.assertEqual(run({**facts, "hook_event": "turn-end"}).mechanism, "hooks")
        actual = run({**facts, "hook_event": "session-or-compaction"})
        self.assertNotEqual(actual.mechanism, "hooks")
        self.assertEqual(self.states(actual)["hooks"], "ruled out")

    def test_an_unknown_event_is_asked_when_it_decides(self):
        facts = {k: v for k, v in self.base(job="check", target_set="claude-code").items() if k != "hook_event"}
        self.assertEqual(run(facts).pending_question, "hook_event")

    def test_no_standalone_mechanism_is_not_reported_as_nothing_fitting(self):
        actual = run(self.base(irreversible=True))
        self.assertTrue(actual.no_standalone_mechanism)
        self.assertFalse(run(self.base()).no_standalone_mechanism)

    def test_event_started_procedure_is_not_a_skill(self):
        actual = run(self.base(trigger="event", script_checkable=True))
        self.assertEqual(actual.mechanism, "hooks")


if __name__ == "__main__":
    unittest.main()
