# Harness Forge

Harness Forge is being built as a subscription-only workflow for Claude
Code and Codex: intake, research, mechanism selection, accepted requirements,
portable builds and verification against a pinned knowledge base.

This checkout currently contains the local bootstrap, feasibility probes
and their evidence. The installable pipeline and live functional support
are not complete. Implementation follows the sibling playground's
[design](../playground/specs/2026-10-06-harness-forge-design.md) and
[plan](../playground/plans/2026-10-07-harness-forge-thin-slice.md).

## Worker isolation limitations

On 2026-10-09 the project owner accepted **automated workers with weaker
isolation**, recorded as `best-effort-v1`. Both harnesses will run their
own research, source-support and review workers with explicit role inputs,
separate sessions/processes and available restrictions. **Complete tool
absence, context isolation and prevention of unauthorized reads, writes
or network access are not guaranteed.**

| Harness | Evidence and remaining gap |
| --- | --- |
| Codex | Codex 0.160.1 accepted a modified model catalog, but the diagnostic did not establish an empty effective tool registry. Extensions, automatic discovery, inherited configuration and authentication boundaries remain unqualified. |
| Claude Code | Claude Code 2.1.292 advertised an empty tool list in the tested `team-profile` Team profile. It still advertised skills, agents and plugins. That observation does not qualify complete context/file/network isolation or transfer to the default `personal-profile` profile. Research and review workers still need functional checks. |

These are version/profile-specific observations, not claims that either
harness is incapable of stronger restrictions. See the
[Codex result](evals/canaries/results/catalog-spike-2026-10-09.md),
[Claude result](evals/canaries/results/offline-init-2026-10-08.md) and
[M1 evidence](evals/canaries/results/worker-boundaries.md).

Use this mode only for trusted projects and operator-approved inputs.
Do not rely on it to protect secrets, unrelated files or login credentials
from a worker, prevent their transmission, or contain hostile content.
Fresh sessions and instructions reduce intended context and actions; they
do not prove that the harness cannot discover more. Accepting findings
afterward cannot undo an earlier read, write or transmission.

The implementation will disclose configured controls, observed behavior
and unknowns in each run's contract, report and generated-result README.
Unproven isolation alone is accepted by this policy. Missing capabilities,
invalid verdicts, failed source checks and observed out-of-scope actions
still fail the affected run. Requirements need user acceptance before
building. No broader tool configuration or account switch is an automatic
fallback.

An artifact result of `verified` will mean its confirmed rules and goals
were checked under this policy; it will **not certify worker containment**.
A goal requiring enforced isolation cannot be met by this policy alone.
The exception does not relax deterministic-checker protections or the
separate disposable environments required for candidate runtime tests.

## Model access

Use subscription-authenticated harness CLIs only. There is no direct-model
API fallback, extra API-key requirement or API-credit dependency.
`personal-profile` is the owner's default Claude profile; `team-profile` was a
one-time diagnostic exception, not a required public-user setup. Public
installation must work with users' own subscription profiles.

## License

[AGPL-3.0-only](LICENSE). A generated-output permission is planned, but
its exact wording still needs approval before publication. This README
does not itself grant that exception.
