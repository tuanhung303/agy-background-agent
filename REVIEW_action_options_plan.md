# Adversarial Review — Tail Action Options Menu, Context Management Safety, Final Stop Quality Gate

Reviewer: Claude Opus 5. Plan under review: `docs_plan_action_options.md`.
Every claim below was verified against the tree at `4d5fbe9`.

**Verdict: Approve with required changes — do not implement as written.**
Component 2 items 1–2 are already shipped (no-ops). Component 2 item 3 is the only genuinely
new surface and carries the highest risk. Components 1 and 3 cannot land as specified: one
targets a code path that does not run when it matters, the other inverts an existing field's
meaning and doubles injected volume against Invariant 1.

---

## Verified baseline facts

| Fact | Value | Source |
|---|---|---|
| `sage.py` line headroom | **2** (197/199) | `wc -l` |
| `policies.py` headroom | 8 (191/199) | `wc -l` |
| `triage.py` headroom | 53 (146/199) | `wc -l` |
| `events.py` headroom | 16 (183/199) | `wc -l` |
| sage/ module count asserted | **exactly 20** | `tests/test_static_analysis.py:57` |
| Semicolon scope | 26 files (sage 20 + hooks 4 + statusline 2) | `test_static_analysis.py:103-106` |
| State ledger | `/tmp/agy_sage_<conv_id>.json` | `session_state.py:17` |
| `MAX_MID_TURN_STEERS` default | **0 = uncapped** | `config.py:112`, `policies.py:90` |
| `options` in response schema | **does not exist** | grep over `sage/` |

The plan's stated invariants (20 files, 26 production files, ledger path) are all accurate.

---

## BLOCKERS

### B1. Component 1 targets a code path that is skipped on the first evaluation
`STATUS_LEGEND` is interpolated **only** in the `is_update` branch (`sage.py:88`). The initial
call of every conversation takes `sage.py:92`, which renders `sage_prompt.md` and appends
`sig_txt` — `STATUS_LEGEND` never appears.

Consequence: a Decision Action Menu placed in `STATUS_LEGEND` is absent precisely on the turn
where the prompt declares goal-pinning to be the "MANDATORY FIRST ACTION". The menu would be
missing exactly when it is most load-bearing, and present only on follow-ups.

### B2. On the update path the menu would displace the Final Stop directive from the tail
Update-path assembly order (`sage.py:88`) is:

```
… GIT DIFF → {sig_txt}  ← FINAL_STOP_DIRECTIVE lands here → {STATUS_LEGEND}  ← already last
```

`STATUS_LEGEND` is *already* the final token block on update turns, after the signal. Appending
the menu there puts this bullet in the highest-recency slot at a finishing stop:

> `• [ON_TRACK]: Clean progress, no drift -> status="on_track" (recap if final stop)`

That is a recap-biasing instruction sitting *after* the uncompromising gate directive it is
supposed to serve. This directly violates Invariant 2. Note the paths are asymmetric: on the
initial path `sig_txt` *is* last, so the gate keeps primacy there and loses it on follow-ups —
the harder case to notice in testing.

### B3. Component 1 cannot physically land in `sage/sage.py`
`sage.py` has 2 lines of headroom, and `test_static_analysis.py:57` asserts `len(pkg_files) == 20`
exactly — so you can neither grow `sage.py` by a 6-line menu nor add a new module to hold it.

---

## HIGH

### H1. Menu vocabulary drifts from the enforced contract, and the tail slot will suppress what it omits
The menu introduces 5 verbs (`ON_TRACK`/`STEER`/`BREAK_LOOP`/`DELEGATE`/`GUARD`) against a real
contract of 3 statuses and 11 categories. It omits:

- `task_complexity` — **REQUIRED in every response**; `sage_prompt.md` warns that omitting it
  "silently disables the pin", and `triage.py:73` gates `is_pinned` on it.
- `pinned_goal` — the declared mandatory first action.
- `architectural_trap`, `scope_drift`, `fake_verification`, `algorithmic_bottleneck`.
- the `watchout` status itself.

A block in the tail position has maximum recency. Listing 5 actions and not listing
`task_complexity`/`pinned_goal` will bias the model away from emitting them — silently
disabling goal-pinning, which is the feature the whole Farseer doctrine rests on.

### H2. `[STEER]` mislabels watchout-class findings as off_track
The menu maps "missing test / deliverable / passive stop" to `[STEER]`. `sage_prompt.md`
classifies all three as **`watchout`** — Final Stop Gate rule 5 says verbatim "Emit `watchout`
with `category="missing_deliverable"`". In `triage.py:110` the `STEER` tag *is* `off_track`, and
`off_track` bypasses the soft `count >= 1` dedup branch (`triage.py:104`).

Net effect: more `off_track` verdicts → more emissions past the soft ceiling → more
`force_continue` blocks. A context regression against Invariant 1, introduced by a label.

### H3. Component 3 inverts `escalation` and doubles injected volume
Today `escalation` is **model-supplied** and its only job is to *bypass* the dedup ceiling
(`triage.py:103-105`: `escalating` disables the `count >= 1` branch). Having `triage.py`
self-derive `escalation="ignored_advice"` from the `seen` count means every repeated non-steer
advice goes from 1 emission to 2 — a flat doubling of injected steer volume.

That is the exact "redundant echo across multi-turn sessions" Invariant 1 forbids. The mechanism
is also self-defeating: a counter-derived "you ignored me" signal carries no information the
counter didn't already have.

### H4. `seen_advice` is session-scoped, not turn-scoped
Component 3 says "repeated **in the same turn**". No such counter exists. `seen` is persisted to
`/tmp/agy_sage_<conv_id>.json` by `record_sage_emit` (`session_state.py:37`) and reloaded every
turn, with a 50-key LRU trim at `triage.py:97`. Any logic written against a per-turn assumption
will fire across turns and behave differently from its spec on turn 2 onward.

---

## MEDIUM

### M1. There is no `options` field, and the plan does not say who produces it
Neither `STATUS_LEGEND` nor the `sage_prompt.md` Response Format defines `options`. So `triage.py`
must either:

- **(a)** gain a new model-supplied field — which bloats every response *and* both schema copies
  (the `STATUS_LEGEND` JSON line and `sage_prompt.md`), fighting Invariant 1; or
- **(b)** synthesize options mechanically from data it does not have.

Path (b) produces exactly the vague meta-advice `sage_prompt.md` Actionability Rule 5 forbids.
The plan's own fallback illustrates it: `[2] Run full verification matrix if multi-file touched`
is conditional and not executable — it fails Rule 2 ("MUST be concrete and executable") and
Rule 3 ("one step, the one that must happen now").

### M2. The token budget is self-contradictory
"< 35 tokens total per injected steer" vs the plan's own worked example (~60–70 tokens for
tag + action + `Ev:` + `Why:` + `Options:`) vs the live `[:2000]`-char clamp at `triage.py:132`
(~500 tokens). 35 tokens cannot hold the five-part banner the plan specifies. Pick one number and
make it the clamp.

### M3. Options will be the first thing silently truncated
`text = " | ".join(parts)[:2000]` (`triage.py:132`). Appending options last means a long
`action`/`evidence` pair silently eats them — non-deterministic, and invisible in tests that use
short fixtures.

### M4. Answer to Q2: yes, the menu induces passive behavior — and the loop is weakly bounded
Steers are delivered as `injectSteps:[{"userMessage": …}]` (`guards.py:57`), i.e. they arrive
**in the user's voice**. A user-voiced `Options: [1] … [2] …` is a strong invitation to answer
"which would you prefer?" — a passive stop. Final Stop Gate rule 5 then blocks that stop and
steers again.

The bound on that loop is thin: `MAX_MID_TURN_STEERS` defaults to **0 = uncapped**
(`config.py:112`, `policies.py:90`), so the only ceiling is 2 emissions per `advice_key` — and
`compute_advice_key` is derived from `action`/`guidance` (`triage.py:44-56`), so a reworded
`action` mints a fresh key and buys 2 more emissions. The comment at `triage.py:38-42` records
that this exact ceiling-escape already happened once via category relabeling.

### M5. Pre-existing gap worth fixing while you are here (Invariant 2)
`format_summon_message` early-returns `FINAL_STOP_DIRECTIVE` (`events.py:157-158`) *before* the
fact-rendering block. So at a finishing stop the sage receives a static directive with **zero
facts** — no tool count, no error signature, no diff size — and `policies.py:120` calls it with no
kwargs at all. The `SEVERITY[EVENT_FINAL_STOP] = 3` and `ASK[EVENT_FINAL_STOP]` entries are
unreachable dead code. The one event the plan calls "comprehensive and uncompromising" is the
only event that gets no evidence block.

---

## Recommendations

**R1 — Relocate the evaluator menu; fix the tail asymmetry (fixes B1, B2, B3).**
Put the menu in `sage_prompt.md` (not line-limited, not in the `== 20` assert). Move
`STATUS_LEGEND` out of `sage.py` into `sage/events.py` (16 lines headroom), and re-order **both**
branches so the signal is emitted last: `… {STATUS_LEGEND}{sig_txt}`. One change makes the menu
present on turn 1 and guarantees `FINAL_STOP_DIRECTIVE` keeps the recency slot unconditionally.

**R2 — Suppress the tail options block when `mode == "final"` (fixes M4).**
Terminal gates get one directive, never a choice. `classify_advice` already receives `mode`
(`triage.py:58`); gate on it. This is the single highest-value change in the review.

**R3 — Make the tail a deterministic fallback, not a numbered menu (fixes M4, M1).**
Drop the numbering and "(Recommended)". Numbered choices read as a question; a labelled
contingency reads as a directive. One clause, imperative, unconditional:
`Else: <one exact command>`. Never two peers.

**R4 — Align the menu to the real contract (fixes H1, H2).**
Keep the 3 statuses as the top-level axis with categories underneath; relabel `[STEER]` to
`watchout` for missing-deliverable/passive-stop; and since the block owns the recency slot,
explicitly name `task_complexity` and `pinned_goal` in it.

**R5 — Keep `options` out of `compute_advice_key` (fixes M4).**
Key stays on `action`+`guidance` so option churn cannot mint new keys and escape the ceiling.
Worth an explicit regression test.

**R6 — Set `MAX_MID_TURN_STEERS` to a real cap (e.g. 3) before shipping anything that raises
emission volume.** It is uncapped today; H3 and M4 both push volume up.

**R7 — Drop Component 3's auto-escalation (fixes H3, H4).**
Leave `escalation` model-owned. If you want escalation pressure, make the *second* emission
strictly stronger in wording at the same count — do not buy an extra emission.

**R8 — Trim Component 2 to its one new item.**
Items 1 and 2 are already implemented verbatim at `triage.py:117-127` (tag+head, `Ev:`, `Why:`).
Also remove `policies.py` from Component 2's file list — it does not assemble message text;
`triage.py` does. That preserves `policies.py`'s 8-line headroom.

**R9 — (Optional, M5) Render facts for `EVENT_FINAL_STOP`** and delete the now-dead
`SEVERITY`/`ASK` entries for it.

---

## Answers to the plan's four questions

1. **Context regression risks?** Yes, three concrete ones: H3 (auto-escalation doubles emissions),
   H2 (mislabelled `[STEER]` raises `off_track` rate), M1(a) (a new schema field is paid on every
   response and in two schema copies). H4 means the dedup logic will not behave as specified.
2. **Does the menu risk confusing the agent / inducing passive behavior?** Yes — M4. Steers arrive
   in the user's voice, so a numbered menu reads as a question, and a passive answer is exactly
   what the Final Stop Gate blocks, creating a steer loop bounded only by a 2-per-key ceiling that
   rewording escapes. R2 + R3 remove the risk while keeping the fallback value.
3. **Ultra-compact without breaking line limits?** Do not touch `sage.py` (2 lines). Menu text
   → `sage_prompt.md`; `STATUS_LEGEND` → `events.py`. Options rendering belongs in `triage.py`
   (53 lines headroom) as one dict lookup plus one `parts.append` (~6 lines). Keep `policies.py`
   out of it entirely.
4. **Verdict.** Approve with required changes. Ship R1, R2, R3, R8 as the minimum viable version;
   R4–R6 before any volume increase; reject Component 3 as written (R7).
