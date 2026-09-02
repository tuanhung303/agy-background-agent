#!/usr/bin/env python3
"""Master verification orchestrator for agy-background-agent.
Auto-discovers and executes all topic suites in scripts/verify/<topic>/main.py.
"""
import argparse
import glob
import importlib.util
import os
import subprocess
import sys
import time


def discover_topics(verify_dir: str):
    """Discovers all topic directories containing main.py or verify scripts."""
    topics = []
    for entry in sorted(os.listdir(verify_dir)):
        topic_path = os.path.join(verify_dir, entry)
        if os.path.isdir(topic_path) and not entry.startswith((".", "_")):
            main_file = os.path.join(topic_path, "main.py")
            if os.path.exists(main_file):
                topics.append((entry, main_file))
    return topics


def run_topic(topic_name: str, script_path: str, cwd: str) -> tuple:
    """Executes a single topic verification script out-of-process."""
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, script_path],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    dur = round(time.time() - t0, 3)
    return res.returncode == 0, dur, res.stdout, res.stderr


def main():
    parser = argparse.ArgumentParser(description="Master verification runner")
    parser.add_argument("--topic", help="Run a specific topic only", default=None)
    parser.add_argument("--slash-plan", action="store_true", help="Run slash plan gating and steering verification directly")
    args = parser.parse_args()

    if args.slash_plan:
        args.topic = "slash_plan"

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    verify_dir = os.path.abspath(os.path.dirname(__file__))

    topics = discover_topics(verify_dir)
    if args.topic:
        topics = [t for t in topics if t[0] == args.topic]
        if not topics:
            print(f"Error: Topic '{args.topic}' not found under {verify_dir}")
            sys.exit(1)

    print("=" * 60)
    print("AGY MASTER VERIFICATION SUITE (scripts/verify/)")
    print(f"Discovered {len(topics)} topic suites: {[t[0] for t in topics]}")
    print("=" * 60)

    all_passed = True
    total_start = time.time()

    for name, script_path in topics:
        print(f"\n▶ Running topic suite: [{name}] ({script_path}) ...")
        passed, dur, stdout, stderr = run_topic(name, script_path, root_dir)
        if passed:
            print(f"  ✓ [{name}] PASSED ({dur}s)")
            if stdout.strip():
                for line in stdout.strip().splitlines()[-4:]:
                    print(f"    │ {line}")
        else:
            all_passed = False
            print(f"  ✗ [{name}] FAILED ({dur}s)")
            if stdout:
                print(f"  --- STDOUT ---\n{stdout}")
            if stderr:
                print(f"  --- STDERR ---\n{stderr}")

    total_dur = round(time.time() - total_start, 3)
    print("\n" + "=" * 60)
    if all_passed:
        print(f"ALL {len(topics)} VERIFICATION TOPICS PASSED CLEANLY in {total_dur}s")
        print("=" * 60)
        sys.exit(0)
    else:
        print(f"VERIFICATION SUITE FAILED in {total_dur}s")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
