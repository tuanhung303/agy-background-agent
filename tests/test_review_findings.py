"""
Regression guards for the code-review findings on the delegation-signals commit.

Every test here failed before its fix. The review-brief guard EXECUTES the git
command the brief hands the reviewer rather than asserting its spelling, because
the original defect was a range that reads as correct and renders empty.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sage.events import DELEGATE_REVIEW_PAYLOAD, PLAYBOOK_SECTIONS, SEVERITY
from sage.facilitation import immediate_settle_message
from sage.policies import _review_payload_text
from sage.task_structure import get_parallelizable_signals
from sage.triage import classify_advice, compute_advice_key


def _steps(paths, prompt="Do the work"):
    """Transcript steps writing `paths` in order, one write per step."""
    out = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": prompt}]
    out += [
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": p}}]}
        for p in paths
    ]
    return out


class TestReviewBriefDiffScope(unittest.TestCase):
    """The reviewer's diff command must actually show the work under review."""

    def _repo_with_uncommitted_work(self, d):
        run = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
        run("init")
        run("config", "user.email", "t@t.t")
        run("config", "user.name", "T")
        with open(os.path.join(d, "shipped.py"), "w") as f:
            f.write("v1\n")
        run("add", "-A")
        run("commit", "-m", "base")
        base = run("rev-parse", "HEAD").stdout.strip()
        # The agent does its work and does NOT commit — the normal case.
        with open(os.path.join(d, "shipped.py"), "w") as f:
            f.write("v2 agent edit\n")
        with open(os.path.join(d, "brand_new.py"), "w") as f:
            f.write("new leg\n")
        return base

    def test_emitted_diff_command_shows_uncommitted_work(self):
        d = tempfile.mkdtemp()
        try:
            base = self._repo_with_uncommitted_work(d)
            text = _review_payload_text({"pinned_goal": "Ship it", "review_base_sha": base})

            m = re.search(r"Diff scope: (git diff \S+)", text)
            self.assertIsNotNone(m, f"no diff-scope command in brief: {text}")
            emitted = m.group(1)
            self.assertNotIn("..HEAD", emitted)

            got = subprocess.run(emitted.split(), cwd=d, capture_output=True, text=True)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertIn("v2 agent edit", got.stdout,
                          "the command the brief hands the reviewer shows no diff")

            # Proof the fix is load-bearing: the old range is empty on this same repo.
            old = subprocess.run(["git", "diff", f"{base}..HEAD"], cwd=d, capture_output=True, text=True)
            self.assertEqual(old.stdout, "", "old range unexpectedly non-empty; test lost its point")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_untracked_files_reachable_via_emitted_intent_to_add(self):
        d = tempfile.mkdtemp()
        try:
            base = self._repo_with_uncommitted_work(d)
            text = _review_payload_text({"pinned_goal": "Ship it", "review_base_sha": base})
            self.assertIn("git add -N", text)
            subprocess.run(["git", "-C", d, "add", "-N", "."], capture_output=True, text=True)
            got = subprocess.run(["git", "-C", d, "diff", base], capture_output=True, text=True)
            self.assertIn("brand_new.py", got.stdout, "new files stayed invisible to the reviewer")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_payload_constant_forbids_the_two_commit_range(self):
        self.assertIn("never base..HEAD", DELEGATE_REVIEW_PAYLOAD)
        self.assertNotIn("base..HEAD diff ONLY", DELEGATE_REVIEW_PAYLOAD)


class TestSeamIsSharingNotRepetition(unittest.TestCase):
    """Assist Mode means two legs write one file, not one leg writing thrice."""

    def test_contiguous_burst_does_not_route_disjoint_legs_to_assist(self):
        for n in range(4, 9):
            others = [f"leg{i}/f{i}.py" for i in range(1, n)]
            # legA iterates on its OWN file three times, back to back.
            res = get_parallelizable_signals(_steps(["legA/a.py"] * 3 + others))
            self.assertNotIn("assist_mode", res["categories"],
                             f"{n} disjoint legs routed to Assist by self-iteration")
            self.assertIn("disjoint_files", res["categories"])

    def test_revisited_file_still_routes_to_assist(self):
        # Work returns to core/a.py after each other file: a genuine seam.
        order = ["core/a.py", "core/b.py", "core/a.py", "core/c.py", "core/a.py", "pkg/d.py"]
        res = get_parallelizable_signals(_steps(order))
        self.assertIn("assist_mode", res["categories"])
        self.assertIn("core/a.py", res["shared_files"])

    def test_integration_file_outranks_high_churn_leaves(self):
        # Five candidates compete for four slots: four revisited leaves at 3 writes each
        # and src/index.ts at 2. Ranking by churn puts every leaf ahead of the barrel
        # file and the [:4] cap then drops the only real seam from the evidence.
        leaves = ["leafA/a.py", "leafB/b.py", "leafC/c.py", "leafD/d.py"]
        order = leaves + ["src/index.ts"] + leaves + ["src/index.ts"] + leaves
        res = get_parallelizable_signals(_steps(order))
        shared = res["shared_files"]
        self.assertEqual(len(shared), 4)
        self.assertEqual(shared[0], "src/index.ts",
                         "the real integration file was evicted by higher-churn leaves")
        # The eviction condition really is present: it is the least-written candidate.
        counts = {"src/index.ts": 2, **{leaf: 3 for leaf in leaves}}
        self.assertTrue(all(counts["src/index.ts"] < counts[f] for f in shared[1:]))

    def test_contiguous_leaf_bursts_are_not_reported_as_the_seam(self):
        order = (["leafA/a.py"] * 3 + ["leafB/b.py"] * 3 + ["leafC/c.py"] * 3
                 + ["leafD/d.py"] * 3 + ["src/index.ts"] * 2)
        shared = get_parallelizable_signals(_steps(order))["shared_files"]
        self.assertEqual(shared, ["src/index.ts"])

    def test_quote_wrapped_paths_still_match_name_rules(self):
        """agy stores tool args JSON-re-encoded; quotes must not defeat basename()."""
        quoted = ['"/repo/src/index.ts"', '"/repo/pkg/leaf.py"',
                  '"/repo/other/x.py"', '"/repo/z/y.py"', '"/repo/src/index.ts"']
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "go"}]
        for p in quoted:
            steps.append({"type": "PLANNER_RESPONSE",
                          "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": p}}]})
        res = get_parallelizable_signals(steps, workspace_root="/repo")
        self.assertIn("src/index.ts", res["shared_files"])
        # Quotes must not survive into any reported path.
        for f in res["shared_files"]:
            self.assertNotIn('"', f)


class TestSettleUsesPinTimeRouting(unittest.TestCase):
    """Settle reports on the decision the pin made; it never re-derives it."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.jpath = os.path.join(self.td.name, "journal.jsonl")
        self.env = patch.dict(os.environ, {"AGY_SAGE_JOURNAL": self.jpath})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def _transcript(self, tool_calls):
        p = os.path.join(self.td.name, "tr.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "go"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "tool_calls": tool_calls}) + "\n")
        return p

    def _entries(self):
        if not os.path.exists(self.jpath):
            return []
        return [json.loads(ln) for ln in open(self.jpath) if ln.strip()]

    def test_runner_does_not_recompute_routing_at_settle(self):
        import sage.runner as runner
        src = open(runner.__file__).read()
        self.assertNotIn("get_parallelizable_signals", src,
                         "settle re-derives routing instead of reading the pin-time verdict")

    def test_missed_delegation_journals_the_real_conv_id(self):
        tr = self._transcript([{"name": "write_to_file", "args": {"TargetFile": "x.py"}}])
        # State as load_and_sync_session_state actually builds it: no conv_id key.
        state = {"delegate_cmd_turn": 1, "task_complexity": "multi_file"}
        self.assertEqual(immediate_settle_message(state, transcript_path=tr, conv_id="c_real"), "")
        entries = self._entries()
        self.assertEqual([e["event"] for e in entries], ["settle_delegate_missed"])
        self.assertEqual(entries[0]["conv_id"], "c_real",
                         "journal entry is unattributable, so it cannot be queried")

    def test_no_journal_when_delegation_was_never_required(self):
        tr = self._transcript([{"name": "write_to_file", "args": {"TargetFile": "x.py"}}])
        state = {"delegate_cmd_turn": 1, "task_complexity": "simple_qa"}
        self.assertEqual(immediate_settle_message(state, transcript_path=tr, conv_id="c1"), "")
        self.assertEqual(self._entries(), [], "a simple_qa turn was logged as a delegation miss")

    def test_no_journal_when_transcript_was_never_read(self):
        state = {"delegate_cmd_turn": 1, "task_complexity": "multi_file"}
        self.assertEqual(immediate_settle_message(state, transcript_path=None, conv_id="c1"), "")
        self.assertEqual(self._entries(), [], "recorded a miss without inspecting anything")

    def test_confirmation_survives_when_subagents_ran(self):
        tr = self._transcript([{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}}])
        state = {"delegate_cmd_turn": 1, "task_complexity": "multi_file"}
        msg = immediate_settle_message(state, transcript_path=tr, conv_id="c1")
        self.assertIn("[WATCH·delegate·confirm]", msg)
        self.assertEqual(self._entries(), [])


class TestEmittedTagsAreDocumented(unittest.TestCase):
    """A sev-1 summon carries no ASK, so an undocumented tag has no routing at all."""

    def test_every_sev1_event_has_a_prompt_playbook_entry(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        content = open(os.path.join(root, "sage", "sage_prompt.md"), encoding="utf-8").read()
        missing = [
            evt for evt, sev in SEVERITY.items()
            if sev == 1 and f"`[EVT·{evt}]`" not in content and f"`[CMD·{evt}]`" not in content
        ]
        self.assertEqual(missing, [], f"sev-1 tags emitted with no documented routing: {missing}")

    def test_tool_threshold_has_a_playbook_section(self):
        from sage.events import EVENT_TOOL_THRESHOLD
        self.assertIn(EVENT_TOOL_THRESHOLD, PLAYBOOK_SECTIONS)


class TestEscalationBudgetDoesNotCompound(unittest.TestCase):
    """One extra grant, added — not a factor stacked on an already-doubled budget."""

    def _fires_before_dedup(self, category, escalating):
        raw = {"status": "off_track", "category": category, "action": "fix it",
               "guidance": "still broken", "confidence": 0.95}
        if escalating:
            raw["escalation"] = "ignored_advice"
        key = compute_advice_key(category, "fix it", "still broken")
        fires = 0
        for count in range(0, 40):
            if classify_advice(raw, seen_advice={key: count})["decision"] == "hold_dedup":
                break
            fires += 1
        return fires

    def test_irreversible_risk_escalation_does_not_reach_eight(self):
        self.assertEqual(self._fires_before_dedup("irreversible_risk", escalating=False), 4)
        self.assertEqual(self._fires_before_dedup("irreversible_risk", escalating=True), 6)

    def test_ordinary_escalation_budget_unchanged(self):
        self.assertEqual(self._fires_before_dedup("architectural_trap", escalating=False), 2)
        self.assertEqual(self._fires_before_dedup("architectural_trap", escalating=True), 4)


if __name__ == "__main__":
    unittest.main()
