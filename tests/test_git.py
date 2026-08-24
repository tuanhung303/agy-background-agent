#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
import unittest

from sage.git import get_git_diff


class TestGit(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_git_diff_no_editing_tools(self):
        diff = get_git_diff([self.test_dir], turn_tool_names={"view_file", "grep_search"})
        self.assertEqual(diff, "None (no file-editing tools invoked in turn)")

    def test_get_git_diff_with_git_repo(self):
        # Init test git repo
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        sample_file = os.path.join(self.test_dir, "sample.txt")
        with open(sample_file, "w") as f:
            f.write("Initial content\n")
        subprocess.run(["git", "add", "sample.txt"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.test_dir, capture_output=True)

        # Modify file
        with open(sample_file, "w") as f:
            f.write("Modified content\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        self.assertIn("Modified content", diff)
        self.assertIn("sample.txt", diff)

    def test_get_git_diff_staged_and_untracked(self):
        # Init test git repo
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        sample_file = os.path.join(self.test_dir, "staged.txt")
        with open(sample_file, "w") as f:
            f.write("Staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=self.test_dir, capture_output=True)

        untracked_file = os.path.join(self.test_dir, "untracked.txt")
        with open(untracked_file, "w") as f:
            f.write("Untracked content\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"multi_replace_file_content"})
        self.assertIn("Staged changes", diff)
        self.assertIn("staged.txt", diff)
        self.assertIn("untracked.txt", diff)
        self.assertIn("Changed lines: 2", diff)

    def test_get_git_diff_untracked_multiline_and_binary_and_preview(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        # 7-line untracked file
        untracked_file = os.path.join(self.test_dir, "untracked.py")
        with open(untracked_file, "w") as f:
            f.write("\n".join(f"line {i}" for i in range(7)) + "\n")

        # Binary untracked file
        bin_file = os.path.join(self.test_dir, "model.bin")
        with open(bin_file, "wb") as f:
            f.write(b"header\0binary\ndata\nmore\n")

        # Non-ASCII untracked file with 3 lines
        unicode_file = os.path.join(self.test_dir, "café.py")
        with open(unicode_file, "w", encoding="utf-8") as f:
            f.write("a = 1\nb = 2\nc = 3\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        self.assertIn("Changed lines: 10", diff)  # 7 + 3
        self.assertIn("Untracked files:", diff)
        self.assertIn("--- untracked.py (untracked preview) ---", diff)

    def test_get_git_diff_with_string_workspace_path(self):
        # Passing string path instead of list must not crash or iterate characters
        diff = get_git_diff(self.test_dir, turn_tool_names={"write_to_file"})
        self.assertIsInstance(diff, str)

    def test_get_git_diff_with_none_or_empty_workspace_path(self):
        self.assertEqual(get_git_diff(None, turn_tool_names={"write_to_file"}), "")
        self.assertEqual(get_git_diff([], turn_tool_names={"write_to_file"}), "")
        self.assertEqual(get_git_diff("", turn_tool_names={"write_to_file"}), "")


if __name__ == "__main__":
    unittest.main()
