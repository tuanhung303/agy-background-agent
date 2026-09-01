"""
sage.receipts - Interpretation receipt enforcement for NEW goal pins.

Fail-closed: a fresh pin (category=pinned_goal) must carry an interpretation
receipt declaring the chosen reading and the cheaper-looking rejected proxy.
Silent cheap interpretations are how scope laundering happens; the receipt
makes the choice visible and auditable without banning confident reads.
"""


def enforce_interpretation_receipt(res, d, goal_already_pinned=False):
    """Mutate res in place: demote receipt-less NEW pins to confused_goal."""
    p_goal = res.get("pinned_goal") or res.get("anchor_goal")
    interp = d.get("interpretation")
    ok = isinstance(interp, str) and len(interp.strip()) >= 20
    is_new_pin = (res.get("category") or "").strip().lower() == "pinned_goal"
    if is_new_pin and not ok and not goal_already_pinned:
        res.update(category="confused_goal", status="watchout",
                   action=res.get("action") or "Re-pin with an interpretation receipt.",
                   guidance=("Goal pin missing interpretation receipt: declare {chosen_reading, proxy_rejected} "
                             "(or n/a if fully unambiguous) alongside pinned_goal."))
        for k in ("pinned_goal", "anchor_goal"):
            res.pop(k, None)
        return res
    res["pinned_goal"] = res["anchor_goal"] = p_goal
    if ok:
        res["interpretation"] = interp
    return res
