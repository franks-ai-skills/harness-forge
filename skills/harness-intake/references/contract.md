# Contract confirmation

Read this before asking the user to confirm a contract, in intake or in
verification.

## What to show

First run `scripts/forge.py workers --harness <this harness>` and save
its output. If a role reports `needs_cost_question`, the login is an API
key or unknown and can cost extra: ask the user `Continue` or `Stop`
and run it again with `--login-answer`. Never log in, log out or switch
accounts. Then run `scripts/forge.py prepare-contract --decision
<record> --evidence <workers output>`, adding
`--revision .harness/research/<slug>.rules.yaml` when it exists; it
reads the repository's `.harness/overrides/` itself. Show its `text` in
full, unchanged: the idea's reach, hosting and confirmed facts; every
part's name, goal, confirmed answers, outcome and mechanism, the reused
artifact of a reuse part, outside controls, accepted requirements with the revision's
sha256, every override with its content and sha256, the worker policy
(`best-effort-v1`, isolation not certified), each planned worker's
configuration and the contract digest. A
line marked as a hint from an untrusted record only points at
differences; it is not part of what the user confirms.

## How to ask

Ask one multiple-choice question with exactly two options, `Confirm` and
`Leave pending`, and no option preselected. The user may also confirm in
their own words, naming the parts: "Confirm A and B" covers A and B only.

These never confirm a part:

- silence, or a reply that does not clearly confirm;
- an option that was preselected but not submitted;
- a confirmation recorded in the repository, or one from another run;
- a confirmation given before the contract changed.

## The relay

After an explicit confirmation, write exactly these fields to a JSON file
and pass it with `--relay` to `research`, `build` and `verify`, together
with the same `--evidence`; they refuse a relay for any other contract:

```json
{"choice": "confirm", "displayed_digest": "<digest you showed>", "part_ids": ["A"], "run_id": "<this run>"}
```

Forge checks the digest, the run and the part ids; it cannot check that
the answer came from the user, so build the relay only from the user's
answer in this session. For `Leave pending` or anything unclear, relay
`"choice": "pending"`. Parts that are not confirmed are blocked, and
their goals are reported as not assessed.

## Rule overrides

An override in `.harness/overrides/<rule-id>.yaml` replaces or retires
one pinned rule for this repository, in selection, building and
verification. The contract shows each one: the pinned and the new rule,
the source quote it was adopted from, and a warning when it loosens or
retires an error, security or eligibility rule. Read every warning to
the user before asking for confirmation. Confirming the contract adopts
the overrides it shows; a changed override needs a new confirmation.
Pass `--root <repository>` to `select` so that its result uses them.

After a Forge update with a new Harness KB, `scripts/forge.py overrides
--root <repository>` compares each override with the new release. When
the release contains the correction or drops the rule, propose removing
the override (`--remove <rule-id>`); otherwise ask whether to keep it
(`--keep <rule-id>`). Until the user answers, the override stops every
command.

## Goal conflicts

Ask one multiple-choice question per conflicting part. First show the
current state from `goal_conflict`: the part's goal, the chosen
mechanism, each failing rule id with its statement and sources, and
the recommended mechanism with its pros and cons. Then offer, in this
order:

- A. **Use the recommended mechanism** (recommended).
- B. **Adjust the goal.** Propose a concrete wording the chosen
  mechanism can meet, built from `adjust` (the answers whose change
  lifts the conflict), and accept the user's own wording.
- C. **Keep as is.** The error stays in the report, and the part is
  never verified.

Record the answer and the user's reason with `scripts/forge.py
resolve-conflict --decision <record> --part <id> --choice
recommended|adjust-goal|keep --reason <reason>`, adding
`--adjusted-goal <goal>` for B. Then follow its `next_stage`:

| `next_stage` | Next |
| --- | --- |
| `build` | Build the part with the recommended mechanism |
| `intake` | The recommended mechanism has no builder in this release: say "recommended; builder unavailable" |
| `readme` | An outside control replaces the mechanism: run `scripts/forge.py readme` |
| `questions` | Ask the part's questions again, run `select`, and research and accept again where the goal changed them |
| `report` | Nothing changes |

Every answer changes the contract: show it and ask for a new
confirmation. The parts in `invalidated_parts` are verified again.
