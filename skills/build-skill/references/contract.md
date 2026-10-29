# What the build step checks

Read this when Forge refuses a build or reports a conflict.

| Check | Refused when |
| --- | --- |
| Outcome and mechanism | The outcome is `outside-instead`, `nothing-fits` or `reuse`, or the chosen mechanism is not `skills` or `commands` ("builder unavailable") |
| Confirmation | No confirmation from this session covers the part, or it is for other inputs |
| Accepted revision | The decision record names another revision, the configuration proposal showed other requirements than the revision holds, or the requirements were researched for another brief than the part's current one |
| Selection | The recorded outcome and recommendation are not what the part's answers give under the effective rules, an answer is still open, or a goal conflict the user has not answered remains |
| Status | The part is blocked; a rebuild never clears a block |
| Name | The part's slug is not 1–64 lowercase letters, digits and single hyphens |
| Description | Empty, more than one line, or over the description limit, an adopted override's included |
| Procedure | Empty, or it uses Claude Code-only placeholders |
| Checklist (`--checklist`, optional) | Not a JSON object holding exactly the part's accepted requirement ids, or a line is empty, not text, more than one line, over 2,000 characters, uses a placeholder, or starts with its own obligation label or word |
| Paths | A file would land outside `.agents/skills/<name>/`, or a directory on the way is a symbolic link |
| Existing content | A file differs from the plan, or the skill's directory holds a file Forge wrote earlier that the plan no longer has, and it is neither the part's last build output nor the version committed in git (on disk and in the index) nor named in `--replace`; or a directory or another type of file is where the plan has a file or `.claude/skills/<name>` link |

The checklist checks are syntax only: whether a line keeps its
requirement's meaning and strength is judged in harness-verify, against
the accepted statement. A refused build writes nothing. An existing
file that differs from the plan and that the "Existing content" check
does not stop at is replaced, or removed when the plan no longer has
it; a failed or inconclusive git check never counts as a match with the
committed version. To repair a
part after a failed verification, rebuild it, then harness-verify
re-verifies it, judging again what Forge chooses.
