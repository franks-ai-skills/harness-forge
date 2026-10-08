# Worker diagnostic: no-model gate

On 2026-10-08 the user approved the bounded diagnostic in playground's
`plans/2026-10-08-harness-forge-worker-diagnostic.md`. The no-model gate
stopped **unproven** for both harnesses. No live model invocations,
container setup or qualification runs occurred. T3 remains stopped.

The detailed findings, timing, limitations and decision costs are in
the sibling playground checkout at
`plans/2026-10-08-harness-forge-worker-diagnostic-results.md`.
This report is accompanied by [the separate evidence index](diagnostic-2026-10-08-index.json).
Its twenty hashed artifacts remain ignored local files. No new grader
was run; the null grader hash records that the live phase was not reached.

Codex 0.160.1's installed macOS binary matches the vendor release
artifact. Matching source identifies `features.view_image` and an
internal empty `ToolPolicy` allowlist, but no complete strict-valid
tool-free CLI configuration or guest build was qualified. The attempted
strict parser check was rejected because `features list` does not
support `--strict-config`, before validating the settings.

Claude 2.1.292 answered an initialization-only control request with
the requested model `claude-opus-5-5`, but supplied no effective tool
inventory. No user/model request was sent. The later user instruction
selects `personal-profile`, which uses a separate personal login profile;
that wrapper was inspected but not run, and the raw-CLI observation
does not qualify it. Neither authentication isolation nor a guest
environment was certified.

The gate began at 10:36:04 UTC and its 30-minute deadline was 11:06:04.
At resumption, the clock read 12:58:28; the diagnostic was closed
without resetting its budget. This elapsed interval is not a claim of
continuous active investigation. Subsequent work records the result.

The M1 code, grader, streams and `evidence-index.json` are unchanged.
No production support, public distribution, design amendment or parity
exception is declared. Another diagnostic requires a bounded handoff;
the detailed report lists the alternatives and their costs.
