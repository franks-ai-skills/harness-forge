# Worker diagnostic: no-model gate

On 2026-10-08 the user approved the bounded diagnostic in playground's
`plans/2026-10-08-harness-forge-worker-diagnostic.md`. The no-model gate
stopped **unproven** for both harnesses, including after resumption with
corrected interruption accounting. No live model invocations,
container setup or qualification runs occurred. T3 remains stopped.

The detailed findings, timing, limitations and decision costs are in
the sibling playground checkout at
`plans/2026-10-08-harness-forge-worker-diagnostic-results.md`.
This report is accompanied by [the separate evidence index](diagnostic-2026-10-08-index.json).
Its twenty hashed artifacts remain ignored local files. No new grader
was run; the null grader hash records that the live phase was not reached.
The [resumed index](diagnostic-2026-10-08-resumed-index.json) adds twenty-three
artifacts and the final counters without changing the historical index.

Codex 0.160.1's installed macOS binary matches the vendor release
artifact. Matching source identifies `features.view_image` and an
internal empty `ToolPolicy` allowlist, but no complete strict-valid
tool-free CLI configuration or guest build was qualified. The attempted
strict parser check was rejected because `features list` does not
support `--strict-config`, before validating the settings. On resumption,
offline app-server startup accepted the shell/image feature controls
under strict validation with the bundled `gpt-6-sol` catalog; an invented
setting was rejected. This is parser evidence, not full tool removal.

Claude 2.1.292 answered an initialization-only control request with
the requested model `claude-opus-5-5`, but supplied no effective tool
inventory. No user/model request was sent. The later user instruction
selects `personal-profile`, which uses a separate personal login profile;
that wrapper was inspected but not run, and the raw-CLI observation
does not qualify it. The user subsequently selected `team-profile` for this
diagnostic only. Its actual zsh function definitions ran with network
denial and a guard against account login/logout changes. Its account/help
check succeeded; initialization with the pinned model and debug logging
still exposed no effective tool inventory. `personal-profile` remains the
later local-development selection on this machine only. These historical
profile names are not public defaults; see [Model access](../../../README.md#model-access).
Neither authentication isolation nor a guest environment
was certified.

The first stop incorrectly counted the rate-limit interruption against
the gate. The correction conservatively charged twenty prior active
minutes and allowed ten more from 14:40:14 UTC. The resumed gate stopped
at 14:45:31 after 317 seconds for missing capability evidence, before
its allowance expired. Exact prior active usage is unknown. Total live
calls remain zero; one pre-live configuration correction is charged.

The M1 code, grader, streams and `evidence-index.json` are unchanged.
No production support, public distribution, design amendment or parity
exception is declared. Further diagnostic work requires a bounded handoff;
the detailed report lists the alternatives and their costs.

## Subsequent review

The suggested Codex protocol-schema search was completed under OS
network denial, including experimental methods. No declared method
returning a thread's complete effective tool inventory was found; app
and MCP catalogs are not the complete registry. The separate
[review evidence index](diagnostic-2026-10-08-review-index.json) records
the generated schemas and command. No model turn or thread was started.

Playground's `plans/2026-10-08-harness-forge-offline-init-follow-up.md`
records the separately authorized Claude `system/init` check with
network-blocked, prompt-bearing launches and subscription-only access.
The [follow-up observed an empty host startup inventory](offline-init-2026-10-08.md)
with a valid `Read` control. This resolves the previous host observation
gap only; prior indexes remain historical and unchanged. No guest,
Codex capability or T3 status is promoted.
