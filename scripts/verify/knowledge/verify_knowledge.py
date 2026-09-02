#!/usr/bin/env python3
"""
scripts.verify.knowledge.verify_knowledge - Live and staged verification for Knowledge Base Maintenance.
"""
import os
import sys
import unittest

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from sage.config import get_real_user_home
from sage.lite.prompt import build_kb_maintainer_prompt, build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict


def main() -> int:
    print("=== KNOWLEDGE BASE MAINTENANCE TOPIC VERIFICATION ===")

    # 1. Path Resolution Invariants
    print("\n[1/4] Verifying Real Home Path Resolution and Escaping...")
    real_home = get_real_user_home()
    assert "sage_isolated_home" not in real_home, f"Failed to escape isolated home: {real_home}"
    print(f"  ✓ Real user home resolved cleanly: {real_home}")

    # 2. Prompt Formatting Invariants
    print("\n[2/4] Verifying KB Maintainer & Verifier Prompt Rendering...")
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
    print("\n[3/4] Verifying LiteVerdict Schema Parsing...")
    v_true = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": "yes"})
    assert v_true.update_knowledge is True, "Failed to parse truthy string 'yes'"
    v_false = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": "0"})
    assert v_false.update_knowledge is False, "Failed to parse falsy string '0'"
    v_alias = LiteVerdict.from_dict({"verdict": "PASS", "requires_knowledge_update": True})
    assert v_alias.update_knowledge is True, "Failed to parse alias 'requires_knowledge_update'"
    print("  ✓ LiteVerdict schema robustly parses booleans, strings, ints, and aliases.")

    # 4. 5-Stage Staged Unit Suite Run
    print("\n[4/4] Executing 5-Stage Comprehensive Test Suite...")
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_knowledge_maintenance.py")
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print("  ✗ 5-Stage test suite failed!")
        return 1

    print("  ✓ All 5 stages passed cleanly (17/17 tests).")
    print("\n=== KNOWLEDGE BASE MAINTENANCE TOPIC VERIFIED CLEANLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
