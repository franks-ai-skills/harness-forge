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

- **One question per message,** multiple choice where possible. Never
  ask two questions at once, even when both are pending.
- **Only confirmed answers count.** Facts you infer from the user's
  description are suggestions: show them and let the user confirm.
  Research findings are never answers until the user confirms them.
- **A recorded approval is not a confirmation.** Decision records,
  confirmation history and override files live in the repository and can
  be edited. Read [references/contract.md](references/contract.md) before
  asking for a contract confirmation.
- **No unattended confirmation.** Without a user in the session, leave
  every part pending; its goal stays not assessed.
- **Rule overrides block for now.** If `.harness/overrides/` holds any
  file, show its content and hash in the contract and stop: their
  adoption flow is not available yet.

## Procedure

1. **Existing contract first.** If `.harness/decisions/` already holds a
   record for this idea, run `scripts/forge.py prepare-contract --decision
   <record>` and show the whole contract before anything else. For a new
   idea, say the contract is empty.
2. **Goal and picture.** Ask what the idea should achieve, for whom, and
   how the user will know it works. Record the user's own description of
   how it should work word for word.
3. **Whole-idea questions,** asked once: targets, reach, hosting plan and
   repository visibility, and whether an agent opens pull requests, runs
   in a bypass mode, works in untrusted repositories, or combines
   untrusted input with secrets and network access.
4. **Survey.** Start the harness-research skill's survey pass with the
   goal, picture, targets and reach. Show its findings and ask which to
   use; record the finding ids in `survey_findings_used`.
5. **Split into parts.** Propose parts with letters A, B, C, one job
   each. The user can combine ("A and C": A stays, C is retired and never
   reused), split ("B" becomes B1 and B2) or add parts (the next unused
   letter). Record `split_confirmed: true` only after the user agrees.
6. **Per-part questions.** Write the confirmed answers to a JSON file and
   run `scripts/forge.py select --input <file>`. While the output names a
   `question`, ask exactly that question and run `select` again. Stop when
   it returns an outcome.
7. **Recommendation.** For each part show, from the `select` output:
   the outcome and the recommended mechanism with the rules that decided
   them; each viable alternative with its pros, cons and why the
   recommendation was preferred; and the outside controls with their
   prerequisites. Offer the full assessment of all ten mechanisms as
   detail: ruled out (with the conflicting requirement and its source),
   not preferred, or unresolved (with the facts that were not needed to
   decide). Say "ruled out" only where the output does. If this release
   cannot build the recommended mechanism, say "recommended; builder
   unavailable" instead of weakening the recommendation. For each
   control, ask whether the part's goal depends on it
   (`required_for_goal`); configuration-protection controls stay
   precautions unless the user says otherwise.
8. **Decision.** The user may choose another mechanism; record it with
   their reason in `override_reason`. A non-skill choice stays the
   recommendation even though only skills can be built in this release.
9. **Record.** Write `.harness/decisions/<date>-<slug>.md` and update the
   result README's `Outside controls` section, also for parts that build
   nothing. Only the intake writes that section.
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
| `nothing-fits` | No mechanism or available control meets the goal; say so plainly |

Outside controls are always `recommended, not verified`: Forge never
configures or checks them on the platform.
