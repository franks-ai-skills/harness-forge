---
name: harness-verify
description: Verifies built Claude Code and Codex skills against the pinned Harness KB rules, the accepted requirements and each part's confirmed goal, and writes a contract-bound report. Use after build-skill, after fixing a part, or when asked whether a built harness setup meets its goals. Not for deciding what to build or for checking platform settings such as branch protection.
---

# Harness verify

Checks what was built against the rules that apply and the goal the user
confirmed, and reports two results per part: the harness result for
what was built, and the goal result.

`scripts/forge.py` is in the Forge plugin root, two directories above
this skill's folder; run it with the Forge environment's Python.

## Rules that are easy to get wrong

- **Confirm the current contract first,** in this session, as
  harness-intake's contract reference describes. Without it, every part
  stays blocked and its goal is reported as not assessed. A run without
  a user still runs the static checks.
- **Scripts decide first and cannot be overruled.** The review worker
  judges only the rules the run requests; it never sees or changes
  script results.
- **Verified has a narrow meaning:** every applicable rule has a result,
  no error rule failed or stayed unresolved, the goal verdict is met,
  executed runtime tests passed and no bound input changed. It never
  means outside controls are configured, and it never certifies worker
  isolation; the report always says `not-certified`.
- **Runtime tests are reported separately.** Without them the report
  says `none run`; an unavailable test is listed with its reason and
  never counts as a pass.

## Procedure

1. Confirm the contract (goal, requirements, overrides and the worker
   policy disclosure).
2. If this is a re-verification, run `scripts/forge.py scope` and ask
   once: `Review (recommended)` or `Extensive review (rarely needed)`,
   showing each option's parts and judged-rule count.
3. Run the static checks: `scripts/forge.py check --decision <record>
   --root <repository> --revision <rules file>`.
4. Launch a fresh review worker with the plugin's `workers/reviewer.md`
   and the frozen files; validate its response. An observed action
   outside its role discards its output and leaves the part not
   verified.
5. Run the cheap runtime tests the part has, in a throwaway workspace.
6. Write the report: `scripts/forge.py report ... --slug <slug> --write`
   creates `.harness/reports/<slug>.md`. Show each part's harness and
   goal result and every failing rule with its source.

Result meanings and the goal-result precedence are in
[references/contract.md](references/contract.md); read it before
explaining a result.
