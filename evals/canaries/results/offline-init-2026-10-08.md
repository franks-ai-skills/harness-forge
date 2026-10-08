# Claude host startup inventory

On 2026-10-08, the authorized subscription-only follow-up observed
`system/init.tools: ["Read"]` in the positive control and
`system/init.tools: []` in the restricted candidate. Both reported
`claude-opus-5-5` and API-key source `none`. The guarded `team-profile`
wrapper confirmed subscription authentication; the default afterward
remains `personal-profile`.

Four positive local socket controls worked. Under the same OS deny-all
network profile used by the CLI, seven direct and seven descendant
connection attempts returned `EPERM`. No API keys, API credits, billing
changes or paid fallback were used. Neither probe produced an assistant
response or terminal usage/cost event; cost prevention rests on the
external network prohibition, not missing usage fields.

Both CLI processes emitted six internal `api_retry` events after init
and were terminated at the 30-second cap. There were no process
relaunches; the no-retry limit did not disable the CLI's own retry loop.
These are startup observations, not successful completed worker runs.

The [separate evidence index](offline-init-2026-10-08-index.json) hashes
nineteen local artifacts. The complete result, cost boundary, timing,
limitations and next steps are in the sibling playground checkout at
`plans/2026-10-08-harness-forge-offline-init-results.md`.

Two prompt-bearing launches are charged, leaving 28 of the original 30.
Both allowed configuration corrections are used. Earlier evidence and
the M1 grader remain unchanged. No guest, authentication boundary,
Codex launcher or production support is certified. No container setup
or T3 work is authorized by this result.
