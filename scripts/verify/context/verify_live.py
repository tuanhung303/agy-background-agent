"""scripts.verify.context.verify_live - Out-of-process live verification for user context distillation and compaction handling."""
import json
import os
import subprocess
import sys
import tempfile
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from sage.lite.gating import extract_turn_execution_provenance
from sage.transcript import extract_session_and_turn_data
from sage.user_context import extract_substantive_user_context, is_trivial_acknowledgment


def run_live_user_context_verification():
    print("=== LIVE OUT-OF-PROCESS USER CONTEXT & COMPACTION RUNTIME VERIFICATION ===")
    t0 = time.time()

    temp_dir = tempfile.mkdtemp(prefix="agy_context_live_")
    try:
        # Scenario 1: Multi-turn substantive request followed by trivial acks
        print("\n--- Scenario 1: Multi-turn Substantive Request with 'ok' and 'continue' Follow-ups ---")
        s1_file = os.path.join(temp_dir, "s1_transcript.jsonl")
        s1_lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>Implement user authentication with JWT tokens and bcrypt password hashing</USER_REQUEST>", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Created auth plan", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/app/auth.py"}}]},
            {"type": "GENERIC", "content": "File /app/auth.py written successfully."},
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>ok proceed</USER_REQUEST>", "created_at": "2026-09-01T10:05:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Added unit tests", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/app/test_auth.py"}}]},
            {"type": "GENERIC", "content": "File /app/test_auth.py written successfully."},
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>continue</USER_REQUEST>", "created_at": "2026-09-01T10:10:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Ran test suite, all 12 tests passed", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/"}}]},
            {"type": "GENERIC", "content": "12 passed in 0.45s"},
        ]
        with open(s1_file, "w", encoding="utf-8") as f:
            for item in s1_lines:
                f.write(json.dumps(item) + "\n")

        # Execute out-of-process python CLI helper to extract provenance
        proc_script = (
            "import json, sys\n"
            "from sage.transcript import _read_transcript_steps\n"
            "from sage.lite.gating import extract_turn_execution_provenance\n"
            "steps = _read_transcript_steps(sys.argv[1])\n"
            "prov = extract_turn_execution_provenance(steps)\n"
            "print(json.dumps(prov))\n"
        )
        res1 = subprocess.run(
            [sys.executable, "-c", proc_script, s1_file],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert res1.returncode == 0, f"Process failed with stderr: {res1.stderr}"
        prov1 = json.loads(res1.stdout.strip())

        print(f"  Process Exit Code: {res1.returncode}")
        print(f"  Latest Raw Prompt: {prov1.get('latest_user_prompt')}")
        print(f"  Primary Goal:      {prov1.get('primary_goal')}")
        print(f"  True Distilled Prompt:\n    {prov1.get('true_user_prompt').replace(chr(10), chr(10) + '    ')}")

        assert "Implement user authentication with JWT tokens" in prov1["true_user_prompt"], "Primary goal missing"
        assert "ok proceed" in prov1["true_user_prompt"], "Intermediate follow-up missing"
        assert "continue" in prov1["true_user_prompt"], "Latest ack missing"
        assert prov1["latest_user_prompt"] == "continue", "Latest prompt mismatch"
        print("  [PASS] Scenario 1 multi-turn provenance distilled with primary goal and follow-ups.")

        # Scenario 2: Transcript Compaction Checkpoint Recovery
        print("\n--- Scenario 2: Transcript Compaction Checkpoint with Follow-up ---")
        s2_file = os.path.join(temp_dir, "s2_transcript.jsonl")
        s2_lines = [
            {
                "type": "CHECKPOINT",
                "summary": "<summary>Refactored billing service: migrated payment gateway to Stripe Elements and added webhook handlers for subscription lifecycle events.</summary>",
                "created_at": "2026-09-01T09:00:00Z",
            },
            {"type": "PLANNER_RESPONSE", "content": "Loaded checkpoint state", "tool_calls": []},
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>run tests and push to remote</USER_REQUEST>", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Tests passed cleanly, pushed to origin/main", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "git push origin main"}}]},
            {"type": "GENERIC", "content": "Everything up-to-date."},
        ]
        with open(s2_file, "w", encoding="utf-8") as f:
            for item in s2_lines:
                f.write(json.dumps(item) + "\n")

        res2 = subprocess.run(
            [sys.executable, "-c", proc_script, s2_file],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert res2.returncode == 0, f"Process failed with stderr: {res2.stderr}"
        prov2 = json.loads(res2.stdout.strip())

        print(f"  Process Exit Code: {res2.returncode}")
        print(f"  Has Compaction:    {prov2.get('has_compaction')}")
        print(f"  True Distilled Prompt:\n    {prov2.get('true_user_prompt').replace(chr(10), chr(10) + '    ')}")

        assert prov2["has_compaction"] is True, "Expected has_compaction=True"
        assert "Refactored billing service: migrated payment gateway" in prov2["true_user_prompt"], "Compacted summary missing"
        assert "run tests and push to remote" in prov2["true_user_prompt"], "Active prompt missing"
        print("  [PASS] Scenario 2 compacted history correctly reconstructed and linked with active turn.")

        # Scenario 3: End-to-end session stop audit out-of-process runner
        print("\n--- Scenario 3: Out-of-Process session-sage.py CLI Stop Hook Execution ---")
        payload = {
            "conversationId": "live_context_eval_session",
            "transcriptPath": s1_file,
            "workspacePaths": [repo_root],
        }
        sage_env = os.environ.copy()
        res3 = subprocess.run(
            [sys.executable, os.path.join(repo_root, "hooks", "session-sage.py"), "post_invocation"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=sage_env,
        )
        print(f"  Process Exit Code: {res3.returncode}")
        print(f"  Stdout Payload:    {res3.stdout.strip()}")
        if res3.stderr.strip():
            print(f"  Stderr:            {res3.stderr.strip()}")

        assert res3.returncode == 0, f"Runner failed with exit code {res3.returncode}, stderr: {res3.stderr}"
        stop_payload = json.loads(res3.stdout.strip()) if res3.stdout.strip() else {}
        assert "terminationBehavior" not in stop_payload or stop_payload.get("injectSteps") == [], "Invalid stop payload"
        print("  [PASS] Scenario 3 out-of-process session-sage CLI executed and terminated cleanly.")

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    elapsed = time.time() - t0
    print(f"\n=== ALL LIVE CONTEXT & COMPACTION EVALUATIONS PASSED ({elapsed:.3f}s) ===")


if __name__ == "__main__":
    run_live_user_context_verification()
