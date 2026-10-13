---
name: harness-verify
description: Verifies built Claude Code and Codex skills against the pinned Harness KB rules, the accepted requirements and each part's confirmed goal, and writes a contract-bound report. Use after build-skill, after fixing a part, or when asked whether a built harness setup meets its goals. Not for deciding what to build or for checking platform settings such as branch protection.
---

# Harness verify

Checks what was built against the rules that apply and the goal the user
confirmed, and reports two results per part: the harness result for
what was built, and the goal result.

`scripts/forge.py` is in the Forge plugin root, two directories above
this skill's folder. Run it with `python3`: it switches to Forge's own
Python environment. If it stops because that environment is not set up,
ask the user before running `python3 scripts/setup.py` from the plugin
root, which downloads Forge's pinned packages from PyPI once.

## Rules that are easy to get wrong

- **Confirm the current contract first,** in this session, as
  harness-intake's contract reference describes. Without it, every part
  stays blocked and its goal is reported as not assessed. A run without
  a user still runs the static checks.
- **Scripts decide first and cannot be overruled.** The review worker
  judges only the rules the run requests; it never sees or changes
  script results.
- **Verified has a narrow meaning:** every applicable rule has a result,
  no error rule failed or stayed unresolved, no goal conflict or open
  security or compatibility drift remains, the goal verdict is met,
  executed runtime tests passed, a built skill's explicit activation was
  observed, and no bound input changed. It never
  means outside controls are configured, and it never certifies worker
  isolation; the report always says `not-certified`.
- **Runtime tests are reported separately.** Without them the report
  says `none run`; an unavailable test is listed with its reason and
  never counts as a pass. Codex reports no inventory of the skills,
  plugins and MCP servers it loaded, so its runtime results are
  best-effort: say so whenever the report states an inventory gap.

## Procedure

1. Confirm the contract (goal, requirements, overrides and the worker
   policy disclosure).
2. If this is a re-verification, run `scripts/forge.py scope --harness
   <harness> --decision <record> --root <repository> --revision <rules
   file> --evidence <workers output> --relay <relay> --slug <slug> --fixed
   <parts the builder fixed> --cases <runtime cases>` with the
   confirmation from step 1 and the harness and cases step 3 will run
   (only that harness's earlier runtime evidence can be kept), and ask
   once:
   `Review (recommended)` or `Extensive review (rarely needed)`, showing
   each option's parts and judged-rule count. Review judges the fixed
   parts, every part linked to them and every part whose inputs or
   confirmation changed. For each part it keeps, show every kept
   judgment and the goal verdict with its run: they come from the
   earlier report, which the repository can hold in any form, and the
   user's choice is what keeps them. Every part's scripts and link
   checks run again either way. For Review, pass `--scope review --fixed
   <parts> --scope-relay <file>` to step 3, the relay holding `choice`
   review, the option's `displayed_digest` and the confirmation's
   `run_id`; for Extensive review, pass `--scope extensive`. Each verify
   run writes a new report, so ask the scope question again before
   running step 3 again, also after confirming a rule as not applicable.
3. For a built skill, write its runtime cases as a YAML file shaped like
   `evals/smoke/vertical.yaml` in the Forge plugin root: an `explicit`
   case that names the skill, an `implicit` case with a request a user
   would make (not for a user-invoked command), and a `negative` near
   miss, a neighboring request that must not activate it, each with the
   fixtures its prompt needs. A Claude Code case may permit only `Skill`,
   `Read`, `Grep` and `Glob`: it runs without a sandbox, so a case that
   permits any other tool is unavailable. Run `scripts/forge.py verify --decision
   <record> --root <repository> --revision <rules file> --harness <this
   harness> --evidence <workers output> --relay <relay> --slug <slug>
   --write --cases <file>`. This harness must be one of the decision's
   targets: if it is not, tell the user to verify from a target
   harness, since the build's runtime tests would not run here. It runs the static checks, a fresh
   review worker on the frozen files and each runtime case in a throwaway
   workspace, then writes `.harness/reports/<slug>.md`. A worker
   configuration other than the confirmed one leaves every part not
   verified; a reviewer seen acting outside its role voids its verdicts.
   Without a confirmation, `scripts/forge.py check` runs the static
   checks alone.
4. If the output lists `unresolved` rules, the reviewer found an error
   rule not applicable. Show each with its reason and ask the user
   whether the rule does not apply to that part. Your own opinion does
   not settle it. Then run step 3 again with one
   `--confirm-not-applicable <confirmation>` per rule the user confirmed,
   passing the entry's `confirmation` value as printed. It is bound to
   the part's current inputs: after any change to them, ask again. The
   confirmation settles only whether a rule applies: if the new reviewer
   finds it applicable, its verdict stands.
5. If `goal_conflicts` lists an error the user has not kept, ask the
   goal-conflict question as harness-intake's contract reference
   describes; a kept conflict stays an error in the report. For each
   `drift` entry that is `blocking`, offer the harness-research skill's
   drift choices; the part stays blocked until the user decides.
6. Show each part's harness and goal result, every failing rule with its
   source, and each runtime case that did not run, with its reason.

Result meanings and the goal-result precedence are in
[references/contract.md](references/contract.md); read it before
explaining a result.
