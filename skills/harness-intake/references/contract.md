# Contract confirmation

Read this before asking the user to confirm a contract, in intake or in
verification.

## What to show

Run `scripts/forge.py prepare-contract --decision <record>`, adding
`--revision .harness/research/<slug>.rules.yaml`, `--overrides
.harness/overrides` and `--evidence` when they exist. Show its `text` in
full, unchanged: the idea's reach, hosting and confirmed facts; every
part's name, goal, confirmed answers, outcome and mechanism, the reused
artifact of a reuse part, outside controls, accepted requirements with the revision's
sha256, every override with its content and sha256, the worker policy
(`best-effort-v1`, isolation not certified) and the contract digest. A
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

After an explicit confirmation, pass exactly these fields to Forge:

```json
{"choice": "confirm", "displayed_digest": "<digest you showed>", "part_ids": ["A"], "run_id": "<this run>"}
```

Forge checks the digest, the run and the part ids; it cannot check that
the answer came from the user, so build the relay only from the user's
answer in this session. For `Leave pending` or anything unclear, relay
`"choice": "pending"`. Parts that are not confirmed are blocked, and
their goals are reported as not assessed.

Nonempty rule overrides block confirmation until their adoption flow is
available.
