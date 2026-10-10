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
3. Run `scripts/forge.py verify --decision <record> --root <repository>
   --revision <rules file> --harness <this harness> --evidence <workers
   output> --relay <relay> --slug <slug> --write`, adding `--cases <file>`
   when the part has runtime cases. It runs the static checks, a fresh
   review worker on the frozen files and each runtime case in a throwaway
   workspace, then writes `.harness/reports/<slug>.md`. A worker
   configuration other than the confirmed one leaves every part not
   verified; a reviewer seen acting outside its role voids its verdicts.
   Without a confirmation, `scripts/forge.py check` runs the static
   checks alone.
4. Show each part's harness and goal result, every failing rule with its
   source, and each runtime case that did not run, with its reason.

Result meanings and the goal-result precedence are in
[references/contract.md](references/contract.md); read it before
explaining a result.
