# Confirmation

Read this before asking the user to confirm, in intake, research,
building or verification. What the user confirms is named by its
purpose:

| Purpose (`--purpose`) | Name | When |
| --- | --- | --- |
| `research` | research proposal | Inputs for research: a new idea, its split, the final intake confirmation, and any confirmation before further research, also for a record with accepted requirements |
| `configuration` | configuration proposal | The researched configuration before building, with the accepted requirements and recorded drift decisions |
| `verification` | configuration summary | The inputs of a verification |

Choose by what comes next, not by what the record holds. Use the name in
everything you say about it. `prepare-contract` is an internal command
name: it prepares the research proposal, configuration proposal or
configuration summary for the current step.

## What to show

First run `scripts/forge.py workers --harness <this harness>` and save
its output as `workers.json` in the run directory that `run-dir`
allocated (see the skill's rule on run files). If a role reports `needs_cost_question`, the login is an API
key or unknown and can cost extra: ask the user `Continue` or `Stop`
and run it again with `--login-answer`. Never log in, log out or switch
accounts. Then run `scripts/forge.py prepare-contract --decision
<record> --evidence <workers output> --run-dir <run directory> --purpose
research|configuration|verification`, adding
`--revision .harness/research/<slug>.rules.yaml` when it exists; it
reads the repository's `.harness/overrides/` itself.

When the user confirmed this record before, add `--compare-with
<relay>`, naming the relay you wrote after the last confirmation of this
record (`relay.json`, `relay-2.json`, … in its run directory). Forge
then says what changed since, as far as the local files show it; if it
cannot use the relay, the summary gives the reason. Say nothing about
earlier confirmations beyond what the summary says.

It returns a `summary`, a `question` and its `options`, and writes the
complete details to a file in the run directory (`details.path`). Show
the `summary` in full and unchanged as your whole reply, as Markdown
outside a code block: add no heading, introduction, restatement or list
of your own. It is at most 25 lines, 245 words and 1,450 characters, so
that it fits on one screen with the question. It holds, in this order
and as far as room allows: what changed since the named confirmation
(weakened requirements first), goal conflicts, overrides and their
warnings, open drift decisions, parts not offered and research still to
do, each part's outcome and mechanism, and the link to the details file
as its last line. Anything not shown is counted by kind in its "Not shown
here" line and is in the details file, which holds everything confirmed
in full: every requirement, override change and warning, condition,
fact, fetched quote and worker configuration, for the user to open. Do
not paste the details file. The details file's `sha256` and the
confirmation `digest` are checks for Forge, not decisions for the user;
you need not show them.

`refused_parts` names each part whose recorded selection build would
refuse, with the reason: a stale selection, an open question or an
unanswered goal conflict. The summary lists them; offer only the other
parts for confirmation, and fix each refused part with `select` (or the
goal-conflict question) before it is confirmed.

`research` says, for each part to build or reuse in place, whether its
accepted requirements (`accepted`) and its deep research
(`deep_research`) are for the part's current brief. `stale` means the
requirements were accepted for an earlier brief: the idea's goals,
picture, targets or reach, or the part's goal, answers or mechanism
changed since. The summary lists each entry whose `accepted` is not
`current` with its `next`; say that build and verify refuse the part
until that is done. Research is not part of what is confirmed: confirming it
starts no research, and the deep pass and acceptance come after the
confirmation.

## How to ask

Ask `question` exactly as returned, as one multiple-choice question
with exactly its two `options`, `Confirm` and `Leave pending`, and no
option preselected. It names the summary's title and says that
`Confirm` covers the summary above and the linked details for the parts
it names. The user may also confirm in
their own words, naming the parts: "Confirm A and B" covers A and B only.

These never confirm a part:

- silence, or a reply that does not clearly confirm;
- an option that was preselected but not submitted;
- a confirmation recorded in the repository, or one from another run;
- a confirmation given before what it covers changed.

## The relay

After an explicit confirmation, write exactly these fields to
`relay.json` in the run directory (`relay-2.json` and so on for a later
confirmation, since a run file is never overwritten) and pass it with `--relay` to `research`, `build` and `verify`, together
with the same `--evidence`; they refuse a relay for any other inputs:

```json
{"choice": "confirm", "displayed_digest": "<prepare-contract's digest>", "part_ids": ["A"], "run_id": "<run-dir's run_id>"}
```

`displayed_digest` is the `digest` of the preparation whose summary you
showed. Forge checks the digest, the run and the part ids; it cannot check that
the answer came from the user, so build the relay only from the user's
answer in this session. For `Leave pending` or anything unclear, relay
`"choice": "pending"`. Parts that are not confirmed are blocked, and
their goals are reported as not assessed.

## Rule overrides

An override in `.harness/overrides/<rule-id>.yaml` replaces or retires
one pinned rule for this repository, in selection, building and
verification. The summary shows what each one changes and every
warning, in full, when it loosens or retires an error, security or
eligibility rule; the details file holds the pinned and the new rule,
the source quote it was adopted from, its content and its sha256. Read
every warning to the user before asking for confirmation. Confirming adopts
the overrides the summary shows; a changed override needs a new confirmation.
Pass `--root <repository>`, `--decision`, `--evidence` and the
confirming `--relay` to `select`, and `--evidence` and `--relay` to
`resolve-conflict` (it finds the repository from the decision), so that
their result uses the adopted overrides; without a confirmation they
decide with the pinned rules, and `select` shows what the overrides
would change as `override_preview`.

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

Every answer changes what is confirmed: prepare it again, show the new
summary and ask for a new confirmation. The parts in `invalidated_parts` are verified again.
