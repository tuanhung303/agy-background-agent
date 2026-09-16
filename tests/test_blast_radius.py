"""tests.test_blast_radius - Tests for deterministic bounded AST blast radius detection."""
import os
import tempfile
import pytest

from sage.lite.evidence import detect_blast_radius_gap, find_direct_workspace_consumers
from sage.lite.gating import extract_turn_execution_provenance
from sage.lite.prompt import build_lite_verifier_prompt


def test_detect_blast_radius_gap_empty_or_non_source():
    # Empty written files
    assert detect_blast_radius_gap(set(), set(), []) is None

    # Only test files written
    assert detect_blast_radius_gap({"tests/test_foo.py"}, set(), []) is None
    assert detect_blast_radius_gap({"foo_test.py"}, set(), []) is None

    # Only docs or non-python written
    assert detect_blast_radius_gap({"README.md", "data.json", "plan.md"}, set(), []) is None


def test_find_direct_workspace_consumers_in_temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create module A: pkg/utils.py
        pkg_dir = os.path.join(tmpdir, "pkg")
        os.makedirs(pkg_dir)
        utils_py = os.path.join(pkg_dir, "utils.py")
        with open(utils_py, "w") as f:
            f.write("def helper(): return 42\n")

        # Create module B importing A: pkg/service.py
        service_py = os.path.join(pkg_dir, "service.py")
        with open(service_py, "w") as f:
            f.write("from pkg.utils import helper\ndef run(): return helper()\n")

        # Create module C not importing A: pkg/other.py
        other_py = os.path.join(pkg_dir, "other.py")
        with open(other_py, "w") as f:
            f.write("def standalone(): return 0\n")

        consumers = find_direct_workspace_consumers("pkg/utils.py", workspace_root=tmpdir)
        assert consumers == ["pkg/service.py"]


def test_detect_blast_radius_gap_unverified_consumer():
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "pkg")
        os.makedirs(pkg_dir)
        utils_py = os.path.join(pkg_dir, "utils.py")
        with open(utils_py, "w") as f:
            f.write("def helper(): return 42\n")

        service_py = os.path.join(pkg_dir, "service.py")
        with open(service_py, "w") as f:
            f.write("from pkg.utils import helper\ndef run(): return helper()\n")

        # Case 1: Modified utils.py, but service.py was neither tested nor inspected
        diag = detect_blast_radius_gap(
            written_files={"pkg/utils.py"},
            inspected_files=set(),
            executed_commands=["python -c 'print(1)'"],
            workspace_root=tmpdir,
        )
        assert diag is not None
        assert "pkg/utils.py" in diag
        assert "pkg/service.py" in diag
        assert "Shared source code was modified with unverified downstream callers" in diag

        # Case 2: Broad test executed (e.g. pytest) -> gap resolved
        assert detect_blast_radius_gap(
            written_files={"pkg/utils.py"},
            inspected_files=set(),
            executed_commands=["pytest"],
            workspace_root=tmpdir,
        ) is None

        # Case 3: Consumer inspected -> gap resolved
        assert detect_blast_radius_gap(
            written_files={"pkg/utils.py"},
            inspected_files={"pkg/service.py"},
            executed_commands=[],
            workspace_root=tmpdir,
        ) is None

        # Case 4: Consumer specifically executed/tested -> gap resolved
        assert detect_blast_radius_gap(
            written_files={"pkg/utils.py"},
            inspected_files=set(),
            executed_commands=["pytest tests/test_service.py"],
            workspace_root=tmpdir,
        ) is None


def test_build_lite_verifier_prompt_injects_blast_radius_diagnostic():
    diag_text = "- Modified module `sage/lite/evidence.py` has unverified downstream caller(s): `sage/lite/gating.py`"
    prompt = build_lite_verifier_prompt(
        user_prompt="Refactor evidence normalization",
        last_agent_output="Refactored evidence.",
        turn_provenance={"blast_radius_diagnostic": diag_text},
    )
    assert "<blast_radius_diagnostic>" in prompt
    assert "</blast_radius_diagnostic>" in prompt
    assert diag_text in prompt
    assert "Shared source code was modified with unverified downstream callers in the workspace" in prompt
