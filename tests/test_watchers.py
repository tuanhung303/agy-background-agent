#!/usr/bin/env python3
"""
tests.test_watchers - Unit tests for subagent and background task watchers.
"""

import unittest
from datetime import datetime, timedelta, timezone

from sage.watchers import (
    get_active_background_tasks,
    get_active_subagents,
)


class TestWatchers(unittest.TestCase):
    def test_subagent_role_retention_on_conversation_id_resolution(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Scout", "Goal": "Explore codebase"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned subagent with "conversationId": "sub_scout_01"',
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["conversation_id"], "sub_scout_01")
        self.assertEqual(subs[0]["role"], "Scout")

    def test_subagent_role_catalog_extraction(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {
                            "Subagents": [
                                {"Role": "Scout"},
                                {"Role": "Implementer"},
                                {"Role": "QA"},
                            ]
                        },
                    }
                ],
            }
        ]
        subs = get_active_subagents(steps)
        roles = [s["role"] for s in subs]
        self.assertIn("Scout", roles)
        self.assertIn("Implementer", roles)
        self.assertIn("QA", roles)

    def test_failed_subagent_invocation_does_not_remain_active(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "name": "invoke_subagent",
                    "args": {"Subagents": [{"Role": "QA"}]},
                }],
            },
            {
                "type": "GENERIC",
                "content": "Error: failed to invoke subagent because capacity is exhausted",
            },
        ]
        self.assertEqual(get_active_subagents(steps), [])

    def test_failed_invocation_only_clears_corresponding_pending_batch(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}]}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}},
                ],
            },
            {"type": "GENERIC", "content": "Error: failed to invoke subagent"},
            {"type": "GENERIC", "content": 'Spawned "conversationId": "sub_qa_1"'},
        ]
        active = get_active_subagents(steps)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["conversation_id"], "sub_qa_1")
        self.assertEqual(active[0]["role"], "QA")

    def test_subagent_age_tracking(self):
        past_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "created_at": past_ts,
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Worker"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Created subagent "conversationId": "sub_worker_age"',
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 1)
        self.assertGreaterEqual(subs[0]["age_seconds"], 100.0)

    def test_subagent_completion_via_sender_message(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Implementer"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned "conversationId": "sub_impl_99"',
            },
            {
                "type": "USER_INPUT",
                "content": "sender=sub_impl_99 Task complete with tests passing.",
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 0)

    def test_subagent_idle_detection(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Scout"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned "conversationId": "sub_idle_1"',
            },
            {
                "type": "SYSTEM_MESSAGE",
                "content": "Subagent sub_idle_1 has gone idle",
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 0)

    def test_subagent_termination_detection(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "QA"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned "conversationId": "sub_kill_target"',
            },
            {
                "type": "SYSTEM_MESSAGE",
                "content": "Killed subagent 'sub_kill_target'",
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 0)

    def test_multiple_subagents_partial_completion_and_distinct_roles(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {
                            "Subagents": [
                                {"Role": "Scout"},
                                {"Role": "Implementer"},
                            ]
                        },
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned:\n"conversationId": "sub_scout_multi"\n"conversationId": "sub_impl_multi"',
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 2)

        # Scout finishes
        steps.append({
            "type": "SYSTEM_MESSAGE",
            "content": "[Message] sender=sub_scout_multi Research completed.",
        })
        subs_after = get_active_subagents(steps)
        self.assertEqual(len(subs_after), 1)
        self.assertEqual(subs_after[0]["conversation_id"], "sub_impl_multi")
        self.assertEqual(subs_after[0]["role"], "Implementer")

    def test_manage_subagents_kill_and_kill_all(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Worker"}]},
                    }
                ],
            },
            {
                "type": "GENERIC",
                "content": 'Spawned "conversationId": "sub_to_kill"',
            },
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "manage_subagents",
                        "args": {"Action": "kill", "ConversationIds": ["sub_to_kill"]},
                    }
                ],
            },
        ]
        subs = get_active_subagents(steps)
        self.assertEqual(len(subs), 0)

    def test_background_task_tracking_and_grace(self):
        now_dt = datetime.now(timezone.utc)
        steps = [
            {
                "type": "GENERIC",
                "created_at": (now_dt - timedelta(seconds=20)).isoformat(),
                "content": "Tool is running as a background task with task id: conv1/task-100\nTask Description: cargo test",
            }
        ]
        tasks = get_active_background_tasks(steps, conv_id="conv1")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "conv1/task-100")
        self.assertLess(tasks[0]["age_seconds"], 300.0)

    def test_background_task_completion(self):
        steps = [
            {
                "type": "GENERIC",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": "Tool is running as a background task with task id: conv1/task-200\nTask Description: build",
            },
            {
                "type": "USER_INPUT",
                "content": "sender=conv1/task-200 Task finished with result: OK",
            },
        ]
        tasks = get_active_background_tasks(steps, conv_id="conv1")
        self.assertEqual(len(tasks), 0)


if __name__ == "__main__":
    unittest.main()
