"""Probe reports must not turn missing or self-reported evidence into support."""

import importlib.util
import json
from pathlib import Path
import sys
import unittest


PROBE_PATH = Path(__file__).resolve().parents[1] / "tools/live_checks.py"
COMPLETE = {"type": "result", "subtype": "success", "is_error": False, "result": "probe finished"}


def load_probe():
    specification = importlib.util.spec_from_file_location("forge_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class ProbeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PROBE_PATH.exists(), "The probe runner is not implemented")
        self.probe = load_probe()

    def test_missing_tool_inventory_is_unproven(self):
        result = self.probe.assess("research", {}, [], {"repository": "UNSUPPLIED_123"})
        self.assertEqual(result["status"], "unproven")

    def test_self_reported_denial_does_not_prove_restrictions(self):
        result = self.probe.assess(
            "source-support", {"claimed_denied": ["read", "write"]}, [], {}
        )
        self.assertEqual(result["status"], "unproven")

    def test_startup_context_leak_fails_without_a_tool_call(self):
        events = [{"type": "system", "subtype": "init", "tools": []}]
        result = self.probe.assess(
            "source-support", {"visible_canaries": ["UNSUPPLIED_123"]}, events,
            {"repository": "UNSUPPLIED_123"},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("repository", result["leaked_surfaces"])

    def test_forbidden_tool_in_actual_inventory_fails(self):
        events = [{"type": "system", "subtype": "init", "tools": ["Bash"]}]
        result = self.probe.assess("research", {}, events, {})
        self.assertEqual(result["status"], "failed")

    def test_no_tools_is_observed_for_source_support(self):
        events = [{"type": "system", "subtype": "init", "tools": []}, COMPLETE]
        result = self.probe.assess("source-support", {}, events, {})
        self.assertEqual(result["status"], "observed")

    def test_unexpected_write_fails(self):
        events = [{"type": "system", "subtype": "init", "tools": []}]
        result = self.probe.assess("review", {}, events, {}, wrote_outside=True)
        self.assertEqual(result["status"], "failed")

    def test_research_needs_real_web_evidence(self):
        events = [{"type": "system", "subtype": "init", "tools": ["WebFetch", "WebSearch"]}]
        result = self.probe.assess("research", {"web_ok": True}, events, {})
        self.assertEqual(result["status"], "unproven")

    def test_child_inventory_is_used_instead_of_parent(self):
        events = [
            {"type": "system", "subtype": "init", "tools": ["Agent", "Bash"]},
            {"type": "system", "subtype": "init", "parent_tool_use_id": "child-1", "tools": []},
            COMPLETE,
        ]
        result = self.probe.assess("source-support", {}, events, {}, native=True)
        self.assertEqual(result["status"], "observed")

    def test_web_success_is_bound_to_the_actual_web_call(self):
        events = [
            {"type": "system", "subtype": "init", "tools": ["WebFetch", "WebSearch"]},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "WebFetch", "id": "fetch-1"}
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "fetch-1", "content": "Example Domain"}
            ]}},
            COMPLETE,
        ]
        result = self.probe.assess("research", {}, events, {})
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["successful_web_tool_results"], 1)

    def test_codex_connector_web_call_fails_a_no_network_role(self):
        events = [{"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "codex_apps",
            "tool": "search_service.web_run", "status": "completed",
            "result": {"content": [{"type": "text", "text": "Example Domain"}]},
            "error": None,
        }}]
        result = self.probe.assess("review", {}, events, {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("codex_apps.search_service.web_run", result["forbidden_tools"])

    def test_init_without_completion_is_unproven(self):
        events = [{"type": "system", "subtype": "init", "tools": []}]
        self.assertEqual(self.probe.assess("source-support", {}, events, {})["status"], "unproven")

    def test_review_inventory_without_read_denials_is_unproven(self):
        events = [{"type": "system", "subtype": "init", "tools": ["Read", "Grep", "Glob"]}, COMPLETE]
        result = self.probe.assess("review", {}, events, {})
        self.assertEqual(result["status"], "unproven")

    def test_review_requires_correlated_permission_denials_for_both_targets(self):
        paths = ["/fixture/repository/unrelated.txt", "/fixture/outside.txt"]
        events = [{"type": "system", "subtype": "init", "tools": ["Read"]}, COMPLETE]
        for index, path in enumerate(paths):
            call_id = f"read-{index}"
            events += [
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "id": call_id, "input": {"file_path": path}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": "outside working directory"}]}, "tool_result_meta": [{"id": call_id, "non_execution_kind": "permission-rule"}]},
            ]
        result = self.probe.assess("review", {}, events, {}, required_denied_paths=paths)
        self.assertEqual(result["status"], "observed")
        events[-1]["tool_result_meta"] = []
        self.assertEqual(self.probe.assess("review", {}, events, {}, required_denied_paths=paths)["status"], "unproven")

    def test_malformed_or_non_object_events_prevent_positive_grade(self):
        text = '\n'.join(['{"type":"system","subtype":"init","tools":[]}', 'broken', '[]', json.dumps(COMPLETE)])
        events, errors = self.probe.parse_events(text)
        self.assertEqual(errors, 2)
        result = self.probe.assess("source-support", {}, events, {}, parse_errors=errors)
        self.assertEqual(result["status"], "unproven")

    def test_forbidden_tool_violation_survives_failed_execution(self):
        events = [{"type": "system", "subtype": "init", "tools": ["Bash"]}]
        for options in ({"exit_code": 1}, {"timed_out": True}):
            self.assertEqual(self.probe.assess("source-support", {}, events, {}, **options)["status"], "failed")

    def test_failed_terminal_prevents_observed_status(self):
        events = [{"type": "system", "subtype": "init", "tools": []}, {"type": "result", "is_error": True}]
        self.assertEqual(self.probe.assess("source-support", {}, events, {})["status"], "unavailable")

    def test_invalid_nested_event_shape_invalidates_positive_evidence(self):
        stream = '\n'.join(json.dumps(e) for e in [
            {"type": "system", "subtype": "init", "tools": []},
            {"type": "assistant", "message": ["invalid"]}, COMPLETE,
        ])
        events, errors = self.probe.parse_events(stream)
        self.assertEqual(errors, 1)
        self.assertEqual(self.probe.assess("source-support", {}, events, {}, parse_errors=errors)["status"], "unproven")

    def test_system_permission_notification_can_have_a_text_message(self):
        notification = {"type": "system", "subtype": "permission_denied", "message": "outside working directory"}
        events, errors = self.probe.parse_events(json.dumps(notification))
        self.assertEqual(errors, 0)
        self.assertEqual(self.probe.content_blocks(events[0]), [])

    def test_native_codex_web_result_is_counted_and_forbidden_without_network(self):
        event = {"type": "item.completed", "item": {"type": "web_search", "action": {"type": "open_page", "url": "https://example.com/"}, "results": [{"type": "text_result", "title": "Example Domain"}]}}
        research = self.probe.assess("research", {}, [event], {})
        self.assertEqual(research["successful_web_tool_results"], 1)
        self.assertEqual(research["status"], "unproven")
        self.assertEqual(self.probe.assess("review", {}, [event], {})["status"], "failed")

    def test_claude_web_call_fails_no_network_role_without_inventory(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "id": "fetch-1"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "fetch-1", "content": "Example Domain"}]}},
        ]
        for role in ("source-support", "review"):
            self.assertEqual(self.probe.assess(role, {}, events, {})["status"], "failed")


if __name__ == "__main__":
    unittest.main()
