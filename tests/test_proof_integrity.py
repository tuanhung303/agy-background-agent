"""Proof structure and local artifact facts, separate from model interpretation."""
import os
import time

import pytest

from sage.lite.proof_validator import _extract_file_paths, validate_empirical_proof


@pytest.mark.parametrize("proof", [None, [], "MFA", [None], [{}], [""], ["valid", 3]])
def test_invalid_proof_shape_is_rejected(proof):
    assert not validate_empirical_proof(proof)[0]


@pytest.mark.parametrize("proof", [
    "Endpoint returned HTTP 200. User can now view the dashboard.",
    "Inspected SSO docs: interactive login is not required.",
    "The requested instructions explain how to run the command later.",
    "Reviewed file /work/release.md, including its TODO examples.",
])
def test_words_do_not_override_the_model_semantic_decision(proof):
    # This is an integrity check, not a claim that these strings prove completion.
    assert validate_empirical_proof([proof]) == (True, "")


def test_nonexistent_media_cannot_be_rescued_by_blocker_word_or_good_item(tmp_path):
    proof = ["MFA was handled", f"Captured screenshot at {tmp_path / 'missing.png'}"]
    valid, reason = validate_empirical_proof(proof)
    assert not valid and "missing or empty" in reason


def test_empty_media_is_not_visual_evidence(tmp_path):
    path = tmp_path / "empty.png"
    path.touch()
    assert not validate_empirical_proof([f"Inspected {path}"])[0]


def test_age_alone_does_not_reject_existing_media(tmp_path):
    path = tmp_path / "capture.png"
    path.write_bytes(b"old capture")
    now = time.time()
    os.utime(path, (now - 100, now - 100))
    provenance = {"turn_start_time": now, "inspected_files": [str(path)], "written_files": ["capture.png", "/elsewhere/capture.png"]}
    valid, reason = validate_empirical_proof([f"Inspected {path}"])
    assert valid and not reason


def test_current_media_and_old_source_execution_are_structurally_valid(tmp_path):
    source = tmp_path / "verify.py"
    source.write_text("pass")
    now = time.time()
    os.utime(source, (now - 100, now - 100))
    image = tmp_path / "new capture.png"
    image.write_bytes(b"capture contents; actual rendering is checked by the model")
    proof = [f"Executed {source} with exit 0", f"Inspected `{image}`"]
    assert validate_empirical_proof(proof) == (True, "")


def test_url_paths_are_not_local_artifacts():
    assert _extract_file_paths("https://example.test/a.png http://example.test/b.svg") == []
    assert _extract_file_paths('See "/tmp/a folder/a.png" and /tmp/b.svg.') == ["/tmp/a folder/a.png", "/tmp/b.svg"]
