"""Every recommended mechanism and viable alternative meets the requirement table.

requirements() below is a hand-written table from the overview's "When
not" conditions and the vendor hook references, kept apart from the KB
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
# Event capabilities per harness, from the hooks references fetched 2026-10-10
# (code.claude.com/docs/en/hooks, learn.chatgpt.com/docs/hooks). Block: the hook
# can prevent the action or stop the agent. Feedback: its result reaches the agent.
SHARED_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact",
                 "PostCompact", "SubagentStart", "SubagentStop", "Stop", "SessionEnd"}
EVENTS = {
    "claude-code": SHARED_EVENTS | {
        "PostToolUseFailure", "PostToolBatch", "PermissionDenied", "UserPromptExpansion", "Setup", "TaskCreated",
        "TaskCompleted", "TeammateIdle", "Notification", "StopFailure", "ConfigChange", "InstructionsLoaded",
        "MessageDisplay", "CwdChanged", "FileChanged", "DirectoryAdded", "WorktreeCreate", "WorktreeRemove",
        "Elicitation", "ElicitationResult", "PreModelSwitch", "PostModelSwitch"},
    "codex": SHARED_EVENTS | {"Interrupt"},
}
# Claude Code: every event honors continue: false unless its section discards it.
BLOCK = {
    "claude-code": EVENTS["claude-code"] - {"Setup", "InstructionsLoaded", "MessageDisplay", "Notification",
                                            "CwdChanged", "FileChanged", "DirectoryAdded", "PostCompact",
                                            "SessionEnd", "StopFailure"},
    "codex": {"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop", "PreCompact",
              "PostCompact"},
}
FEEDBACK = {
    "claude-code": {"SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PreToolUse", "PermissionRequest",
                    "PostToolUse", "PostToolUseFailure", "PostToolBatch", "Stop", "SubagentStop", "SubagentStart",
                    "TaskCreated", "TaskCompleted", "TeammateIdle", "PostModelSwitch"},
    "codex": {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart",
              "SubagentStart"},
}
# Claude Code prompt and agent hooks: where ok: false takes effect, blocks, or reaches Claude.
MODEL_VERDICT_EVENTS = {"PreToolUse", "PostToolUse", "PostToolBatch", "PostToolUseFailure", "UserPromptSubmit",
                        "UserPromptExpansion", "Stop", "SubagentStop", "TaskCreated", "TaskCompleted", "TeammateIdle"}
MODEL_BLOCK = MODEL_VERDICT_EVENTS - {"PostToolUseFailure"}
MODEL_FEEDBACK = MODEL_VERDICT_EVENTS - {"PostToolBatch", "UserPromptSubmit", "UserPromptExpansion"}
HARNESSES = {"claude-code": ("claude-code",), "codex": ("codex",), "both": ("claude-code", "codex")}
ALL_EVENTS = sorted(EVENTS["claude-code"] | EVENTS["codex"])
VARIED = {
    "job": ["guidance", "procedure", "setting", "access-control", "integration", "check", "delegate", "bundle",
            "run-unattended", "one-step"],
    "every_time": ["always", "usually"],
    "irreversible": [True, False],
    "trigger": ["user", "model", "event"],
    "own_context": [True, False],
    "own_model": [True, False],
    "script_checkable": [True, False],
    # Representative events; HookSweepTest covers every event.
    "hook_event": ["PreToolUse", "PostCompact", "SessionStart", "Interrupt"],
    "hook_effect": ["block", "feedback", "report"],
    "external_system": ["none", "cli", "no-cli"],
    "target_set": ["claude-code", "codex", "both"],
}
# The model, or instructions and skills it follows, start these on its own decision.
MODEL_STARTED = {"instructions", "skills", "subagents"}


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
    event, effect = facts["hook_event"], facts["hook_effect"]
    for harness in HARNESSES[facts["target_set"]]:
        if event not in EVENTS[harness]:
            add({"hooks"}, f"{harness} has no {event} event")
        if effect == "block" and event not in BLOCK[harness]:
            add({"hooks"}, f"a {harness} hook cannot block or stop at {event}")
        if effect == "feedback" and event not in FEEDBACK[harness]:
            add({"hooks"}, f"a {harness} hook result does not reach the agent at {event}")
    needs_model = not facts["script_checkable"] or (facts["own_context"] and facts["own_model"])
    if needs_model and facts["target_set"] != "claude-code":
        add({"hooks"}, "needs a model; portable hook handlers are commands and MCP tool calls")
    if needs_model and event not in MODEL_VERDICT_EVENTS:
        add({"hooks"}, "needs a model; a prompt or agent verdict has no effect at this event")
    if needs_model and effect == "block" and event not in MODEL_BLOCK:
        add({"hooks"}, "needs a model; its verdict cannot block at this event")
    if needs_model and effect == "feedback" and event not in MODEL_FEEDBACK:
        add({"hooks"}, "needs a model; its verdict does not reach the agent at this event")
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


def handler(facts: dict) -> str:
    needs_model = not facts["script_checkable"] or (facts["own_context"] and facts["own_model"])
    return "prompt or agent handler" if needs_model else "command handler"


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
                if actual.mechanism == "hooks":
                    self.assertEqual(actual.variant, handler(facts))
                for alternative in actual.alternatives:
                    if alternative["concept"] == "hooks":
                        self.assertEqual(alternative["variant"], handler(facts))
                if actual.outcome in ("harness-only", "outside-alongside", "reuse"):
                    for concept in unmet:
                        self.assertEqual(states[concept], "ruled out", f"{concept}: {unmet[concept]}")

    def test_reuse_meets_every_confirmed_requirement(self):
        for facts in combinations(external_system=["none"], target_set=["both"], hook_event=["PreToolUse"],
                                  hook_effect=["block"]):
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
                                  hook_event=["PreToolUse"], hook_effect=["block"], external_system=["none"],
                                  target_set=["both"]))
        actual = run(facts, [candidate("skills"), candidate("hooks")])
        self.assertEqual((actual.outcome, actual.mechanism), ("reuse", "hooks"))
        self.assertEqual([conflict["candidate"] for conflict in actual.reuse_conflicts], ["existing-skills"])


class HookSweepTest(unittest.TestCase):
    """Every event, effect, target and handler for the jobs a hook serves."""

    def test_hooks_are_recommended_exactly_where_the_table_allows_them(self):
        for job, trigger in (("check", "model"), ("procedure", "event")):
            for values in itertools.product(["always", "usually"], ALL_EVENTS, ["block", "feedback", "report"],
                                            HARNESSES, [True, False], [(True, True), (True, False), (False, False)]):
                every_time, event, effect, target_set, script_checkable, (own_context, own_model) = values
                facts = {"job": job, "every_time": every_time, "irreversible": False, "trigger": trigger,
                         "own_context": own_context, "own_model": own_model, "script_checkable": script_checkable,
                         "hook_event": event, "hook_effect": effect, "external_system": "none",
                         "target_set": target_set}
                actual = run(facts)
                unmet = requirements(facts)
                states = {entry["concept"]: entry["state"] for entry in actual.assessment}
                with self.subTest(facts=facts):
                    self.assertIsNone(actual.pending_question)
                    if "hooks" in unmet:
                        self.assertNotEqual(actual.mechanism, "hooks", unmet["hooks"])
                        self.assertNotIn("hooks", [a["concept"] for a in actual.alternatives])
                        self.assertEqual(states["hooks"], "ruled out", unmet["hooks"])
                    elif not own_context:
                        self.assertEqual(actual.mechanism, "hooks")
                    if actual.mechanism == "hooks":
                        self.assertEqual(actual.variant, handler(facts))


class ReportedBypassesTest(unittest.TestCase):
    def base(self, **changes):
        facts = {"job": "procedure", "every_time": "usually", "irreversible": False, "trigger": "model",
                 "own_context": False, "own_model": False, "script_checkable": False, "hook_event": "PreToolUse",
                 "hook_effect": "block", "external_system": "none", "target_set": "both"}
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
        for event in ("Stop", "TaskCompleted"):
            self.assertEqual(run({**facts, "hook_event": event}).mechanism, "hooks", event)
        for event in ("SessionStart", "PermissionRequest", "PermissionDenied"):
            actual = run({**facts, "hook_event": event})
            self.assertNotEqual(actual.mechanism, "hooks", event)
            self.assertEqual(self.states(actual)["hooks"], "ruled out", event)

    def test_a_blocking_check_needs_an_event_where_each_harness_can_block(self):
        facts = self.base(job="check", every_time="always", script_checkable=True)
        self.assertEqual(run({**facts, "hook_event": "PermissionRequest"}).mechanism, "hooks")
        # Claude Code honors continue: false at SessionStart; Codex documents no stop there.
        self.assertNotEqual(run({**facts, "hook_event": "SessionStart"}).mechanism, "hooks")
        self.assertEqual(run({**facts, "hook_event": "SessionStart", "target_set": "claude-code"}).mechanism, "hooks")

    def test_codex_can_stop_after_compaction(self):
        facts = self.base(job="check", every_time="always", script_checkable=True, hook_event="PostCompact")
        self.assertEqual(run({**facts, "target_set": "codex"}).mechanism, "hooks")
        actual = run({**facts, "target_set": "claude-code"})
        self.assertNotEqual(actual.mechanism, "hooks")
        self.assertEqual(self.states(actual)["hooks"], "ruled out")

    def test_interrupt_is_a_codex_event(self):
        facts = self.base(trigger="event", script_checkable=True, hook_event="Interrupt", hook_effect="report")
        self.assertEqual(run({**facts, "target_set": "codex"}).mechanism, "hooks")
        for target_set in ("claude-code", "both"):
            self.assertEqual(self.states(run({**facts, "target_set": target_set}))["hooks"], "ruled out", target_set)

    def test_mandatory_execution_does_not_need_to_block(self):
        facts = self.base(trigger="event", every_time="always", script_checkable=True, hook_event="SessionStart",
                          hook_effect="report")
        for target_set in ("claude-code", "codex", "both"):
            self.assertEqual(run({**facts, "target_set": target_set}).mechanism, "hooks", target_set)

    def test_a_reporting_check_runs_at_an_event_without_decision_control(self):
        facts = self.base(job="check", script_checkable=True, hook_event="Notification", hook_effect="report",
                          target_set="claude-code")
        self.assertEqual(run(facts).mechanism, "hooks")
        self.assertEqual(self.states(run({**facts, "hook_effect": "block"}))["hooks"], "ruled out")

    def test_the_effect_is_asked_when_it_decides(self):
        facts = self.base(job="check", script_checkable=True, hook_event="SessionStart", target_set="codex")
        del facts["hook_effect"]
        self.assertEqual(run(facts).pending_question, "hook_effect")
        facts["hook_event"] = "PreToolUse"
        self.assertIsNone(run(facts).pending_question)

    def test_reuse_is_described_by_its_candidate(self):
        facts = self.base(job="check", every_time="always", script_checkable=True)
        actual = run(facts, [candidate("configuration")])
        self.assertEqual((actual.outcome, actual.mechanism), ("reuse", "configuration"))
        self.assertIsNone(actual.variant)
        self.assertEqual((actual.pros, actual.cons), ([], []))
        self.assertEqual(actual.reuse["name"], "existing-configuration")

    def test_an_event_procedure_resolves_its_handler(self):
        facts = self.base(trigger="event", script_checkable=True, own_context=True, own_model=True,
                          target_set="claude-code")
        actual = run(facts)
        self.assertEqual((actual.mechanism, actual.variant), ("hooks", "prompt or agent handler"))
        del facts["own_model"]
        self.assertEqual(run(facts).pending_question, "own_model")

    def test_a_reused_hook_resolves_its_handler(self):
        facts = self.base(job="check", script_checkable=True, own_context=True, target_set="claude-code")
        del facts["own_model"]
        self.assertEqual(run(facts, [candidate("hooks")]).pending_question, "own_model")
        for own_model, expected in ((True, "prompt or agent handler"), (False, "command handler")):
            actual = run({**facts, "own_model": own_model}, [candidate("hooks")])
            self.assertEqual((actual.outcome, actual.mechanism, actual.variant), ("reuse", "hooks", expected))

    def test_a_hook_alternative_names_its_handler(self):
        actual = run(self.base(job="check", script_checkable=True, own_context=True, target_set="claude-code"))
        self.assertEqual(actual.mechanism, "subagents")
        alternative = next(a for a in actual.alternatives if a["concept"] == "hooks")
        self.assertEqual(alternative["variant"], "command handler")

    def test_an_undecided_handler_asks_for_a_model_of_its_own(self):
        facts = self.base(job="check", every_time="always", script_checkable=True, own_context=True,
                          target_set="claude-code")
        del facts["own_model"]
        self.assertEqual(run(facts).pending_question, "own_model")

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
