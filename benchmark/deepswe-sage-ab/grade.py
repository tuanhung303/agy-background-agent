#!/usr/bin/env python3
"""grade.py — grade a DeepSWE run locally from junit XMLs / CTRFs + config.json whitelists.

Mirror of tests/grader.py logic:
  - node-id = "<classname>: <name>" from junit (same as --use-suite-name) or CTRF test name
  - reward = f2p_all_pass AND p2p_keep_fraction, binary
Usage: grade.py <workdir> <task-id> <evidence-dir>
"""
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

TASKS = "/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks"


def node_ids_from_junit(paths):
    ids = set()
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            root = ET.parse(p).getroot()
        except Exception:
            continue
        for tc in root.iter("testcase"):
            cls = tc.get("classname") or ""
            name = tc.get("name") or ""
            if not name:
                continue
            fid = f"{cls}: {name}" if cls else name
            failed = tc.find("failure") is not None or tc.find("error") is not None
            if not failed:
                ids.add(fid)
    return ids


def node_ids_from_ctrf(paths):
    ids = set()
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p))
            for t in data.get("results", {}).get("tests", []):
                if t.get("status") in ("passed", "pass"):
                    ids.add(t.get("name"))
        except Exception:
            continue
    return ids


def main():
    if len(sys.argv) < 4:
        print("Usage: grade.py <workdir> <task-id> <evidence-dir>")
        sys.exit(1)

    workdir, task_id, evid = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg_file = os.path.join(TASKS, task_id, "tests", "config.json")
    if not os.path.exists(cfg_file):
        raise SystemExit(f"Missing config: {cfg_file}")

    cfg = json.load(open(cfg_file))
    f2p = cfg["f2p_node_ids"]
    p2p = cfg["p2p_node_ids"]

    # Apply the held-out test patch into the workspace
    test_patch = os.path.join(TASKS, task_id, "tests", "test.patch")
    r = subprocess_apply(test_patch, workdir)

    # Find and run the suites exactly like test.sh for this task
    base_xml = os.path.join(evid, "base.xml")
    new_xml = os.path.join(evid, "new.xml")
    base_ctrf = os.path.join(evid, "base_ctrf.json")
    new_ctrf = os.path.join(evid, "new_ctrf.json")

    run_cmds(task_id, workdir, base_xml, new_xml, base_ctrf, new_ctrf, evid)

    passed = node_ids_from_junit([base_xml, new_xml])
    passed.update(node_ids_from_ctrf([base_ctrf, new_ctrf]))

    f2p_pass = [i for i in f2p if i in passed]
    p2p_pass = [i for i in p2p if i in passed]
    res = {
        "task": task_id,
        "patch_applied": r,
        "f2p": f"{len(f2p_pass)}/{len(f2p)}",
        "p2p": f"{len(p2p_pass)}/{len(p2p)}",
        "f2p_frac": round(len(f2p_pass) / max(1, len(f2p)), 3),
        "p2p_frac": round(len(p2p_pass) / max(1, len(p2p)), 3),
        "reward": int(len(f2p_pass) == len(f2p) and len(p2p_pass) == len(p2p)),
    }
    # partial credit metric for improvement tracking
    res["partial"] = round((res["f2p_frac"] * 0.5 + res["p2p_frac"] * 0.5), 3)
    print(json.dumps(res, indent=1))
    with open(os.path.join(evid, "reward.json"), "w") as f:
        json.dump(res, f, indent=1)


def subprocess_apply(patch, workdir):
    if not os.path.exists(patch):
        return "missing-patch"
    r = subprocess.run(["git", "-C", workdir, "apply", "--check", patch],
                       capture_output=True, text=True)
    if r.returncode != 0:
        r2 = subprocess.run(["git", "-C", workdir, "apply", "--3way", patch],
                            capture_output=True, text=True)
        return f"3way:{r2.returncode}:{(r2.stdout+r2.stderr)[-200:]}"
    subprocess.run(["git", "-C", workdir, "apply", patch], capture_output=True, text=True)
    return "applied"


def run_cmds(task_id, workdir, base_xml, new_xml, base_ctrf, new_ctrf, evid):
    """Run test commands corresponding to each task's tests/test.sh."""
    env = dict(os.environ, PATH=f"{os.path.expanduser('~/.local/share/pnpm')}:{os.environ.get('PATH','')}")
    if task_id.startswith("koota"):
        cmd_base = ["pnpm", "-F", "core", "test", "run", "--exclude", "**/deferred.test.ts",
                    "--reporter=junit", f"--outputFile={base_xml}"]
        cmd_new = ["pnpm", "-F", "core", "test", "run", "tests/deferred.test.ts",
                   "--reporter=junit", f"--outputFile={new_xml}"]
        for tag, cmd in (("base", cmd_base), ("new", cmd_new)):
            with open(os.path.join(evid, f"{tag}_run.log"), "w") as lf:
                subprocess.run(cmd, cwd=workdir, stdout=lf, stderr=subprocess.STDOUT, timeout=2400, env=env)
    elif task_id.startswith("valibot"):
        lib = os.path.join(workdir, "library")
        cmd_base = ["corepack", "pnpm", "exec", "vitest", "run",
                    "src/methods/parse/parse.test.ts", "src/methods/parse/parseAsync.test.ts",
                    "src/methods/safeParse/safeParse.test.ts", "src/methods/safeParse/safeParseAsync.test.ts",
                    "src/methods/pipe/pipe.test.ts", "src/methods/pipe/pipeAsync.test.ts",
                    "src/schemas/lazy/lazy.test.ts", "src/schemas/lazy/lazyAsync.test.ts",
                    "--reporter=junit", f"--outputFile={base_xml}"]
        cmd_new = ["corepack", "pnpm", "exec", "vitest", "run",
                   "src/methods/recursive/recursive.test.ts",
                   "--reporter=junit", f"--outputFile={new_xml}"]
        for tag, cmd in (("base", cmd_base), ("new", cmd_new)):
            with open(os.path.join(evid, f"{tag}_run.log"), "w") as lf:
                subprocess.run(cmd, cwd=lib, stdout=lf, stderr=subprocess.STDOUT, timeout=2400, env=env)
    elif task_id.startswith("ts-pattern"):
        cmd_base = ["npx", "jest", "tests/helpers.test.ts", "--no-coverage", "--reporters=default",
                    f"--reporters=jest-junit", "--outputFile", base_xml]
        cmd_new = ["npx", "jest", "tests/match-each.test.ts", "--no-coverage", "--reporters=default",
                   f"--reporters=jest-junit", "--outputFile", new_xml]
        for tag, cmd in (("base", cmd_base), ("new", cmd_new)):
            with open(os.path.join(evid, f"{tag}_run.log"), "w") as lf:
                subprocess.run(cmd, cwd=workdir, stdout=lf, stderr=subprocess.STDOUT, timeout=2400, env=env)
    else:
        # Generic runner: execute test.sh in task dir if present
        task_test_sh = os.path.join(TASKS, task_id, "tests", "test.sh")
        if os.path.exists(task_test_sh):
            with open(os.path.join(evid, "test_sh.log"), "w") as lf:
                subprocess.run(["bash", task_test_sh], cwd=workdir, stdout=lf, stderr=subprocess.STDOUT, timeout=2400, env=env)


if __name__ == "__main__":
    main()
