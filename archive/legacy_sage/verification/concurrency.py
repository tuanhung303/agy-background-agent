"""
sage.verification.concurrency - Manages parallel execution of isolated tests.
"""

import concurrent.futures
from sage.executor import run_model_cascade

def execute_test_isolated(parent_conv_id, source_code, test_name, test_code):
    prompt = (
        f"We are running a test named '{test_name}'.\n"
        "You have the ability to write this source code and test code to disk and run it using pytest or python, then report the outcome.\n"
        "Return ONLY a JSON object with 'passed' (boolean) and 'output' (string containing error or output). Do NOT include any other text.\n\n"
        f"Source Code:\n```python\n{source_code}\n```\n\n"
        f"Test Code:\n```python\n{test_code}\n```"
    )

    def normalize_exec(raw_json):
        if isinstance(raw_json, dict):
            return raw_json
        return {"passed": False, "output": "Invalid JSON format returned"}

    result = run_model_cascade(
        parent_conv_id=parent_conv_id,
        prompt=prompt,
        prefixes=["test_exec_"],
        normalize_func=normalize_exec,
        default_on_failure={"passed": False, "output": "Agent cascade execution failed"},
        label=f"Executor-{test_name}",
        schema_keys=("passed", "output")
    )
    return {
        "name": test_name,
        "passed": result.get("passed", False),
        "output": result.get("output", "")
    }

def run_tests_in_parallel(parent_conv_id, source_code, tests, max_workers=3):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(execute_test_isolated, parent_conv_id, source_code, t.get("name", "Unknown"), t.get("code", "")): t
            for t in tests
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                t = futures[future]
                results.append({
                    "name": t.get("name", "Unknown"),
                    "passed": False,
                    "output": f"Exception during execution: {e}"
                })
    return results
