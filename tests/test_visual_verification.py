"""Unit tests for visual deliverable layout verification enforcement."""
import json
import pytest

from sage.lite.evidence import detect_visual_verification_gap
from sage.lite.gating import extract_turn_execution_provenance
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict
from sage.lite.verifier import run_lite_verification


def test_detect_visual_verification_gap_when_unrendered():
    """Detects gap when visual presentation artifacts are modified without render tools."""
    written = {"01-architecture/hld/sbc_ark_hld_deck.html", "sbc_ark_hld_deck.pptx"}
    prompt = "for the hld slide deck use the default theme in /blitzz-design"
    commands = [
        "uv run --with python-pptx python build_deck.py",
        "python3 -c 'import pptx; prs = pptx.Presentation(\"deck.pptx\"); print(len(prs.slides))'",
        "git status",
    ]
    inspected = {"README.md"}
    diag = detect_visual_verification_gap(written, prompt, commands, inspected, True)
    assert diag is not None
    assert "sbc_ark_hld_deck.html" in diag or "sbc_ark_hld_deck.pptx" in diag
    assert "python-pptx shape loops" in diag


def test_detect_visual_verification_gap_cleared_by_render_command():
    """Gap is cleared when an actual visual rendering tool (e.g. soffice, pdftoppm) was executed."""
    written = {"sbc_ark_hld_deck.pptx"}
    prompt = "update slides"
    commands = [
        "soffice --headless --convert-to pdf sbc_ark_hld_deck.pptx",
        "pdftoppm -png -r 100 sbc_ark_hld_deck.pdf preview_slides/slide",
    ]
    inspected = set()
    diag = detect_visual_verification_gap(written, prompt, commands, inspected, True)
    assert diag is None


def test_detect_visual_verification_gap_cleared_by_image_inspection():
    """Gap is cleared when the agent inspects a rendered preview image."""
    written = {"sbc_ark_hld_deck.html"}
    prompt = "update slides"
    commands = ["npm run build"]
    inspected = {"preview_slides/slide-01.png"}
    diag = detect_visual_verification_gap(written, prompt, commands, inspected, True)
    assert diag is None


def test_detect_visual_verification_gap_not_triggered_for_code():
    """Regular code or text files do not trigger visual verification gap."""
    written = {"parser.py", "test_parser.py"}
    prompt = "fix parser error handling"
    commands = ["pytest tests/"]
    inspected = {"parser.py"}
    diag = detect_visual_verification_gap(written, prompt, commands, inspected, True)
    assert diag is None


def test_gating_provenance_populates_visual_diagnostic():
    """extract_turn_execution_provenance attaches visual_verification_diagnostic."""
    steps = [
        {"type": "USER_INPUT", "content": "remake the hld slide deck with blitzz-design"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Edited slides.",
            "tool_calls": [
                {
                    "name": "write_to_file",
                    "args": {"TargetFile": "/path/deck.html"},
                },
                {
                    "name": "run_command",
                    "args": {"CommandLine": "python3 build_deck.py"},
                },
            ],
        },
    ]
    prov = extract_turn_execution_provenance(steps)
    assert prov["visual_verification_diagnostic"] is not None
    assert "deck.html" in prov["visual_verification_diagnostic"]


def test_prompt_injects_visual_verification_diagnostic_block():
    """build_lite_verifier_prompt injects <visual_verification_diagnostic> tag."""
    prov = {
        "visual_verification_diagnostic": "Visual deliverables were modified without headless render.",
    }
    prompt = build_lite_verifier_prompt(
        user_prompt="remake slides",
        last_agent_output="Done slides.",
        turn_provenance=prov,
    )
    assert "<visual_verification_diagnostic>" in prompt
    assert "Visual deliverables were modified without headless render." in prompt
    assert "</visual_verification_diagnostic>" in prompt


def test_verifier_rejects_pass_when_visual_unrendered(monkeypatch):
    """When visual_verification_diagnostic is present, an unverified PASS is rejected."""
    prov = {
        "visual_verification_diagnostic": "Visual presentation unrendered.",
    }
    attempts = []

    def mock_exec(prompt, fork_id, deadline, cwd):
        attempts.append(prompt)
        if len(attempts) == 1:
            return LiteVerdict(verdict="PASS", completion="complete", proof=["python-pptx shape count 20"])
        return LiteVerdict(verdict="FAIL", completion="incomplete", action="Render the slides and inspect preview images.")

    monkeypatch.setattr("sage.lite.verifier._execute_verdict", mock_exec)
    verdict = run_lite_verification(
        parent_conv_id="test-parent",
        fork_conv_id="test-fork",
        user_prompt="remake slides",
        last_agent_output="Done slides.",
        cwd="/tmp",
        turn_provenance=prov,
    )
    assert len(attempts) == 2
    assert verdict.verdict == "FAIL"
    assert "Render the slides" in verdict.action


def test_sbc_azure_transcript_triggers_visual_diagnostic():
    """Verify that the exact transcript from SBC Azure Documentation Cleanup flags visual gap."""
    from pathlib import Path
    transcript_path = Path("/Users/__blitzzz/.gemini/antigravity/brain/3aac567d-8430-4132-96d6-54869a915413/.system_generated/logs/transcript.jsonl")
    if not transcript_path.exists():
        pytest.skip("SBC Azure transcript not available locally")
    lines = [json.loads(line) for line in transcript_path.read_text().splitlines() if line.strip()]
    steps_up_to_522 = [s for s in lines if s.get("step_index", 0) <= 522]
    prov = extract_turn_execution_provenance(steps_up_to_522)
    assert prov["visual_verification_diagnostic"] is not None
    assert "visual layout" in prov["visual_verification_diagnostic"].lower()

