# Research workers and their limits

Read this before telling the user what the research and source-support
workers may do, what their isolation does not guarantee, or why
`scripts/forge.py research` discarded a worker's output. The script
launches and checks the workers; never launch one yourself or run one
again with wider permissions.

## How the script runs them

Each worker is a new, independent CLI process of the user's harness, not a
native subagent and not this conversation. The script stages the worker's
role request in a fresh temporary directory, adding the CLI's
output-schema file where required. It also shows the request in the
worker's prompt because research and support workers have no file tools.
It starts the worker with its role's restrictions and reads its single
JSON output.

| Role | Allowed | Restricted to |
| --- | --- | --- |
| Research | Web search and fetch | The brief; no file writes, no commands |
| Source support | Nothing | Judging the supplied claim, quote and context |

## What is not guaranteed

Under Forge's `best-effort-v1` worker policy, these restrictions are
configured, not certified: complete tool absence and context, file and
network isolation are not guaranteed. The script records each worker's
configured controls, observed tools and unknowns as worker evidence. A
worker seen acting outside its role fails its run, and its output is
discarded. An unproven restriction alone does not fail the run.

## Discarded output

The script checks every response against Forge's worker-response schema.
A research worker that fails or answers outside the schema yields no
findings. A support reply that is malformed, has no verdict, holds a
duplicate verdict or one for another finding drops its finding.
