"""
scripts.eval.eval_validator - Systematic scenario validation & mutation testing harness.

Validates scenario structural invariants, expectation rigor (anti-facade / anti-mock-replay),
and performs fault injection to guarantee all eval scenarios actively kill mutations.
"""
import copy
import re
import unittest.mock as mock

EXPECTED_KEYS = {
    "first_fire_cp", "decision_category", "fire_before_tool_index",
    "max_parallel_emissions", "midturn_interrupts", "final_pending_clarify_ok",
    "prompt_byte_ratio_lt", "final_category", "text_contains", "decision_type",
}


def validate_scenario_schema(sc):
    """Validate structural schema and invariant properties of a scenario dict."""
    errors = []
    if not isinstance(sc, dict):
        return ["scenario must be a JSON dictionary"]
    sid = sc.get("id")
    if not sid or not isinstance(sid, str) or not re.match(r"^[a-z0-9_]+$", sid):
        errors.append(f"invalid scenario id: {sid!r}")
    if not sc.get("desc") or not isinstance(sc.get("desc"), str):
        errors.append(f"scenario {sid} missing non-empty desc")
    if not sc.get("user_prompt") or not isinstance(sc.get("user_prompt"), str):
        errors.append(f"scenario {sid} missing non-empty user_prompt")
    tools = sc.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append(f"scenario {sid} must have non-empty tools list")
    else:
        for idx, t in enumerate(tools):
            if not isinstance(t, list) or len(t) != 2 or not all(isinstance(x, str) for x in t):
                errors.append(f"scenario {sid} tool[{idx}] must be [name, desc] pair")
    expect = sc.get("expect")
    if not isinstance(expect, dict) or not expect:
        errors.append(f"scenario {sid} missing non-empty expect block")
    else:
        active_keys = set(expect.keys()) & EXPECTED_KEYS
        if not active_keys:
            errors.append(f"scenario {sid} expect block contains no recognized assertions")
    return errors


def generate_scenario_mutations(sc):
    """Generate targeted fault-injection mutations to verify expectation sensitivity."""
    mutations = []
    sid = sc.get("id", "")
    expect = sc.get("expect", {})
    has_midturn_expect = any(k in expect for k in ("decision_category", "first_fire_cp", "fire_before_tool_index", "final_pending_clarify_ok"))
    if has_midturn_expect and sc.get("script"):
        m1 = copy.deepcopy(sc)
        for x in m1["script"]:
            if isinstance(x, dict):
                x["category"] = "unrelated_bogus_category"
        if expect.get("final_pending_clarify_ok") and isinstance(m1.get("final_script"), dict):
            m1["final_script"]["category"] = "unrelated_bogus_category"
        mutations.append((f"{sid}:wrong_midturn_category", m1))

        m2 = copy.deepcopy(sc)
        for x in m2["script"]:
            if isinstance(x, dict):
                x["status"] = "on_track"
        if expect.get("final_pending_clarify_ok") and isinstance(m2.get("final_script"), dict):
            m2["final_script"]["status"] = "on_track"
            m2["final_script"]["category"] = "general"
        mutations.append((f"{sid}:invert_midturn_status", m2))

    if "first_fire_cp" in expect or "fire_before_tool_index" in expect:
        m3 = copy.deepcopy(sc)
        m3["script"] = [None, None, None, None] + list(m3.get("script") or [])
        mutations.append((f"{sid}:delayed_execution", m3))
    if sc.get("final_script") and isinstance(sc["final_script"], dict):
        fs = sc["final_script"]
        if fs.get("category") and "final_category" in expect:
            m4 = copy.deepcopy(sc)
            m4["final_script"]["category"] = "unrelated_final_category"
            m4["tools"] = [[t[0], "python -c 'import test'"] for t in m4.get("tools", [])]
            mutations.append((f"{sid}:wrong_final_category", m4))
            m5 = copy.deepcopy(sc)
            m5["final_script"]["status"] = "on_track"
            m5["tools"] = [[t[0], "python -c 'import test'"] for t in m5.get("tools", [])]
            mutations.append((f"{sid}:invert_final_status", m5))
    if sc.get("dedup_probe"):
        m6 = copy.deepcopy(sc)
        m6["dedup_probe"]["pad"] = ["", "", "x", 1, 1]
        mutations.append((f"{sid}:corrupt_dedup_probe", m6))
    if sid == "hammer_same_cat" and len(sc.get("script", [])) >= 2:
        m7 = copy.deepcopy(sc)
        m7["script"][1]["category"] = "loop_detection"
        mutations.append((f"{sid}:unsuppressed_hammer_category", m7))
    if "text_contains" in expect:
        m8 = copy.deepcopy(sc)
        for x in m8.get("script", []):
            if isinstance(x, dict):
                x["action"] = "safe non-matching action"
                x["guidance"] = "safe non-matching guidance"
        if m8.get("final_script") and isinstance(m8["final_script"], dict):
            m8["final_script"]["action"] = "safe non-matching action"
            m8["final_script"]["guidance"] = "safe non-matching guidance"
        m8["tools"] = [[t[0], "python -c 'import test'"] for t in m8.get("tools", [])]
        mutations.append((f"{sid}:corrupt_text_payload", m8))
    if "decision_type" in expect:
        m9 = copy.deepcopy(sc)
        if expect["decision_type"] == "watchout":
            for x in m9.get("script", []):
                if isinstance(x, dict):
                    x["confidence"] = 0.95
        elif expect["decision_type"] == "steer":
            for x in m9.get("script", []):
                if isinstance(x, dict):
                    x["confidence"] = 0.3
        mutations.append((f"{sid}:invert_decision_type", m9))
    return mutations


def run_scenario_mutation_suite(sc, drive_turn_fn, grade_fn, dedup_probe_fn=None):
    """Run scenario through mutations; return mutation test report."""
    base_res = drive_turn_fn(sc)
    if dedup_probe_fn and sc.get("dedup_probe"):
        probe = dedup_probe_fn(sc)
        if probe:
            base_res["dedup_probe"] = probe
    base_probs = grade_fn(base_res, sc.get("expect") or {})
    if base_probs:
        return {"id": sc["id"], "base_pass": False, "total_mutations": 0, "killed": 0, "survived": []}
    mutations = generate_scenario_mutations(sc)
    killed = 0
    survived = []
    for mut_name, mut_sc in mutations:
        m_res = drive_turn_fn(mut_sc)
        if dedup_probe_fn and mut_sc.get("dedup_probe"):
            probe = dedup_probe_fn(mut_sc)
            if probe:
                m_res["dedup_probe"] = probe
        probs = grade_fn(m_res, mut_sc.get("expect") or {})
        if probs:
            killed += 1
        else:
            survived.append(mut_name)
    return {
        "id": sc["id"],
        "base_pass": True,
        "total_mutations": len(mutations),
        "killed": killed,
        "survived": survived,
    }


def run_policy_mutations(scenarios_by_id, drive_turn_fn, grade_fn):
    """Test policy-level fault injections to prove scenario suite sensitivity."""
    results = []
    with mock.patch("sage.policies.has_repeated_tool_calls", return_value=False):
        for sid in ("loop_early", "loop_tsc_compile"):
            if sid in scenarios_by_id:
                res = drive_turn_fn(scenarios_by_id[sid])
                probs = grade_fn(res, scenarios_by_id[sid].get("expect", {}))
                results.append((f"policy:disable_loop_override:{sid}", len(probs) > 0))
    if "hammer_same_cat" in scenarios_by_id:
        with mock.patch("sage.triage.compute_advice_key", side_effect=lambda c, a, g=None: f"uniq_{a}"):
            with mock.patch("sage.policies._hammer_suppressed", return_value=False):
                res = drive_turn_fn(scenarios_by_id["hammer_same_cat"])
                probs = grade_fn(res, scenarios_by_id["hammer_same_cat"].get("expect", {}))
                results.append(("policy:disable_hammer_guard:hammer_same_cat", len(probs) > 0))
    if "irreversible_destructive_guard" in scenarios_by_id:
        with mock.patch("sage.triage.is_destructive_action", return_value=False):
            res = drive_turn_fn(scenarios_by_id["irreversible_destructive_guard"])
            probs = grade_fn(res, scenarios_by_id["irreversible_destructive_guard"].get("expect", {}))
            results.append(("policy:disable_destructive_action_guard:irreversible_destructive_guard", len(probs) > 0))
    if "low_confidence_steer_demote" in scenarios_by_id:
        with mock.patch("sage.policies.SAGE_STEER_MIN_CONFIDENCE", 0.0):
            res = drive_turn_fn(scenarios_by_id["low_confidence_steer_demote"])
            probs = grade_fn(res, scenarios_by_id["low_confidence_steer_demote"].get("expect", {}))
            results.append(("policy:disable_confidence_demotion:low_confidence_steer_demote", len(probs) > 0))
    if "defer_delegated_exec" in scenarios_by_id:
        with mock.patch("sage.policies.detect_transcript_deferral", return_value={"matched": False}):
            res = drive_turn_fn(scenarios_by_id["defer_delegated_exec"])
            probs = grade_fn(res, scenarios_by_id["defer_delegated_exec"].get("expect", {}))
            results.append(("policy:disable_transcript_deferral:defer_delegated_exec", len(probs) > 0))
    if "post_approval_defer" in scenarios_by_id:
        with mock.patch("sage.policies.detect_transcript_deferral", return_value={"matched": False}):
            res = drive_turn_fn(scenarios_by_id["post_approval_defer"])
            probs = grade_fn(res, scenarios_by_id["post_approval_defer"].get("expect", {}))
            results.append(("policy:disable_transcript_deferral:post_approval_defer", len(probs) > 0))
    return results
