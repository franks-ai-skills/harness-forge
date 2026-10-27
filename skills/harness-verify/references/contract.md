# Results and their precedence

Read this before explaining a verification result.

## Harness result

| Result | When |
| --- | --- |
| `verified` | Confirmed in this session; every applicable rule has a result; no error rule failed, errored or stayed unresolved; the links between parts hold; the recorded selection is current and no goal conflict remains, a kept one included; no security or compatibility drift is open; the goal verdict is met; executed runtime tests passed; every declared behavior case passed in this harness; a built skill's explicit activation was observed live; no bound input changed. With adopted overrides the report says "verified with N overrides" and names them |
| `not verified` | Something was built or vendored, and at least one condition above fails |
| `not applicable` | Nothing is built: an outside-only or nothing-fits part, or a mechanism without a builder in this release |
| `not checked` | A reused artifact that is not available locally |

The review worker's verdicts, including whether the procedure is
consistent with its own requirements and allowlists, are a static
assessment of the built files: they do not demonstrate live behavior or
certify isolation, and `verified` does not mean every inconsistency was
found. Runtime results are reported separately.

## Behavior tests

A behavior case checks what a built skill does once it runs. Forge runs
it in a throwaway workspace with only the frozen skill, the case's
fixtures and the dependencies it lists, never a copy of the repository,
and the harness lets the skill write only the declared paths while it
runs: Claude Code through `Edit` rules in `dontAsk` mode, with reads
blocked outside the workspace and no shell (Claude Code 2.1.257 or later;
older or unknown versions make the case unavailable); Codex through
Forge's permission profile, which reads only the workspace, writes only
the declared paths and has no network. Codex's sandbox grants a whole
subtree for a directory or a path that does not exist yet, and a command
can turn a granted file into a directory, so in Codex a case with an
exact write is unavailable: declare `dir/**` there, or test exact paths
in Claude Code. Anything changed outside the
declared writes fails the case; the comparison of the workspace before
and after the run is further evidence, since a file written and removed
again leaves no trace in it.

A case passes only when the skill was observed to activate, as an
activation case sees it, and every assertion held: files the fixtures
already held prove nothing alone. `no_denied` counts the refusals Claude
Code reports; Codex does not report refused file operations inside its
commands, so a case asking for it there is unavailable. A command check
sees the recorded command text, not what a script it starts does.

Each declared case needs exactly one result in each harness it names. A
failed case keeps the part from `verified`; an unavailable or erroring
one does too, as unverified behavior, which is no claim that the build is
wrong. Results are bound to the case, its fixtures and the bytes of its
dependencies: changing any of them means testing again.

## Goal result, decided in this order

1. `not assessed`: the configuration summary is unconfirmed, the part is blocked, the
   harness result is `not checked`, or a bound input of a part without a
   build (its goal, its README hand-off) changed after the run was
   prepared.
2. `not met`: the harness result is `not verified`, or the confirmed
   outcome is nothing fits. A failed build is a failure, never a
   dependency.
3. `depends on outside controls`: the goal needs at least one outside
   control (listed by name), and the harness result is `verified` or
   `not applicable`. Forge never checks platform settings.
4. `met by the verified build`: verified, and the goal needs no outside
   control. Precautionary controls do not count as goal dependencies.

## Bound inputs

The result is bound to the sha256 of every checked file and link, the
Harness KB version and rules, overrides, the accepted Idea KB revision,
each part's record, the checker versions and the worker evidence. A
change to any of them returns the affected parts to `built`; a shared
file such as the README affects every part that owns it.

## Provenance

The report names the Harness Forge copy that verified (its declared
version and, for a Git checkout of Forge itself, the commit and whether
it had uncommitted changes) and, per part, the copy that last built it
successfully, as the build recorded it in the decision record. Any value
that could not be established is `unknown`. "Built outputs" compares the
checked files and links with the ones that build left; a hand edit since
then shows as changed. It is information only and changes no result.

## Not applicable from the reviewer

Allowed only for rules whose applicability needs judgment. For an error
rule it stays unresolved until the user confirms the reason in this
session; that confirmation settles only whether the rule applies.

## Re-verification scope

Forge chooses the scope itself and asks nothing. A Review run sends the
reviewer only its scope: the fixed parts, the parts linked to them,
every part whose bound inputs or confirmation changed and every part
whose earlier result or runtime evidence cannot be kept. It keeps
results only from the report Forge recorded writing for this repository
(outside it, in Forge's own data directory), unchanged; an edited,
foreign or unrecorded report, or `--scope extensive`, judges every part,
and the report's "Review scope" line says which and why. Every
part's script rules and link checks run again in every run. A part
outside the scope keeps its earlier judged results and goal verdict,
each marked "kept from attempt A of run X" (or "kept from run X" from a
report made before attempts were recorded), with its runtime evidence
marked the same way, and stays verified only if it was
verified then and its scripts pass now. Runtime cases and fixtures are
not shown to the reviewer: when only they changed, or a part's earlier
runtime evidence no longer holds, a part verified last time keeps its
judgments and its runtime cases run again, and it stays verified only if
each required case passes now; a part not verified last time is judged
again in full.
