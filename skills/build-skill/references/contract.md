# What the build step checks

Read this when Forge refuses a build or reports a conflict.

| Check | Refused when |
| --- | --- |
| Outcome and mechanism | The outcome is `outside-instead`, `nothing-fits` or `reuse`, or the chosen mechanism is not `skills` or `commands` ("builder unavailable") |
| Confirmation | No confirmation from this session covers the part, or it is for another contract |
| Accepted revision | The decision record names another revision, or the contract showed other requirements than the revision holds |
| Name | The part's slug is not 1–64 lowercase letters, digits and single hyphens |
| Description | Empty, more than one line, or over the description limit |
| Procedure | Empty, or it uses Claude Code-only placeholders |
| Paths | A file would land outside `.agents/skills/<name>/`, or a directory on the way is a symbolic link |
| Existing content | A file differs from the plan, or `.claude/skills/<name>` is not the planned relative link |

A refused build writes nothing. Conflicts come with a unified diff;
replace only the files the user names.
