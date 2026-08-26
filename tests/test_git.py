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
        self.assertIn("sample.txt", diff)
        self.assertIn("Changed lines: 2", diff)  # 1 added + 1 removed, from numstat
        self.assertNotIn("Modified content", diff)  # no patch body is ever inlined

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
        self.assertIn("staged.txt", diff)
        self.assertIn("untracked.txt", diff)
        self.assertIn("Changed lines: 1", diff)  # staged.txt: 1 added line
        self.assertIn("Untracked files: 1", diff)
        self.assertNotIn("Staged content", diff)

    def test_get_git_diff_untracked_multiline_and_binary(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        # 7-line untracked file
        untracked_file = os.path.join(self.test_dir, "untracked.py")
        with open(untracked_file, "w") as f:
            f.write("\n".join(f"line {i}" for i in range(7)) + "\n")

        # Binary untracked file (must be skipped and noted)
        bin_file = os.path.join(self.test_dir, "model.bin")
        with open(bin_file, "wb") as f:
            f.write(b"header\0binary\ndata\nmore\n")

        # Non-ASCII untracked file with 3 lines
        unicode_file = os.path.join(self.test_dir, "café.py")
        with open(unicode_file, "w", encoding="utf-8") as f:
            f.write("a = 1\nb = 2\nc = 3\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        # No untracked file is opened, so binary/unicode content never reaches the summary.
        self.assertIn("Untracked files: 3", diff)
        self.assertIn("Changed lines: 0", diff)
        self.assertNotIn("line 3", diff)
        self.assertNotIn("binary", diff)

    def test_get_git_diff_untracked_trailing_newline_accounting(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        # 1 line without trailing newline
        with open(os.path.join(self.test_dir, "f1.txt"), "w") as f:
            f.write("hello")
        # 2 lines without trailing newline
        with open(os.path.join(self.test_dir, "f2.txt"), "w") as f:
            f.write("line1\nline2")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        self.assertIn("Untracked files: 2", diff)
        self.assertIn("Changed lines: 0", diff)
        self.assertNotIn("line1", diff)

    def test_get_git_diff_untracked_oversized_and_symlink_not_read(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        # >1 MiB file (1.1 MiB)
        big_file = os.path.join(self.test_dir, "big.txt")
        with open(big_file, "w") as f:
            f.write("x" * (1100 * 1024))

        # Untracked symlink
        target_file = os.path.join(self.test_dir, "target.txt")
        with open(target_file, "w") as f:
            f.write("line 1\nline 2\n")
        subprocess.run(["git", "add", "target.txt"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add target"], cwd=self.test_dir, capture_output=True)

        link_file = os.path.join(self.test_dir, "link.txt")
        os.symlink(target_file, link_file)

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        # Oversized file and symlink need no special-casing: nothing is opened.
        self.assertIn("Untracked files: 2", diff)
        self.assertIn("Changed lines: 0", diff)
        self.assertNotIn("xxxxxxxxxx", diff)

    def test_get_git_diff_untracked_status_entry_cap(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        for i in range(55):
            with open(os.path.join(self.test_dir, f"file_{i:02d}.txt"), "w") as f:
                f.write(f"content {i}\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        # All 55 are counted; only the first 12 status entries are listed verbatim.
        self.assertIn("Untracked files: 55", diff)
        self.assertIn("+43 more entries", diff)

    def test_get_git_diff_with_string_workspace_path(self):
        # Passing string path instead of list must not crash or iterate characters
        diff = get_git_diff(self.test_dir, turn_tool_names={"write_to_file"})
        self.assertIsInstance(diff, str)

    def test_get_git_diff_with_none_or_empty_workspace_path(self):
        self.assertEqual(get_git_diff(None, turn_tool_names={"write_to_file"}), "")
        self.assertEqual(get_git_diff([], turn_tool_names={"write_to_file"}), "")
        self.assertEqual(get_git_diff("", turn_tool_names={"write_to_file"}), "")

    def test_get_git_diff_never_exposes_tracked_secrets(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        tracked_file = os.path.join(self.test_dir, "config.py")
        with open(tracked_file, "w") as f:
            f.write("API_KEY = 'initial'\n")
        subprocess.run(["git", "add", "config.py"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.test_dir, capture_output=True)

        with open(tracked_file, "w") as f:
            f.write("API_KEY = 'SECRET_TOKEN_9999'\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        # Stronger than redaction: no patch body means the secret is never read at all.
        self.assertNotIn("SECRET_TOKEN_9999", diff)
        self.assertIn("config.py", diff)

    def test_get_git_diff_with_shell_aliases(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        with open(os.path.join(self.test_dir, "f.txt"), "w") as f:
            f.write("hello\n")

        for alias in ("bash", "exec", "terminal"):
            diff = get_git_diff([self.test_dir], turn_tool_names={alias})
            self.assertNotEqual(diff, "None (no file-editing tools invoked in turn)")
            self.assertIn("f.txt", diff)

    def test_get_git_diff_untracked_content_not_leaked(self):
        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.test_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True)

        untracked_file = os.path.join(self.test_dir, "secrets.env")
        with open(untracked_file, "w") as f:
            f.write("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")

        diff = get_git_diff([self.test_dir], turn_tool_names={"write_to_file"})
        self.assertNotIn("wJalrXUtnFEMI", diff)
        self.assertIn("secrets.env", diff)

    def test_redact_secrets_corpus_and_no_diff_fabrication(self):
        from sage.sanitizer import redact_secrets, clamp_diff
        secrets_corpus = [
            ("AWS_SECRET_ACCESS_KEY = 'wJalrXUtnFEMI/K7MDENG'", "wJalrXUtnFEMI"),
            ("GITHUB_TOKEN = 'ghp_RealLookingTokenValue12345'", "ghp_RealLookingTokenValue"),
            ("DB_PASSWORD = 'hunter2'", "hunter2"),
            ('DB_PASSWORD = "correct horse battery staple"', "horse battery staple"),
            ("OPENAI_API_KEY = 'sk-proj-xyz12345'", "sk-proj-xyz12345"),
            ("STRIPE_SECRET_KEY = 'sk_live_12345'", "sk_live_12345"),
            ("MY_AUTH_TOKEN = 'abc_token_123'", "abc_token_123"),
            ("JWT_SECRET = 'supersecretjwt'", "supersecretjwt"),
            ("SLACK_BEARER_TOKEN = 'xoxb-12345'", "xoxb-12345"),
            ("access_token = 'token_abc_xyz'", "token_abc_xyz"),
            ("Authorization: Basic dXNlcjpwYXNzMTIz", "dXNlcjpwYXNzMTIz"),
            ("Authorization: Bearer my_super_secret_bearer_token", "my_super_secret_bearer_token"),
            ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890abcdef\nQUJDREVGRw==\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA1234567890abcdef"),
            ("-----BEGIN PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890abcdef", "MIIEowIBAAKCAQEA1234567890abcdef"),
        ]
        for payload, marker in secrets_corpus:
            redacted = redact_secrets(payload)
            self.assertNotIn(marker, redacted, f"Failed to redact marker {marker} from payload {payload}")
            clamped = clamp_diff(payload)
            self.assertNotIn(marker, clamped, f"Failed in clamp_diff for {marker}")

        # Ensure no line-spanning diff fabrication on normal code or empty values
        normal_diff = "+import auth\n+CRITICAL_LINE = 1\n+from auth import login\n+DB_PASSWORD =\n+CRITICAL_LINE_2 = 2\n+def compute():\n+    return 42\n"
        redacted_diff = redact_secrets(normal_diff)
        self.assertIn("CRITICAL_LINE = 1", redacted_diff)
        self.assertIn("CRITICAL_LINE_2 = 2", redacted_diff)
        self.assertIn("def compute():", redacted_diff)
        self.assertEqual(len(normal_diff.splitlines()), len(redacted_diff.splitlines()))

        # Multi-file diff with unterminated PEM redacts key body without destroying subsequent files
        multi_diff = "--- a/key.pem\n+++ b/key.pem\n+-----BEGIN PRIVATE KEY-----\n+MIIEowIBAAKCAQEA1234567890abcdef\n--- a/main.py\n+++ b/main.py\n+def compute():\n+    return 42\n"
        redacted_multi = redact_secrets(multi_diff)
        self.assertNotIn("MIIEowIBAAKCAQEA1234567890abcdef", redacted_multi)
        self.assertIn("--- a/main.py", redacted_multi)
        self.assertIn("def compute():", redacted_multi)


if __name__ == "__main__":
    unittest.main()


class TestWorkspaceRoot(unittest.TestCase):
    def test_resolve_workspace_root_picks_first_real_dir_absolute(self):
        from sage.git import resolve_workspace_root
        real = tempfile.mkdtemp()
        try:
            self.assertEqual(resolve_workspace_root(["/no/such/dir", real]), os.path.abspath(real))
            self.assertEqual(resolve_workspace_root(real), os.path.abspath(real))
            self.assertEqual(resolve_workspace_root(["/no/such/dir"]), "")
            self.assertEqual(resolve_workspace_root([]), "")
            self.assertEqual(resolve_workspace_root(None), "")
        finally:
            shutil.rmtree(real, ignore_errors=True)

    def test_summary_reports_absolute_workspace_path(self):
        """Sage runs with HOME rebound; a relative label would be unusable to it."""
        from sage.git import get_git_diff
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "init"], cwd=d, capture_output=True)
            with open(os.path.join(d, "a.txt"), "w") as f:
                f.write("x\n")
            cwd = os.getcwd()
            try:
                os.chdir(d)
                diff = get_git_diff(["."], turn_tool_names={"write_to_file"})
            finally:
                os.chdir(cwd)
            self.assertNotIn("Workspace (.)", diff)
            self.assertIn(f"Workspace ({os.sep}", diff)
            self.assertIn(os.path.basename(d), diff)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_summary_reads_no_file_contents(self):
        """The untracked line-count loop is gone: no untracked file is ever opened."""
        from sage.git import get_git_diff
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "init"], cwd=d, capture_output=True)
            with open(os.path.join(d, "big.txt"), "w") as f:
                f.write("SENTINEL_CONTENT\n" * 1000)
            opened = []
            real_open = open

            def tracking_open(path, *a, **k):
                opened.append(str(path))
                return real_open(path, *a, **k)

            import builtins
            builtins.open = tracking_open
            try:
                diff = get_git_diff([d], turn_tool_names={"write_to_file"})
            finally:
                builtins.open = real_open
            self.assertNotIn("SENTINEL_CONTENT", diff)
            self.assertEqual([p for p in opened if "big.txt" in p], [])
            self.assertIn("Untracked files: 1", diff)
        finally:
            shutil.rmtree(d, ignore_errors=True)
