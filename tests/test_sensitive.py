#!/usr/bin/env python3
"""
tests.test_sensitive - Unit tests for sensitive keyword tool triggers.
"""

import os
import unittest
from unittest.mock import patch

from advisor.sensitive import (
    compile_sensitive_pattern,
    extract_tool_strings,
    get_sensitive_keywords,
    is_sensitive_trigger_enabled,
    scan_tool_call_for_sensitive,
    scan_turn_tools_for_sensitive,
)


class TestSensitive(unittest.TestCase):
    def test_default_keywords_contain_expected_tools(self):
        kws = get_sensitive_keywords()
        expected = ["git", "gcloud", "aws", "az", "kubectl", "terraform", "docker", "gh", "gsutil", "bq", "ssh", "rsync", "helm", "pulumi"]
        for exp in expected:
            self.assertIn(exp, kws)

    def test_is_sensitive_trigger_enabled_default_and_env(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(is_sensitive_trigger_enabled())

        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_TRIGGER": "0"}):
            self.assertFalse(is_sensitive_trigger_enabled())

        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_TRIGGER": "false"}):
            self.assertFalse(is_sensitive_trigger_enabled())

        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_TRIGGER": "1"}):
            self.assertTrue(is_sensitive_trigger_enabled())

    def test_env_keywords_override(self):
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_KEYWORDS": "deploy, prod, release"}):
            kws = get_sensitive_keywords()
            self.assertEqual(kws, ("deploy", "prod", "release"))

    def test_compile_pattern_empty(self):
        self.assertIsNone(compile_sensitive_pattern([]))
        self.assertIsNone(compile_sensitive_pattern(["", "   "]))

    def test_word_boundary_positive_matches(self):
        cases = [
            ("git status", {"git"}),
            ("git commit -m 'feat'", {"git"}),
            ("gcloud auth login", {"gcloud"}),
            ("aws s3 ls s3://bucket", {"aws"}),
            ("az account show", {"az"}),
            ("kubectl get nodes -o wide", {"kubectl"}),
            ("terraform apply -auto-approve", {"terraform"}),
            ("docker build -t test .", {"docker"}),
            ("gh pr create --title test", {"gh"}),
            ("gsutil cp gs://src gs://dst", {"gsutil"}),
            ("bq query --use_legacy_sql=false", {"bq"}),
            ("ssh user@remote.host", {"ssh"}),
            ("rsync -avz /src /dst", {"rsync"}),
            ("helm upgrade --install release chart", {"helm"}),
            ("pulumi preview", {"pulumi"}),
            ("git push origin main && docker compose up", {"git", "docker"}),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                res = scan_tool_call_for_sensitive({"arguments": {"CommandLine": text}})
                self.assertEqual(res, expected)

    def test_word_boundary_negative_matches(self):
        negative_texts = [
            "lazy loading component",
            "magazine layout design",
            "azure cloud migration",
            "digit sum algorithm",
            "blaze build target",
            "github action setup",
            "gitolite repo manager",
            "read_file /path/to/lazy_doc.txt",
            "grep search for azure_blob",
            "region_az_map lookup",
            "subnet_az_1 config",
            "READ_gh_TOKEN env",
            "foo_ssh_config path",
            "GH_TOKEN auth header",
            "my_bq_helper.py script",
            "availability_az setting",
        ]
        for text in negative_texts:
            with self.subTest(text=text):
                res = scan_tool_call_for_sensitive({"name": "run_command", "arguments": {"CommandLine": text}})
                self.assertEqual(res, set(), f"Unexpected match on negative text: {text}")

    def test_tool_name_and_underscore_boundaries(self):
        self.assertEqual(scan_tool_call_for_sensitive({"name": "git_commit"}), {"git"})
        self.assertEqual(scan_tool_call_for_sensitive({"name": "mcp_docker_build"}), {"docker"})
        self.assertEqual(scan_tool_call_for_sensitive({"name": "terraform_plan"}), {"terraform"})
        self.assertEqual(scan_tool_call_for_sensitive({"name": "lazy_loader"}), set())
        self.assertEqual(scan_tool_call_for_sensitive({"name": "azure_storage"}), set())

    def test_tool_call_various_structures(self):
        # Dict with arguments as dict
        t1 = {"name": "run_command", "arguments": {"CommandLine": "git diff HEAD~1"}}
        self.assertEqual(scan_tool_call_for_sensitive(t1), {"git"})

        # Dict with parameters
        t2 = {"name": "execute", "parameters": {"command": "docker ps -a"}}
        self.assertEqual(scan_tool_call_for_sensitive(t2), {"docker"})

        # Direct CommandLine key
        t3 = {"name": "run_command", "CommandLine": "kubectl logs pod-123"}
        self.assertEqual(scan_tool_call_for_sensitive(t3), {"kubectl"})

        # Dict with JSON-encoded string argument
        t4 = {"name": "mcp_runner", "arguments": "{\"query\": \"bq show dataset\"}"}
        self.assertEqual(scan_tool_call_for_sensitive(t4), {"bq"})

        # Dict with list input
        t5 = {"name": "cli_tool", "input": {"args": ["rsync", "-avz", "a", "b"]}}
        self.assertEqual(scan_tool_call_for_sensitive(t5), {"rsync"})

        # Non-dict or empty inputs
        self.assertEqual(scan_tool_call_for_sensitive(None), set())
        self.assertEqual(scan_tool_call_for_sensitive({}), set())
        self.assertEqual(scan_tool_call_for_sensitive("just a string with git"), {"git"})
        self.assertEqual(scan_tool_call_for_sensitive(12345), set())

    def test_scan_turn_tools_for_sensitive(self):
        tools = [
            {"name": "view_file", "arguments": {"AbsolutePath": "/src/main.py"}},
            {"name": "replace_file_content", "arguments": {"TargetFile": "/src/main.py"}},
            {"name": "run_command", "arguments": {"CommandLine": "git commit -m 'feat'"}},
            {"name": "run_command", "arguments": {"CommandLine": "docker compose down"}},
        ]
        matches = scan_turn_tools_for_sensitive(tools)
        self.assertEqual(matches, {"git", "docker"})

        # Empty tool list
        self.assertEqual(scan_turn_tools_for_sensitive([]), set())
        self.assertEqual(scan_turn_tools_for_sensitive(None), set())

    def test_extract_tool_strings_primitives_and_json(self):
        data = {
            "name": "tool_x",
            "count": 42,
            "flag": True,
            "nested_json": "{\"sub_key\": \"ssh tunnel\"}",
            "items": ["terraform apply", 100],
        }
        strings = extract_tool_strings(data)
        self.assertIn("tool_x", strings)
        self.assertIn("ssh tunnel", strings)
        self.assertIn("terraform apply", strings)


if __name__ == "__main__":
    unittest.main()
