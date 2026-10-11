---
name: harness-intake
description: Turns an idea for coding-agent tooling in Claude Code or Codex into a decided, user-confirmed plan with one recommended mechanism or outside control per part, and records it. Use when someone wants to build a skill, hook, subagent, command, plugin or automation, or asks which harness mechanism fits a goal. Not for building or verifying files; use build-skill and harness-verify.
---

# Harness intake

Turns an idea into parts, computes a recommendation for each part from
the pinned Harness KB, and records what the user decides. The
recommendation comes from `scripts/forge.py select`; you phrase the
questions and explain the result, you never pick a mechanism yourself.

`scripts/forge.py` is in the Forge plugin root, two directories above
this skill's folder. Run it with the Forge environment's Python (see the
plugin README). Every command prints bounded JSON.

## Rules that are easy to get wrong

- **One question per message, in the user's terms.** Forge's question
  texts say what to find out, not how to word it: ask about the user's
  own idea and say what the answer changes for it. Offer the choices as
  A, B, C, recommended first, and accept a letter as the answer; use the
  harness's multiple-choice tool where it has one. Don't cite this skill
  or Forge as the reason for a question. Never ask two questions at once,
  even when both are pending.
- **Only confirmed answers count.** Facts you infer from the user's
  description are suggestions: show them and let the user confirm.
  Research findings are never answers until the user confirms them.
- **A recorded approval is not a confirmation.** Decision records,
  confirmation history and override files live in the repository and can
  be edited. Read [references/contract.md](references/contract.md) before
  asking for a contract confirmation.
- **No unattended confirmation.** Without a user in the session, leave
  every part pending; its goal stays not assessed.
- **Overrides apply only through the contract.** `prepare-contract`
  shows each file in `.harness/overrides/` with the old and new rule,
  its source quote and its warnings; it applies once the user confirms
  that contract. An override that cannot be adopted stops every command:
  run `scripts/forge.py overrides --root <repository>` and show why.

## Procedure

1. **Existing contract first.** If `.harness/decisions/` already holds a
   record for this idea, run `scripts/forge.py prepare-contract --decision
   <record>`, with `--revision` when it records accepted requirements, and
   show the whole contract before anything else. For a new
   idea, say the contract is empty.
2. **Goal and picture.** Ask what the idea should achieve, for whom, and
   how the user will know it works. Record the user's own description of
   how it should work word for word.
3. **Whole-idea questions,** asked once: targets, reach, hosting plan and
   repository visibility, and whether an agent opens pull requests, runs
   without permission prompts (the agent's prompts, not questions a skill
   asks the user), works in untrusted repositories, or combines
   untrusted input with secrets and network access.
4. **Survey.** Start the harness-research skill's survey pass with the
   goal, picture, targets and reach. Show its findings and ask which to
   use; record the finding ids in `survey_findings_used`.
5. **Split into parts.** Propose parts with letters A, B, C, one job
   each. The user can combine ("A and C": A stays, C is retired and never
   reused), split ("B" becomes B1 and B2) or add parts (the next unused
   letter). Record `split_confirmed: true` only after the user agrees.
6. **Per-part questions.** Write the confirmed answers to a JSON file and
   run `scripts/forge.py select --input <file>`. Add each reuse
   candidate the user confirmed from the survey under `candidates`, with
   its `name`, `source`, `license`, `maintenance`, `fit`, `concept` and,
   for a hook, its form as `variant`, with every setting that changes its
   effect: `synchronous command handler`, `async command handler` (also
   `asyncRewake`), `prompt handler without continueOnBlock`, `prompt
   handler with continueOnBlock` or `agent handler`, as its configuration
   shows it; leave `variant` out when the configuration does not show it,
   and Forge will not reuse it.
   While the output names a `question`, ask exactly that question and run
   `select` again. Stop when it returns an outcome.
7. **Recommendation.** For each part show, from the `select` output:
   the outcome and the recommended mechanism, with its `variant` when
   the output names one (such as a hook's handler), and the rules that
   decided them; each viable alternative with its `variant`, pros, cons,
   cited `pages` and why the recommendation was preferred; and the outside controls
   with their prerequisites. When an alternative's `variant` is null and
   `variant_missing_facts` is not empty, say that its form is still open
   and ask those questions only if the user considers that alternative.
   Describe a reused mechanism from the researched candidate in `reuse`
   and the required `variant`; the output gives no pros or cons for it.
   Record the candidate's own `variant` in the part's `reuse`. Offer the full assessment of all ten mechanisms as
   detail: ruled out (with the conflicting requirement and its source),
   not preferred, or unresolved (with the facts that were not needed to
   decide). Say "ruled out" only where the output does. For each
   `reuse_conflicts` entry, say which researched candidate was not reused
   and which requirement rules out its mechanism or its form. When
   `no_standalone_mechanism` is true, say that no supported standalone
   mechanism meets every confirmed requirement and that combinations of
   mechanisms were not assessed; do not say that nothing could work. If this release
   cannot build the recommended mechanism, say "recommended; builder
   unavailable" instead of weakening the recommendation. For each
   control, ask whether the part's goal depends on it
   (`required_for_goal`); configuration-protection controls stay
   precautions unless the user says otherwise.
8. **Decision.** The user may choose another mechanism; record it with
   their reason in `override_reason`. A non-skill choice stays the
   recommendation even though only skills can be built in this release.
   Run `select` again with the part's answers plus `chosen` and `goal`.
   A `goal_conflict` with severity `warning` only means another
   mechanism was recommended: say so. With severity `error`, a confirmed
   answer rules the choice out, and you ask the goal-conflict question
   described in [references/contract.md](references/contract.md#goal-conflicts).
9. **Record.** Write `.harness/decisions/<date>-<slug>.md`, then run
   `scripts/forge.py readme --decision <record> --root <repository>`. It
   writes the result README's `Outside controls` section, also for parts
   that build nothing, with each control's conditions spelled out. Only
   the intake writes that section.
10. **Confirm the contract.** Render it with `prepare-contract` and ask
    for confirmation as [references/contract.md](references/contract.md)
    describes. Unconfirmed parts stay pending.

## Outcomes

| Outcome | Meaning |
| --- | --- |
| `harness-only` | A harness mechanism alone fits |
| `outside-alongside` | An outside control is needed, with a harness mechanism for feedback |
| `outside-instead` | Only an outside control enforces it; nothing is built |
| `reuse` | A researched existing solution fits; fit, license and maintenance were confirmed |
| `nothing-fits` | No supported standalone mechanism or available control meets the goal; say so plainly, and that combinations were not assessed when `no_standalone_mechanism` is true |

Outside controls are always `recommended, not verified`: Forge never
configures or checks them on the platform.
