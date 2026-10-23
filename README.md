# Harness Forge

Harness Forge is a plugin for Claude Code and Codex. It takes an idea
for agent tooling through intake, research, mechanism selection,
accepted requirements, a portable skill build and verification against
a pinned harness knowledge base. It runs through the vendors' CLIs with
your existing login. It builds skills into the target repository, in
`.agents/skills/<name>/`, linked from `.claude/skills/<name>` for Claude
Code, and installs nothing at user level.

Status: version 0.2.0, pinned to release 0.2.0 of its
knowledge base, [agent-harness-kb](https://github.com/franks-ai-skills/agent-harness-kb).
Version 0.1.0 was published together with the knowledge base on
2026-10-16; [CHANGELOG.md](CHANGELOG.md) lists the changes since.
The workflow passes its
automated live runs in Claude Code and Codex from a local checkout. The
installation procedures below were tested in throwaway locations. An
installed copy ran the whole workflow in Claude Code. Codex ran its
reference sessions on 2026-10-18 from the plugin installed from a local
marketplace, and their re-verification on 2026-10-21 from a checkout,
because the installed copy was out of date. No harness is supported for
general use yet.

## Installation

Requirements:

- Claude Code or the Codex CLI, logged in. See [Model access](#model-access)
  for what a run costs with each login type.
- Python 3.11 to 3.14 with `venv` and `pip`, as `python3`. Some Linux
  distributions package `venv` separately.
- The GitHub CLI `gh` only for reporting knowledge-base drift as an
  issue.

Claude Code:

```sh
claude plugin marketplace add franks-ai-skills/harness-forge
claude plugin install harness-forge@harness-forge
```

Codex:

```sh
codex plugin marketplace add franks-ai-skills/harness-forge
codex plugin add harness-forge@harness-forge
```

Start a new session afterwards. The plugin adds four skills:
`harness-intake`, `harness-research`, `build-skill` and
`harness-verify`. It adds no hooks, MCP servers or other components that
run code when the harness starts.

Install from the GitHub repository, not from a local working copy. A
local-path marketplace makes Codex copy the whole directory into its
plugin cache, including ignored files such as a virtual environment or
private evidence.

### Forge's Python environment

Forge validates its records with `jsonschema` and `PyYAML`. The first
`scripts/forge.py` command, run by a skill, stops and names the setup
command; the skill asks you before running it:

```sh
python3 <plugin root>/scripts/setup.py
```

It creates a virtual environment in Forge's own data directory,
`~/.local/share/harness-forge/env-<lock digest>/` (`$XDG_DATA_HOME`
when set; `%LOCALAPPDATA%\harness-forge` on Windows; `HARNESS_FORGE_HOME`
overrides all three). It installs exactly the wheels pinned with hashes
in `requirements.lock`, downloaded from PyPI once, and runs no build
code. It changes no user configuration. Later `forge.py` commands switch
to that environment by themselves. If that environment stops working,
for example after its Python was upgraded, `forge.py` names the setup
command again, which rebuilds it. A plugin update with a new lock gets
a new environment; delete the directory to remove it.

Without network access, download the wheels elsewhere with
`pip download --require-hashes --only-binary :all: -r requirements.lock -d wheelhouse`
and run `python3 scripts/setup.py --wheelhouse wheelhouse`. The tested
and expected platforms are in
[packaging/assessment.md](packaging/assessment.md#supported-environments).

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

The adapters use an initially empty temporary working directory outside
the target repository, stage only the role's supplied inputs, pass a
small environment allowlist (plus `CLAUDE_CONFIG_DIR` or `CODEX_HOME`
when the user has set them), close inherited extra file descriptors, and
suppress optional configuration, hook and plugin discovery. They never
resume or fork a prior session or fall back to native delegation. They
use no configured MCP servers or apps: Codex runs disable apps
(including `apps._default.enabled=false`) and user and project server
discovery; Claude runs use the strict empty-MCP configuration and role
tool lists.
An empty Codex table alone is not proof that lower-layer servers vanished.
Known failure to apply a required baseline control blocks launch;
unobserved runtime containment remains disclosed rather than invented.

Forge also checks what each worker reports doing. A Claude worker's
successful file access outside its workspace fails it. A Codex
worker's shell lines must fit a small grammar: reading commands such as
`cat`, `nl`, `grep`, `rg` and printing `sed`, with known options, plain
paths inside its workspace, and no variables, substitutions, globs or
redirections other than `2>/dev/null`
([workers/reviewer.md](workers/reviewer.md) lists it). Any other line,
whatever its exit code, fails the worker and discards its output. This
check sees only what the harness reports; it does not replace the
sandbox and does not prove containment.

Each role has its own launch restrictions: Claude research gets only
web tools, source support an empty tool list, and review only read tools.
Codex uses a no-write filesystem policy, web search only for research,
and disabled shell and local-image tools for source support. Forge checks
these settings against the installed CLI version before each launch; they
do not prove that all remaining tools, reads or outbound channels are
blocked.

Use this mode only for trusted projects and operator-approved inputs.
Do not rely on it to protect secrets, unrelated files or login credentials
from a worker, prevent their transmission, or contain hostile content.
Fresh sessions and instructions reduce intended context and actions; they
do not prove that the harness cannot discover more. Accepting findings
afterward cannot undo an earlier read, write or transmission.

Each run's contract and `.harness/reports/<slug>.md` disclose the
configured controls, observed behavior and unknowns.
Generated artifacts do not need a generic Forge-isolation README section;
their own runtime limitations and required outside controls still belong
in their README.
Unproven isolation alone is accepted by this policy. Missing capabilities,
invalid verdicts, failed source checks and observed out-of-scope actions
still fail the affected run. Requirements need user acceptance before
building. No broader tool configuration or account switch is an automatic
fallback.

An artifact result of `verified` means its confirmed rules and goals
were checked under this policy; it does **not certify worker containment**.
A goal requiring enforced isolation cannot be met by this policy alone.
The exception does not relax deterministic-checker protections or the
separate disposable environments required for candidate runtime tests.

### Gaps in the runtime tests of a built skill

Forge runs each built skill's test prompts in a throwaway workspace that
holds only the skill and the case's files. What the harness reports
limits what those tests can prove:

| Harness | Gap |
| --- | --- |
| Claude Code | The cases run without a sandbox, so only the `Skill`, `Read`, `Grep` and `Glob` tools can be permitted. Skills that stay listed with bundled skills turned off (your own, or built-ins that this setting keeps, such as `design`, `doctor` and `plugin-authoring` in 2.1.296) are turned off for each case with `skillOverrides`; a skill that still loads makes the case `not-run`. So does a candidate named like a bundled skill, which would answer its `Skill` call or `/<name>`. A user-invoked skill (`disable-model-invocation: true`) is out of the model's reach, so Forge runs its explicit case as `/<name> <prompt>`, sent as a stream-json message that Claude Code replays: only its own expansion of a resolved `/<name>` counts as activation, and a case without one reports `not-run`, not a pass. |
| Codex | Codex does not report which skills, plugins and MCP servers it loaded, so its runs are best-effort: `clean` is null and the summary states the gap. Activation counts only when Codex prints the skill's whole `SKILL.md` with one reading command that names no other file, or with one `cat` whose first file it is and whose output starts with it; a read chained with other commands reports `not-run`. A near-miss case has no signal that proves non-activation and reports `not-run`. A user-invoked skill (`policy.allow_implicit_invocation: false`) gets its explicit case as `$<name> <prompt>`; Codex injects the skill without a stream event, so that case passes only when the model also reads `SKILL.md`, and otherwise reports `not-run`. Codex 0.162 starts a `codex-code-mode-host` helper by default; on macOS, when the Homebrew cask installed it, Gatekeeper can stop its first launch at a prompt. |

A reused skill that ships in a plugin (`skills/<name>/` beside the
plugin's `plugin.json`) gets its static checks and review, but its
runtime cases report `not-run` in both harnesses: a plugin skill loads
through plugin installation, which the throwaway workspace does not do.

`verify` keeps each launch's raw output, so a result can be looked at
after the throwaway workspace is gone. The directory is
`diagnostics/<time>-<run id>-<random>/` in Forge's data directory (see
[Forge's Python environment](#forges-python-environment)), never inside
the repository, and `verify` prints it as `diagnostics`. Each case has
`cases/<case id>/` and Claude Code's two skill-listing launches have
`baselines/`, each with `launch.json` (command, prompt as sent,
workspace path, the hashes of the workspace's files, exit or timeout,
outcome and reason) and the `stdout.txt` and `stderr.txt` of a launch
that ran. They hold the model's output and the case prompts; Forge
never deletes them, so delete them when you are done. The first few
problems with writing them are listed as `diagnostics_problems`, their
number as `diagnostics_problem_count`; results do not depend on them.
When a skill-listing launch fails, the affected cases name which launch
failed and how (exit status, timeout, an error the harness reported, or
no listing); the harness's own message stays in the diagnostics.

## Markdown reading

Forge reads Markdown to find headings and sections, to skip code when it
checks a skill's links and placeholders, to anchor KB citations, to write
and check its own README sections and to read its earlier reports. It
parses CommonMark with
[markdown-it-py](https://github.com/executablebooks/markdown-it-py),
including fenced and indented code, HTML blocks, setext headings, list
items and block quotes.

- A heading inside a list item or block quote is no section and no KB
  anchor. GitHub anchors such headings, so a link to one fails the check
  instead of passing.
- Where markdown-it-py reads other than CommonMark 0.31.2, Forge uses
  its own rules: HTML blocks and inline HTML with ASCII tag names and
  only spaces and tabs as whitespace (markdown-it-py's Unicode patterns
  take `<scrıpt>` for `<script>` and a form feed for a space); link
  reference definitions taken from the start of their paragraph, with
  labels of at most 999 characters (a longer one is paragraph text);
  labels matched after Unicode case folding; and link destinations whose
  backslash escapes and entity references resolve as CommonMark says.
- Forge refuses blocks or inline elements nested 20 levels deep, which
  markdown-it-py skips, and a setext underline below nothing but link
  reference definitions. Checks of such a file fail and the README
  writer refuses it.
- Inside its own README sections Forge accepts only the lines it writes
  there, and it appends a section only where it shows as a heading of
  its own.

## Research isolation guidance

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

[AGPL-3.0-only](LICENSE). The files whose text Forge copies into the
files it generates in your repository (its skill templates, the code
that writes its records and README sections, and the knowledge-base
rule files in `snapshot/rules/`) are also licensed under the
[Harness Forge Generated Output Exception](LICENSE-OUTPUT-EXCEPTION),
which their notice names: what Forge copies there does not stop you
from licensing those generated files on terms you choose. Forge itself
stays AGPL-3.0-only. The knowledge-base pages in `snapshot/docs/`
quote their sources; the quoted passages remain their authors'. The
approval record is
[legal/generated-output-permission.md](legal/generated-output-permission.md);
contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

[AGENTS.md](AGENTS.md) describes the contributor rules and the test
command. `tools/live_checks.py` runs the automated live checks with your
subscription; with an API-key or unknown login, pass
`--login-answer continue` on each run to accept that it can cost extra.

**Temporary, development only:** with
`HARNESS_FORGE_TEMPORARY_CODEX_ROLLOUT=1`, a Codex explicit case of a
user-invoked skill runs without `--ephemeral`. Forge reads that run's own
session rollout in `$CODEX_HOME/sessions` (default `~/.codex/sessions`),
credits "instructions injected" when Codex recorded the candidate's
exact `SKILL.md`, and deletes the file. It reads no other session file.
This shows that the instructions reached the model, not that the
procedure ran. It is off by default; only the 2026-10-21 Codex
re-verification of the bug-intake reference used it, and it will be
removed.

## Outside controls

- `codeowners-agent-config` (part A): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - Workflow, scanner and policy files that define a gate are under code owners or come from outside the repository at a pinned ref, and where CODEOWNERS is used, CODEOWNERS owns itself (`outside.ci.workflow-protected`)
  - A CODEOWNERS entry names an owner for every file that configures the agent, including `.agents/`, `.claude/`, `CLAUDE.md`, `.mcp.json`, `.codex/`, `AGENTS.md`, `AGENTS.override.md`, `.devcontainer/`, the CI workflow files and the CODEOWNERS file itself, and the branch rule requires code-owner review (`outside.codeowners.agent-config`)
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The control is enforced on the user's plan and repository visibility, or the recommendation says it is not (`outside.gate.plan-enforced`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - Where the control requires approval, approval comes from an identity other than the author, the last pusher and the launching user, and it resets on new pushes (`outside.merge.requester-cannot-approve`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `outer-isolation` (part A): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `codeowners-agent-config` (part B): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - Workflow, scanner and policy files that define a gate are under code owners or come from outside the repository at a pinned ref, and where CODEOWNERS is used, CODEOWNERS owns itself (`outside.ci.workflow-protected`)
  - A CODEOWNERS entry names an owner for every file that configures the agent, including `.agents/`, `.claude/`, `CLAUDE.md`, `.mcp.json`, `.codex/`, `AGENTS.md`, `AGENTS.override.md`, `.devcontainer/`, the CI workflow files and the CODEOWNERS file itself, and the branch rule requires code-owner review (`outside.codeowners.agent-config`)
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The control is enforced on the user's plan and repository visibility, or the recommendation says it is not (`outside.gate.plan-enforced`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - Where the control requires approval, approval comes from an identity other than the author, the last pusher and the launching user, and it resets on new pushes (`outside.merge.requester-cannot-approve`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `outer-isolation` (part B): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `codeowners-agent-config` (part C): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - Workflow, scanner and policy files that define a gate are under code owners or come from outside the repository at a pinned ref, and where CODEOWNERS is used, CODEOWNERS owns itself (`outside.ci.workflow-protected`)
  - A CODEOWNERS entry names an owner for every file that configures the agent, including `.agents/`, `.claude/`, `CLAUDE.md`, `.mcp.json`, `.codex/`, `AGENTS.md`, `AGENTS.override.md`, `.devcontainer/`, the CI workflow files and the CODEOWNERS file itself, and the branch rule requires code-owner review (`outside.codeowners.agent-config`)
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The control is enforced on the user's plan and repository visibility, or the recommendation says it is not (`outside.gate.plan-enforced`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - Where the control requires approval, approval comes from an identity other than the author, the last pusher and the launching user, and it resets on new pushes (`outside.merge.requester-cannot-approve`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `outer-isolation` (part C): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `codeowners-agent-config` (part D): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - Workflow, scanner and policy files that define a gate are under code owners or come from outside the repository at a pinned ref, and where CODEOWNERS is used, CODEOWNERS owns itself (`outside.ci.workflow-protected`)
  - A CODEOWNERS entry names an owner for every file that configures the agent, including `.agents/`, `.claude/`, `CLAUDE.md`, `.mcp.json`, `.codex/`, `AGENTS.md`, `AGENTS.override.md`, `.devcontainer/`, the CI workflow files and the CODEOWNERS file itself, and the branch rule requires code-owner review (`outside.codeowners.agent-config`)
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The control is enforced on the user's plan and repository visibility, or the recommendation says it is not (`outside.gate.plan-enforced`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - Where the control requires approval, approval comes from an identity other than the author, the last pusher and the launching user, and it resets on new pushes (`outside.merge.requester-cannot-approve`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
- `outer-isolation` (part D): recommended, not verified. Precaution; availability available. It works as a gate only when:
  - No broader credential or channel the agent can reach, such as a personal token or a metadata endpoint, bypasses the control (`outside.gate.no-broader-bypass`)
  - The agent's identity, including the bot, app, token and the human whose token it uses, is on no bypass or override list of the control (`outside.merge.no-agent-bypass`)
  - It targets the real refs and resources it protects, such as the default and release branches and tags or the production accounts (`outside.gate.real-refs`)
  - Its configuration is checked periodically, because gate configuration drifts silently (`outside.gate.periodic-verification`)
