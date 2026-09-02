#!/usr/bin/env python3
"""
scripts.verify.stop_verifier.verify_stop_verifier - Live end-to-end verification runner for generalized Stop Verifier.
"""
import os
import sys
import tempfile

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from sage.lite.gating import is_plan_or_qa_intent
from sage.lite.proof_validator import validate_empirical_proof
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict


def main() -> int:
    print("=== STOP VERIFIER GENERALIZATION LIVE VERIFICATION ===")

    # 1. Intent Gate Verification
    print("\n[1/5] Testing Intent Gate (Plan / QA vs Implementation)...")
    plan_prompts = [
        "/plan make a new branch and review recent commits",
        "/qa explain the database architecture",
        "make a plan first before doing any changes",
        "brainstorm the test cases, short, quick",
    ]
    for p in plan_prompts:
        assert is_plan_or_qa_intent(p), f"Failed to identify plan intent: {p}"
        print(f"  ✓ Recognized plan/QA intent: {p[:45]}...")

    impl_prompts = [
        "implement user authentication endpoint",
        "fix bug in payment webhook handler",
    ]
    for p in impl_prompts:
        assert not is_plan_or_qa_intent(p), f"False positive plan intent: {p}"
        print(f"  ✓ Recognized implementation intent: {p[:45]}...")

    # 2. Visual / Website / SVG / Release Proof Disqualification
    print("\n[2/5] Testing Visual / Website / SVG / Release Pseudo-Proof Disqualification...")
    pseudo_proofs = ["vite build finished in 2.1s", "15/15 unit tests passed", "pre-push hook passed", "git push origin staging -> 658423aa..b1e599b1"]
    valid, reason = validate_empirical_proof(pseudo_proofs)
    assert not valid, "Failed to disqualify pseudo-proofs"
    print(f"  ✓ Pseudo-proof correctly disqualified: {reason}")

    # 3. Visual, Sandbox & Provenance Proof Acceptance
    print("\n[3/5] Testing Visual, Sandbox & Provenance Empirical Proof Acceptance...")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
        tmp_img.write(b"PNG_MOCK")
        img_path = tmp_img.name

    try:
        genuine_proofs = [
            f"Rendered visual preview screenshot captured at {img_path}",
            "Executed live CLI integration test in ephemeral sandbox /tmp/sandbox with exit code 0",
        ]
        valid, reason = validate_empirical_proof(genuine_proofs)
        assert valid, f"Valid proofs rejected: {reason}"
        print(f"  ✓ Genuine empirical proofs accepted: {genuine_proofs[0][:40]}...")

        # Test stale artifact rejection when turn started after file creation
        turn_prov_stale = {
            "turn_start_time": 9999999999.0,
            "written_files": ["/other/file.ts"],
            "generated_images": [],
        }
        stale_valid, stale_reason = validate_empirical_proof([f"Captured screenshot at {img_path}"], turn_provenance=turn_prov_stale)
        assert not stale_valid, "Failed to reject stale recycled artifact"
        print(f"  ✓ Stale recycled artifact correctly rejected: {stale_reason}")
    finally:
        if os.path.exists(img_path):
            os.remove(img_path)

    # 4. Prompt Integrity Verification
    print("\n[4/5] Testing Generalized Verifier Prompt Invariants...")
    prompt = build_lite_verifier_prompt("Build SVG and web app", "Output delivered.")
    required_sections = [
        "0. INTENT TYPE",
        "1. AUTONOMY & ANTI-DEFERRAL",
        "2. COMPLETENESS, BLAST RADIUS & REGRESSION IMMUNITY",
        "3. ESCALATION & SAFETY FAILURE",
        "4. MISSING DOMAIN EMPIRICAL PROOF",
        "STRICT DISQUALIFICATION",
        "PRE-FLIGHT ADVERSARIAL PROTOCOL",
        '"verdict": "PASS" | "FAIL"',
    ]
    for sec in required_sections:
        assert sec in prompt, f"Missing section in prompt: {sec}"
        print(f"  ✓ Verified invariant section present: {sec}")

    # 5. Multi-Domain Verification Matrix & Schema Serialization
    print("\n[5/5] Testing Multi-Domain Matrix (IT, Office, Web, Backend, Data)...")
    domain_cases = [
        ("IT/DevOps", ["Executed terraform validate in sandbox: Success! 0 errors."], True),
        ("IT Deferral", ["Wrote main.tf, user can run terraform apply later"], False),
        ("Office", ["Audited report.xlsx: 12/12 formulas verified with no #REF! errors"], True),
        ("Backend Regression", [
            "Executed pytest tests/ across 3 services with exit code 0",
            "Live curl POST http://127.0.0.1:8000/api/v1/users returned HTTP 201 with verified UUID",
        ], True),
        ("Data SQL", ["Executed query against test db: returned 1420 rows with valid schema"], True),
    ]
    for name, proofs, expected in domain_cases:
        is_val, _ = validate_empirical_proof(proofs)
        assert is_val == expected, f"Failed domain test for {name}"
        print(f"  ✓ Verified {name} domain behavior")

    v = LiteVerdict(verdict="PASS", comment="Verified live.", proof=["Captured /tmp/test.png"])
    d = v.to_dict()
    assert d["verdict"] == "PASS" and d["proof"] == ["Captured /tmp/test.png"]
    print("  ✓ Schema serialization verified cleanly.")

    print("\n=== ALL 5 VERIFICATION CHANNELS PASSED CLEANLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
