# Harness Forge

Harness Forge is being built as a workflow for Claude Code and Codex,
run through their CLIs with your existing login: intake, research,
mechanism selection, accepted requirements, portable builds and
verification against a pinned knowledge base.

Status: the workflow runs live in Claude Code and Codex from a local
checkout, with research, source-support and review workers launched as
separate CLI processes and runtime tests of the built skill. On
2026-10-11 the automated live runs and one hands-on session per harness
completed it. Goal conflicts, repository rule overrides, knowledge-base
drift with its issue proposals, checks of the links between parts and
re-verification with a Review or Extensive scope followed the same day.
The reference checks and installation are not done yet, so no harness
is supported for general use yet. The feasibility probes and their evidence remain in
`evals/canaries/`.
Implementation follows the sibling playground's
[design](../playground/specs/2026-10-06-harness-forge-design.md) and
[plan](../playground/plans/2026-10-07-harness-forge-thin-slice.md).

## Worker isolation limitations

On 2026-10-09 the project owner accepted **automated workers with weaker
isolation**, recorded as `best-effort-v1`. Both harnesses run their
own research, source-support and review workers in independent CLI
processes with explicit role inputs and the baseline controls below.
Native subagents are excluded from this release's worker adapters.
**Complete tool absence, context isolation and prevention of unauthorized reads, writes
or network access are not guaranteed.**

| Harness | Evidence and remaining gap |
| --- | --- |
| Codex | In M1, tested native launches returned repository-instruction/skill canaries and created write markers; the child-role application was not authoritative, so these are launch-path failures. Initial separate-process support/review runs made successful connector web calls despite their no-network settings. Explicit app disabling removed those calls in repeated probes, but file/write denial remained unproven. The later catalog spike accepted configuration without establishing an empty effective tool registry. |
| Claude Code | The tested native reviewer returned unrelated and outside-file canaries. Separate-process probes showed the expected tool lists and no successful canary leak or write across all three roles; some out-of-workspace Read attempts returned errors. The later empty-tool observation applies only to the tested Team-plan profile, which still advertised skills/agents/plugins; it does not carry over to other accounts or profiles. Complete role/context/authentication isolation and production functional support remain unqualified. |

These are version/profile-specific observations, not claims that either
harness is incapable of stronger restrictions. See the
[Codex result](evals/canaries/results/catalog-spike-2026-10-09.md),
[Claude result](evals/canaries/results/offline-init-2026-10-08.md) and
[M1 evidence](evals/canaries/results/worker-boundaries.md).
Observed boundary violations reject those tested launch configurations;
accepting the policy does not rehabilitate them or prove all native
subagents incapable of stronger restrictions.

The planned adapters must use an initially empty temporary working
directory outside the target repository, stage only the role's supplied
inputs, pass a small environment allowlist (plus `CLAUDE_CONFIG_DIR` or
`CODEX_HOME` when the user has set them), close inherited extra file
descriptors, and suppress optional configuration/hook/plugin discovery.
Do not resume/fork a prior session or fall back to native delegation.
Use no configured MCP servers or apps: Codex must explicitly disable
apps (including `apps._default.enabled=false`), suppress user/project
server discovery and disable any remaining configured server by ID;
Claude must use its strict empty-MCP configuration and role tool lists.
An empty Codex table alone is not proof that lower-layer servers vanished.
Known failure to apply a required baseline control blocks launch;
unobserved runtime containment remains disclosed rather than invented.

Per-role launch restrictions are also required: Claude research gets only
web tools, source support an empty tool list, and review only read tools.
Codex uses a no-write filesystem policy, web search only for research,
and disabled shell/local-image tools for source support. Configure input
read limits and additional tool removal wherever supported. These settings
are checked against supported CLI versions; they do not prove all remaining
tools, reads or outbound channels are blocked.

Use this mode only for trusted projects and operator-approved inputs.
Do not rely on it to protect secrets, unrelated files or login credentials
from a worker, prevent their transmission, or contain hostile content.
Fresh sessions and instructions reduce intended context and actions; they
do not prove that the harness cannot discover more. Accepting findings
afterward cannot undo an earlier read, write or transmission.

The implementation will disclose configured controls, observed behavior
and unknowns in each run's contract and `.harness/reports/<slug>.md`.
Generated artifacts do not need a generic Forge-isolation README section;
their own runtime limitations and required outside controls still belong
in their README.
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

## Outside controls

**Research environment isolation — recommended, not verified.** Research
fetches untrusted web content even when the project is trusted. Run it in
an operator-provided disposable environment with only the approved brief
and source policy, no target-repository mount, and no project secrets or
unrelated account credentials. Where the operator controls fetching, block
private, loopback, link-local and cloud metadata destinations, covering
IPv4/IPv6, resolved addresses and redirects. This applies
[OWASP's external-request guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
to the research environment. Public-web research still permits outbound
transmission to public sites; this is not an exfiltration guarantee.
Forge does not provision or verify these outside controls.

The harness still needs its subscription authentication. Keep it separate
from supplied worker data where the platform permits; `best-effort-v1`
does not prove the worker cannot read or misuse it. This recommendation
therefore does not promise a credential-free environment or remove the
accepted authentication/egress gap. It does not mandate Docker or add a
new API credential requirement.

An operator may choose a separate worker profile to reduce unrelated
history/configuration exposure. Forge does not require or provision one.
A fresh directory alone does not prove credential or keychain isolation;
using the existing account can expose more than its login material.

## Model access

Forge runs the standard `claude` and `codex` CLIs with the login they
already have. The account location is the vendor default, or the one the
user has set in the vendors' own `CLAUDE_CONFIG_DIR` or `CODEX_HOME`
variable; Forge passes these through unchanged and never sets them. No custom profile,
personal alias, shell configuration or owner-specific plugin is required.
Temporary per-worker restrictions apply without changing saved settings.
Forge does not log in or out, copy credentials or switch accounts. Without
a login, the run is unavailable.

**Check your login type before a run.** Forge uses your login as it is.
A subscription login draws on your plan's usage limits. An API-key login
bills each request to your API account, so a Forge run can cost extra.

| CLI | Command | A subscription login shows |
| --- | --- | --- |
| Claude Code | `claude auth status` | `"authMethod": "claude.ai"` |
| Codex | `codex login status` | `Logged in using ChatGPT` |

Forge runs the same commands before it starts the first worker of a run.
If the login is not a subscription, or its type is unclear, Forge says
that the run may cost extra and asks whether to continue or stop. A run
without a user stops at that point. Forge never adds an API key or falls
back to another account.

The project owner's custom setup is a temporary local-development exception
on one machine. It must never become a product default or installation
step. Personal profile names in diagnostic evidence identify historical
observations only; they do not specify public account behavior.

## License

[AGPL-3.0-only](LICENSE). A generated-output permission is planned, but
its exact wording still needs approval before publication. This README
does not itself grant that exception.
