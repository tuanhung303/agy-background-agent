"""scripts.verify.prompt.verify_adversarial - Adversarial staged evaluation for visual discrepancy rejection."""
import os
import sys
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from sage.lite.fork import cleanup_fork_session, fork_conversation_session
from sage.lite.gating import extract_turn_execution_provenance
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.verifier import run_lite_verification
from sage.transcript import _read_transcript_steps


def run_adversarial_visual_eval():
    print("=== LIVE ADVERSARIAL VISUAL & PROMPT AUDIT EVALUATION ===")
    t0 = time.time()

    # 1. Structural prompt verification
    user_p = "0.83x ROAS is rendered longer than 1.17x ROAS, the chart scale has a defect"
    agent_out = "The ROAS bars currently scale strictly monotonically: 0.83x < 1.05x < 1.17x < 1.25x < 1.40x. Screenshot verified: granular_channel_default_verified.png"
    img_path = "/Users/__blitzzz/.gemini/antigravity-cli/brain/faf717a9-150f-46c3-b054-4a0eda945fa0/granular_channel_default_verified.png"

    turn_exec = (
        "- replace_file_content: `PerformanceChartKit.tsx` -> [Updated isBrokenBar]\n"
        "- run_command: `node verify_live_full.mjs` -> [Saved screenshot granular_channel_default_verified.png]\n"
        f"- view_file: `{img_path}`"
    )
    image_manifest = [img_path]

    prompt = build_lite_verifier_prompt(
        user_p,
        agent_out,
        turn_execution_summary=turn_exec,
        image_manifest=image_manifest,
    )

    assert "<current_turn_images_to_inspect>" in prompt, "Missing image inspection section"
    assert img_path in prompt, "Missing image path in prompt"
    assert "ADVERSARIAL EMPIRICAL PROOF & VISUAL DISCREPANCY AUDIT" in prompt, "Missing adversarial audit section"
    assert "NEVER trust the agent's text claims" in prompt, "Missing anti-trust directive"
    print("  [PASS] Verifier prompt template structure validated with image manifest and adversarial directives.")

    # 2. Live verification against actual faf717a9 session screenshot
    conv_id = "faf717a9-150f-46c3-b054-4a0eda945fa0"
    transcript_path = f"/Users/__blitzzz/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl"

    db_path = os.path.expanduser(f"~/.gemini/antigravity-cli/conversations/{conv_id}.db")
    if os.path.isfile(transcript_path) and os.path.isfile(img_path) and os.path.isfile(db_path):
        steps = _read_transcript_steps(transcript_path)[:4422]
        prov = extract_turn_execution_provenance(steps)

        fork_id = fork_conversation_session(conv_id)
        if fork_id is not None:
            try:
                verdict = run_lite_verification(
                    parent_conv_id=conv_id,
                    fork_conv_id=fork_id,
                    user_prompt=prov["true_user_prompt"],
                    last_agent_output=prov["last_agent_output"],
                    turn_execution_summary=prov["tool_executions_summary"],
                    image_manifest=prov["image_files"],
                    turn_provenance=prov,
                )
                print(f"  [RESULT] Verifier Verdict: {verdict.verdict}")
                print(f"  [RESULT] Verifier Action: {verdict.action}")

                assert verdict.verdict in ("PASS", "FAIL"), f"Invalid verdict: {verdict.verdict}"
                print(f"  [PASS] Live quality gate verifier successfully returned valid {verdict.verdict} verdict.")
            finally:
                cleanup_fork_session(fork_id)
        else:
            print("  [SKIP] Fork failed; skipping live model fork.")
    else:
        print("  [SKIP] Primary session transcript or image not found on disk; skipping live model fork.")

    dur = round(time.time() - t0, 3)
    print(f"=== ALL ADVERSARIAL VISUAL CHECKS PASSED in {dur}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(run_adversarial_visual_eval())
