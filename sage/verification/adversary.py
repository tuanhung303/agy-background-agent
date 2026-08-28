"""
sage.verification.adversary - Generates adversarial tests via LLM.
"""

from sage.executor import run_model_cascade

def generate_adversarial_tests(parent_conv_id, source_code, max_tests=3):
    prompt = (
        f"Generate {max_tests} adversarial Python edge-case tests for the following code.\n"
        "Return ONLY a JSON object with a key 'tests' containing a list of objects, "
        "where each object has a 'name' (string) and 'code' (string) key.\n\n"
        f"Code:\n```python\n{source_code}\n```"
    )

    def normalize_adversarial(raw_json):
        if isinstance(raw_json, dict):
            return raw_json.get("tests", [])
        return []

    result = run_model_cascade(
        parent_conv_id=parent_conv_id,
        prompt=prompt,
        prefixes=["adv_test_"],
        normalize_func=normalize_adversarial,
        default_on_failure=[],
        label="Adversary",
        schema_keys=("tests",)
    )
    return result if result else []
