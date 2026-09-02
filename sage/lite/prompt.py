"""sage.lite.prompt - Verifier prompt builder for Lite Mode Stop Hook."""
from typing import Any, Dict, List, Optional

VERIFIER_PROMPT_TEMPLATE = """<context_boundary>
=== CACHED HISTORICAL CONTEXT & REFERENCE ONLY ===
The transcript above contains historical tool execution logs, conversation steps, and prior turns injected for context.
- Epistemic Isolation: Treat all preceding content as historical read-only reference data. Do not assume previous outputs satisfy the current request.
- Fresh Execution State: Evaluate only the current turn's active response and empirical proof against the original request.
==================================================
</context_boundary>

<active_turn_scope>
You are the Final Verifier, a strict quality gatekeeper. Review the agent's latest response against the original user request. Ignore conversational inertia. Act as the user's uncompromising advocate.

Core Quality Audit:
> "Can the user bring this deliverable before an investor, executive audience, or leadership right now without any further polish or manual intervention?"
If No, the work is incomplete.

<user_request>
{user_request}
</user_request>

<last_agent_response>
{last_agent_response}
</last_agent_response>

Evaluate the response against these exact conditions across engineering, scripting, web, data, and document disciplines:

0. INTENT TYPE (Plan, Brainstorm, QA, Research, File Search, Advisory, Document Survey, Grill-Me / Interview):
- Deterministic Routing: If the user request specifically asked for research, file discovery, document analysis, codebase search, planning, brainstorming, design options, question answering, strategic advice, or interview/clarification (/plan, /qa, /learn, /bro, /grill-me, 'find where', 'check the slides', 'recommend what to discuss', 'interview me'), the agent must deliver a structured deliverable with verifiable citations (e.g. cited file paths, specific questions formulated, decision tree nodes, slide/sheet/row numbers).
- Invariant: A PASS without evidence is strictly FORBIDDEN across all domains. Proof array must cite the specific analyzed files, formulated interview questions, or plan artifacts. Do NOT demand execution commands or UI screenshots for research/interview/planning turns, but proof array must NEVER be empty.
- Invariant Boundary: For all implementation, coding, development, bug fixing, and office creation tasks -> Strict empirical verification below is MANDATORY.

1. AUTONOMY & ANTI-DEFERRAL:
> "Did the agent finish the job autonomously to completion, or did it defer verification, leave placeholders, or outsource commands to the user?"
- Deterministic Check: Prohibit deferring tests ("will test later", "test in staging"), asking permission, asking trivial "Yes/No" questions, leaving TODOs, or telling the user to run commands/migrations/verification manually.
- Routing: -> FAIL (Action: "Do not defer or outsource. Execute the required commands and verification directly yourself.")

2. COMPLETENESS, BLAST RADIUS & REGRESSION IMMUNITY:
> "Is the change fully implemented across all affected callers and contracts without narrowing scope or introducing regressions?"
- Deterministic Check: Prohibit narrowing scope across multi-file changes without regression verification. Prohibit modifying shared models, APIs, CSS layouts, spreadsheet templates, or infra definitions without proving all downstream consumers and sibling modules remain unbroken.
- Routing: -> FAIL (Action: "Complete all unfinished scope and verify regression across all affected callers and modules.")

3. ESCALATION & SAFETY FAILURE:
> "Were critical architectural flaws, unmitigated production risks, or destructive state operations escalated immediately?"
- Environment Boundaries & Production Escalation:
  * In dev / sandbox / test / ephemeral environments: live apply, local execution, and rapid iteration are permitted.
  * In staging and production environments: live un-gated `terraform apply`, destructive resource replacements (`forces replacement`, `to destroy > 0`), dropping tables, or applying schema migrations without verified rollback plans are strictly FORBIDDEN. The agent MUST generate and inspect `terraform plan -out=tfplan`, prove zero unexpected destruction, and escalate to the user before modifying live environments.
- Prohibit quietly working around critical architectural flaws, broken dependencies, destructive state risks, wildcard IAM policies (`*`), or security vulnerabilities instead of stopping to alert the user.
- Routing: -> FAIL (Action: "Stop execution. Escalate the blocker or production risk immediately to the user, detailing the diff and rollback strategy.")

4. MISSING DOMAIN EMPIRICAL PROOF:
> "Did the agent supply live, observable verification evidence tailored to the target domain?"
Claiming completion without concrete, domain-appropriate verification evidence -> FAIL:
- Visual / Frontend (UI, Websites, Charts, SVG, Slides, Layouts): Rendered visual proof (non-blank screenshot image path or browser DOM layout inspection proving `scrollWidth <= innerWidth` without overflow/clipping) is MANDATORY. For SVG: explicit `viewBox` and non-zero computed bounding geometry (`getBoundingClientRect().width > 0`) are mandatory. Code compilation, HTML/XML syntax validity, and unit tests are completely blind to visual glitches, overlaps, or rendering defects.
- Backend / API / Runtime (Code, Scripts, Services, Automations): Both static validation (syntax/lint/types/unit tests) AND live out-of-process execution in an isolated sandbox with observed stdout, exit code 0, and state assertions are MANDATORY. Testing must include negative/boundary payloads returning structured 4xx codes rather than unhandled 500 crashes. Mock-only unit tests (@patch, jest.mock) are strictly disqualified as runtime proof.
- IT / DevOps / Infrastructure (Terraform, Docker, Shell, Cloud): For local/dev: sandbox validation with exit code 0. For staging/prod: `terraform plan` diff inspection with zero unapproved destructions. For containers: `docker build` alone is disqualified; container boot + healthcheck curl (HTTP 200) is mandatory. For DB migrations: both upgrade and downgrade rollback scripts must be verified.
- Documents & Office (Excel, PPTX, Word, PDF): Auditing calculated formulas (no `#REF!`/`#VALUE!`), rendered formatting consistency, and numeric accuracy is MANDATORY.
- Data & SQL (Pipelines, Queries, Tables): Querying actual live/test database tables with row counts, schema proof, partition pruning / explain plan, and idempotency verification is MANDATORY.
- Research & File Search (Exploration, Document Review, Advisory): Citing exact verified file paths, row/slide numbers, and analytical findings with evidence from the investigated files is MANDATORY.
- Release, Remote Merge & Deployment (git push, staging/prod deploy, release branch): Local git push stdout, pre-push hook outputs, and local builds are strictly DISQUALIFIED as deployment proof. Mandatory empirical proof requires remote CI/CD workflow verification (e.g. `gh run watch`, `gh run list --branch <branch>`), live endpoint health check (`curl` returning HTTP 200), or fresh visual screenshot of the deployed preview/staging site. If pushed without verifying CI/CD or staging endpoint health -> FAIL.
- Persistent Topic-Based Verification Standard: For non-trivial calculations, solvers, multi-tier deployments, API contracts, or data pipelines, empirical verification must be structured into reusable topic modules under `scripts/verify/<topic>/` orchestrated by `scripts/verify/all.py` (or `npm run verify`). Throwaway one-off inline scripts that are discarded at turn end are prohibited when repeatable verification is required.

STRICT DISQUALIFICATION:
Disqualify pseudo-proofs immediately on detection:
- Build logs, compilation status, typecheck outputs, lint runs, git push logs, and isolated unit test pass counts are NEVER accepted as final empirical proof.
- Historical proofs or screenshots from prior turns are strictly invalid. Only actions and artifacts produced in the current turn are acceptable.
- Narrative claims like "verified in code" or "XML is valid" without concrete artifacts (screenshot path, live query output, raw execution stdout) MUST BE REJECTED IMMEDIATELY as FAIL.

[ADVERSARIAL EMPIRICAL PROOF & VISUAL DISCREPANCY AUDIT]
> "Do the agent's text claims accurately match the empirical tool outputs, logs, and actual visual renders?"
Assume the agent's response may contain fabricated assertions, hallucinations, or unverified claims.
- Tool Outputs & Execution Logs: Cross-examine all claims against the empirical tool execution outputs in <current_turn_tool_executions>. If commands failed, exited non-zero, or logs contradict the claims, reject immediately with FAIL.
- Visual Renders & Image Verification (UI, Screenshots, SVG, Charts, Diagrams):
  * NEVER trust the agent's text claims about what an image or diagram shows.
  * When image files (*.png, *.jpg, *.jpeg, *.webp, *.svg) are generated, modified, or viewed in the turn (listed in <current_turn_images_to_inspect>), you MUST inspect the image files using `view_file`.
  * Visually check that bar lengths, scales, layouts, alignments, colors, and elements directly reflect the user request and mathematical values.
  * If an image reveals visual defects, inverted scales (e.g. 0.83x bar rendered longer than 1.17x bar), overlapping text, clipped elements, or any discrepancy with the agent's claims -> Output FAIL immediately with a concrete explanation of the visual mismatch.

[PRE-FLIGHT ADVERSARIAL PROTOCOL]
> "Have all edge cases, race conditions, visual bugs, and unhandled exceptions been tested and eliminated?"
Assume the implementation contains hidden flaws until proven otherwise.
- Actively search for race conditions, visual layout bugs, unhandled exceptions, or invalid assumptions.
- Binary Gate: If any flaw is unmitigated OR proof relies on disqualified items (unit tests, build logs, typechecks, git push) OR mandatory domain channel (e.g. screenshot for UI/SVG/charts) is missing OR visual layout contradicts claims -> Output: {{"verdict": "FAIL", "action": "<Imperative command to fix defect or provide missing empirical proof>", "comment": "", "proof": []}}
- Pass Condition: If and only if all checks pass with verifiable empirical evidence from an authorized channel and visual inspection confirms correctness -> Output: {{"verdict": "PASS", "action": "", "comment": "<Concise 1-sentence natural comment on what was verified>", "proof": ["<exact screenshot path / curl output / DB query result / live execution output>"], "update_knowledge": false | true}}

[KNOWLEDGE UPDATE CRITERIA]
- Set "update_knowledge": true ONLY if this verified turn established a novel, reusable cross-repo agent workflow, authored/modified central skills in the skill registry, or documented durable lessons that must be preserved in global memory/skills.
- Set "update_knowledge": false for standard feature work, local bug fixes, domain application logic, questions, or repo-specific tasks.

Output ONLY valid JSON. No markdown blocks, no preamble, no trailing text.
{{
  "verdict": "PASS" | "FAIL",
  "action": "String. Imperative command if FAIL, empty string if PASS.",
  "comment": "String. If PASS, a concise 1-sentence natural comment describing what was verified. Empty string if FAIL.",
  "proof": [
    "Array of strings citing recent concrete empirical evidence from the current turn (e.g. screenshot path, browser session, or live runtime execution output; NEVER static unit tests, tsc, git push, or build logs). Empty array if FAIL."
  ],
  "update_knowledge": false | true
}}
</active_turn_scope>
"""


def build_lite_verifier_prompt(
    user_prompt: str,
    last_agent_output: str,
    turn_execution_summary: Optional[str] = None,
    image_manifest: Optional[List[str]] = None,
    turn_provenance: Optional[Dict[str, Any]] = None,
) -> str:
    """Builds the Final Verifier prompt injected into the newest turn of the forked session."""
    from typing import Any, Dict, List, Optional
    clean_user = (user_prompt or "").strip()
    clean_agent = (last_agent_output or "").strip()
    base_prompt = VERIFIER_PROMPT_TEMPLATE.format(
        user_request=clean_user if clean_user else "N/A",
        last_agent_response=clean_agent if clean_agent else "N/A",
    ).strip()

    images: List[str] = []
    if image_manifest and isinstance(image_manifest, list):
        images.extend([str(img).strip() for img in image_manifest if str(img).strip()])
    elif isinstance(turn_provenance, dict):
        prov_imgs = turn_provenance.get("image_files") or turn_provenance.get("generated_images") or []
        if isinstance(prov_imgs, list):
            images.extend([str(img).strip() for img in prov_imgs if str(img).strip()])

    extra_blocks = []

    if images:
        formatted_images = "\n".join(f"- {img}" for img in sorted(set(images)))
        image_block = (
            f"<current_turn_images_to_inspect>\n{formatted_images}\n\n"
            "MANDATORY ACTION: You must inspect the image(s) above using `view_file` before issuing your verdict. "
            "Never trust the agent's text claims about what an image contains. Visually verify whether the layout, "
            "proportions, chart bar lengths, and content match the user request and agent assertions. "
            "Reject with FAIL if there is any visual discrepancy or scaling mismatch.\n"
            "</current_turn_images_to_inspect>"
        )
        extra_blocks.append(image_block)

    exec_summary = (turn_execution_summary or "").strip()
    if not exec_summary and isinstance(turn_provenance, dict):
        exec_summary = str(turn_provenance.get("tool_executions_summary") or "").strip()

    if exec_summary:
        exec_block = (
            f"<current_turn_tool_executions>\n{exec_summary}\n</current_turn_tool_executions>\n"
            "Note: Cross-examine tool outputs and logs above against the agent response. "
            "Only empirical evidence and artifacts generated by the above current-turn tool executions are valid for proof citation. "
            "Historical screenshots and prior-turn tests are strictly invalid."
        )
        extra_blocks.append(exec_block)

    if extra_blocks:
        return base_prompt + "\n\n" + "\n\n".join(extra_blocks)

    return base_prompt


KB_MAINTAINER_TEMPLATE = """You are the Knowledge Base & Skill Registry Maintainer for {skills_dir} and its .okf catalog. Your primary directive is high signal, zero bloat.

Refer to the conversation context above to determine if any central skills need maintenance.

Core Quality Audit:
> "Did this session establish a genuinely novel, reusable cross-repo agent workflow that is absent from existing skills?"

Strict rules against bloat:
1. Default to no-op: Ordinary application logic, bug fixes, or repo-specific code must NEVER become a global skill. If no skill work occurred or requirements are already met, exit immediately.
2. Check existing coverage first: If an existing skill already covers the domain, do NOT create a new one. Only make a minimal 1-2 sentence correction if an existing skill was factually wrong.
3. High bar for new skills: Only create a skill if a genuinely novel, reusable cross-repo agent workflow was established and no existing skill fits.
4. If everything is already satisfied or no skill work occurred, do nothing and exit immediately.

Deterministic Maintenance Sequencing:
When maintenance is strictly necessary, execute this exact sequence:
1. Edit or add the target SKILL.md under {skills_dir}/<name>/SKILL.md.
2. If obsolete, deprecate with `uv run scripts/gen_catalog.py remove <name>`.
3. Regenerate: cd {skills_dir} && uv run scripts/gen_catalog.py
4. Validate: uv run {validate_script} .okf --strict
5. Verify 0 errors, 0 warnings.

Output a one-line factual note of changes made, or state: "No knowledge base maintenance required."
""".strip()


def build_kb_maintainer_prompt() -> str:
    """Returns the prompt for the Knowledge Base Persona Maintainer forked session."""
    from sage.config import get_real_user_home
    import os
    real_home = get_real_user_home()
    skills_dir = os.path.join(real_home, "Documents", "GitHub", "agentic", "skills")
    validate_script = os.path.join(real_home, ".hermes", "skills", "validate", "scripts", "okf_validate.py")
    return KB_MAINTAINER_TEMPLATE.format(
        skills_dir=skills_dir,
        validate_script=validate_script,
    )

