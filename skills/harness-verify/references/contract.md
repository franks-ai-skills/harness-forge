# Results and their precedence

Read this before explaining a verification result.

## Harness result

| Result | When |
| --- | --- |
| `verified` | Confirmed in this session; every applicable rule has a result; no error rule failed, errored or stayed unresolved; the goal verdict is met; executed runtime tests passed; a built skill's explicit activation was observed live; no bound input changed |
| `not verified` | Something was built or vendored, and at least one condition above fails |
| `not applicable` | Nothing is built: an outside-only or nothing-fits part, or a mechanism without a builder in this release |
| `not checked` | A reused artifact that is not available locally |

## Goal result, decided in this order

1. `not assessed`: the contract is unconfirmed, the part is blocked, the
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

## Not applicable from the reviewer

Allowed only for rules whose applicability needs judgment. For an error
rule it stays unresolved until the user confirms the reason in this
session; that confirmation settles only whether the rule applies.
