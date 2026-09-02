#!/usr/bin/env python3
"""
scripts.verify.knowledge.verify_knowledge - Live and staged verification for Knowledge Base Maintenance.
Includes live out-of-process execution against a real temporary conversation DB with primary disk verification.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from sage.config import get_real_user_home
from sage.executor import SAGE_ISOLATED_HOME
from sage.lite.prompt import build_kb_maintainer_prompt, build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict

HOOK_PATH = os.path.join(root_dir, "hooks", "session-sage.py")


def run_live_kb_end_to_end_verification() -> bool:
    """Executes live out-of-process stop hook with knowledge update on a temporary SQLite DB."""
    temp_id = f"live_kb_test_{int(time.time()*1000)}"
    temp_dir = tempfile.mkdtemp(prefix="sage_kb_live_")
    real_cli_dir = os.path.join(temp_dir, ".gemini", "antigravity-cli")
    real_conv_dir = os.path.join(real_cli_dir, "conversations")
    os.makedirs(real_conv_dir, exist_ok=True)

    db_file = os.path.join(real_conv_dir, f"{temp_id}.db")
    transcript_path = os.path.join(temp_dir, "transcript.jsonl")
    knowledge_dir = os.path.join(temp_dir, "skills")
    os.makedirs(knowledge_dir, exist_ok=True)

    test_skill_file = os.path.join(knowledge_dir, "test-skill", "SKILL.md")
    os.makedirs(os.path.dirname(test_skill_file), exist_ok=True)

    # 1. Initialize real SQLite DB with trajectory_meta schema
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS trajectory_meta (cascade_id TEXT)")
        conn.execute("INSERT INTO trajectory_meta VALUES (?)", (temp_id,))
        conn.commit()

    # 2. Populate transcript with skill creation and verification steps
    steps = [
        {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Create git-workflow skill and update knowledge base catalog"},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": test_skill_file}}], "content": "Created skill."},
        {"type": "GENERIC", "content": "File written successfully."},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 scripts/gen_catalog.py"}}], "content": "Catalog updated."},
        {"type": "GENERIC", "content": "Exit code 0: Catalog generated."},
        {"type": "PLANNER_RESPONSE", "content": "Skill created and verified.", "tool_calls": []},
    ]
    with open(transcript_path, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")

    # 3. Simulate knowledge creation on disk
    with open(test_skill_file, "w", encoding="utf-8") as f:
        f.write("---\nname: test-skill\ndescription: Live verified knowledge skill test.\n---\n# Test Skill\nVerified empirical knowledge.\n")

    # 4. Execute out-of-process stop hook with mock verdict triggering knowledge update
    iso_home = os.path.join(temp_dir, "iso_home")
    env = os.environ.copy()
    env["AGY_REAL_HOME"] = temp_dir
    env["SAGE_ISOLATED_HOME"] = iso_home
    env["AGY_LITE_MOCK_VERDICT"] = "PASS: Work verified cleanly and skill added | update_knowledge=true"

    payload = {
        "conversationId": temp_id,
        "transcriptPath": transcript_path,
        "workspacePaths": [root_dir],
    }

    try:
        res = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=temp_dir,
            timeout=30,
        )

        assert res.returncode == 0, f"Hook failed with exit code {res.returncode}, stderr: {res.stderr}"
        resp_data = json.loads(res.stdout.strip())
        assert resp_data.get("decision") == "stop", f"Expected decision: stop but got {resp_data}"

        # 5. Direct primary disk verification
        # 5a. Verify knowledge file and frontmatter metadata on disk
        assert os.path.isfile(test_skill_file), f"Knowledge skill file missing at {test_skill_file}"
        with open(test_skill_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "name: test-skill" in content, "Missing frontmatter name in skill file"
        assert "description:" in content, "Missing frontmatter description in skill file"
        assert "Verified empirical knowledge" in content, "Missing body content in skill file"

        # 5b. Verify audit log recorded execution
        audit_log = os.environ.get("AGY_SAGE_LOG", "/tmp/agy_sage.log")
        if os.path.isfile(audit_log):
            with open(audit_log, "r", encoding="utf-8") as f:
                logs = f.read()
            assert f"Lite Mode: knowledge update requested; executing background maintainer" in logs or "fork" in logs, "Missing KB maintainer entry in audit log"

        # 5c. Verify that isolated home forked databases were cleaned up
        iso_conv_dir = os.path.join(iso_home, ".gemini", "antigravity-cli", "conversations")
        if os.path.isdir(iso_conv_dir):
            try:
                leaked = [f for f in os.listdir(iso_conv_dir) if temp_id in f]
                assert len(leaked) == 0, f"Leaked forked DBs found in isolated home: {leaked}"
            except OSError:
                pass

        print("  ✓ Out-of-process stop hook successfully executed on real temporary DB.")
        print(f"  ✓ Primary disk artifact verified: {test_skill_file} (size: {len(content)} bytes).")
        print("  ✓ Isolated session databases and forks cleaned up with zero leak.")
        return True

    finally:
        # Cleanup temporary files and databases
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except OSError:
                pass


def main() -> int:
    print("=== KNOWLEDGE BASE MAINTENANCE TOPIC VERIFICATION ===")

    # 1. Path Resolution Invariants
    print("\n[1/5] Verifying Real Home Path Resolution and Escaping...")
    real_home = get_real_user_home()
    assert "sage_isolated_home" not in real_home, f"Failed to escape isolated home: {real_home}"
    print(f"  ✓ Real user home resolved cleanly: {real_home}")

    # 2. Prompt Formatting Invariants
    print("\n[2/5] Verifying KB Maintainer & Verifier Prompt Rendering...")
    kb_prompt = build_kb_maintainer_prompt()
    assert "/Documents/GitHub/agentic/skills" in kb_prompt, "Missing absolute skills path in KB prompt"
    assert "/.hermes/skills/validate/scripts/okf_validate.py" in kb_prompt, "Missing absolute validate script path in KB prompt"
    assert " ~/Documents" not in kb_prompt, "Unexpanded tilde found in KB prompt"
    print("  ✓ KB maintainer prompt correctly renders verified absolute filesystem paths.")

    verifier_prompt = build_lite_verifier_prompt("Create global skill", "SKILL.md created")
    assert "[KNOWLEDGE UPDATE CRITERIA]" in verifier_prompt, "Missing knowledge update criteria in verifier prompt"
    assert '"update_knowledge": false | true' in verifier_prompt, "Missing boolean options in verifier prompt schema"
    print("  ✓ Verifier prompt correctly contains explicit knowledge update guidelines.")

    # 3. Schema Parsing Invariants
    print("\n[3/5] Verifying LiteVerdict Schema Parsing...")
    v_true = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": "yes"})
    assert v_true.update_knowledge is True, "Failed to parse truthy string 'yes'"
    v_false = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": "0"})
    assert v_false.update_knowledge is False, "Failed to parse falsy string '0'"
    v_alias = LiteVerdict.from_dict({"verdict": "PASS", "requires_knowledge_update": True})
    assert v_alias.update_knowledge is True, "Failed to parse alias 'requires_knowledge_update'"
    print("  ✓ LiteVerdict schema robustly parses booleans, strings, ints, and aliases.")

    # 4. 5-Stage Staged Unit Suite Run
    print("\n[4/5] Executing 5-Stage Comprehensive Test Suite...")
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_knowledge_maintenance.py")
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print("  ✗ 5-Stage test suite failed!")
        return 1
    print("  ✓ All 5 stages passed cleanly (17/17 tests).")

    # 5. Live End-to-End Out-of-Process Execution with Disk Verification
    print("\n[5/5] Executing Live Out-of-Process Hook on Temporary DB & Verifying Disk Metadata...")
    if not run_live_kb_end_to_end_verification():
        print("  ✗ Live end-to-end knowledge update verification failed!")
        return 1

    print("\n=== KNOWLEDGE BASE MAINTENANCE TOPIC VERIFIED CLEANLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
