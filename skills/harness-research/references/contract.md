# Research workers and their limits

Read this before launching research or source-support workers.

## Launching

Each worker is a new, independent CLI process of the user's harness, not a
native subagent and not this conversation. Stage only its role input
(`brief.json` or `finding.json`) in an empty temporary directory, start
it with the restrictions for its role, and read its single JSON output.

| Role | Allowed | Restricted to |
| --- | --- | --- |
| Research | Web search and fetch | The brief; no file writes, no commands |
| Source support | Nothing | Judging the supplied claim, quote and context |

## What is not guaranteed

Under Forge's `best-effort-v1` worker policy, these restrictions are
configured, not certified: complete tool absence and context, file and
network isolation are not guaranteed. Record each worker's configured
controls, observed tools and unknowns as worker evidence. A worker that
is seen acting outside its role fails its run, and its output is
discarded. An unproven restriction alone does not fail the run.

## Output handling

Validate every response against Forge's worker-response schema before
using it. Malformed output, a missing verdict, a duplicate or unknown
finding id, or a verdict for another finding drops the affected finding;
never retry with wider permissions.
