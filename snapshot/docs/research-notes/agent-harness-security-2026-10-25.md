# Skill security: write destinations, repository metadata, embedded material and credential reads

Researched and sources fetched on 2026-10-25 for
[agent-harness-kb#11](https://github.com/franks-ai-skills/agent-harness-kb/issues/11),
after a later review of a skill built and verified with Harness Forge
found gaps in these four areas. It supplements the
[2026-10-05 security note](agent-harness-security.md), which stays as it
was. Quotes are verbatim from the fetched page.

## Write destinations

- CWE-59, Improper Link Resolution Before File Access: "The product
  attempts to access a file based on the filename, but it does not
  properly prevent that filename from identifying a link or shortcut
  that resolves to an unintended resource." Its taxonomy mappings include
  CERT C's "Canonicalize path names originating from untrusted sources"
  and "Check for the existence of links when dealing with files"
  ([CWE-59](https://cwe.mitre.org/data/definitions/59.html)).
- CWE-367, Time-of-check Time-of-use Race Condition: "The product checks
  the state of a resource before using that resource, but the resource's
  state can change between the check and the use in a way that
  invalidates the results of the check." Among its mitigations: "Recheck
  the resource after the use call to verify that the action was taken
  appropriately" ([CWE-367](https://cwe.mitre.org/data/definitions/367.html)).

**Inference:** a check of a destination before writing it is a preflight
check, not race-free containment: the path can change between the check
and the write. A recheck after writing detects an unauthorized write; it
neither prevents nor undoes it. A destination that does not exist yet is
an ordinary new file only if no link sits at that name: a dangling link
also "does not exist" to a check that follows links.

## Repository metadata

- GitHub Security Lab lists event data "that might be attacker controlled
  and should be treated as potentially untrusted input", including
  `github.event.commits.*.message`, `github.event.head_commit.message`,
  the commits' author names and emails, issue and pull request titles
  and bodies, comments and `github.head_ref`; on branch names: "The
  allowed charset for branch names is somewhat limited and branches
  cannot have spaces or colons in their names. However, command
  injection is still possible"
  ([GitHub Security Lab](https://securitylab.github.com/resources/github-actions-untrusted-input/)).
- GitHub Docs names "less obvious sources of potentially untrusted
  input, such as branch names and email addresses" and gives
  `` zzz";echo${IFS}"hello";# `` as a valid branch name
  ([script injections](https://docs.github.com/en/actions/concepts/security/script-injections)).
- The KB already cites openai/codex-action's security document for commit
  messages as an untrusted prompt source
  ([automation guide](../guide/automation.md)).

Not found in the fetched sources: tag names and file names as
attacker-controlled input. They are not claimed.

## Embedded material

- OWASP LLM01, Prompt Injection, mitigation: "Separate and clearly denote
  untrusted content to limit its influence on user prompts"; and "it is
  unclear if there are fool-proof methods of prevention for prompt
  injection" ([OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).
- OpenAI, safety in building agents: "Pass untrusted inputs through user
  messages to limit their influence"; "Structured outputs and isolation
  greatly reduce, but don't fully remove, this risk"
  ([agent builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)).
- CommonMark 0.31.2, §4.5 Fenced code blocks: the closing fence must use
  the opening fence's character, "with at least as many backticks or
  tildes as the opening code fence"; "If the end of the containing block
  (or document) is reached and no closing code fence has been found, the
  code block contains all of the lines after the opening code fence until
  the end of the containing block (or document)"
  ([CommonMark §4.5](https://spec.commonmark.org/0.31.2/#fenced-code-blocks)).

**Inference:** in Markdown, a fence longer than every run of backticks
(or tildes) in the embedded material cannot be closed by that material,
but the fence also ends where its containing list item or block quote
ends. That is syntax containment only: it does not stop a model from
following instructions in the material, which is what the separation and
labelling above mitigate, without a guarantee. A prompt written to a file
for use in another session loses the instructions of the session that
wrote it, so it has to state itself which part is untrusted material.

## Credential reads

- Claude Code's settings example shows deny rules "Read(./.env)" and
  "Read(./.env.*)" to "stop it reading `.env` files"
  ([settings](https://code.claude.com/docs/en/settings)).
- OpenCode: "`read` is `"allow"`, but `.env` files require approval by
  default", with `*.env` and `*.env.*` set to `ask` and `*.env.example`
  to `allow` ([OpenCode permissions](https://opencode.ai/docs/permissions/#defaults));
  the KB's vendor page said `deny` until this pass and was corrected
  ([KB vendor page](../vendors/opencode/permissions-and-sandbox.md));
  a Codex permission profile can deny `**/*.env`
  ([Codex permissions](../vendors/codex/permissions-and-sandbox.md)).

**Inference:** reading a credential file the task does not need puts its
contents into the session, whatever the output rules say about
persisting them. Naming such a file by path is enough when only its
existence matters. The vendors' defaults tell a secret-bearing `.env`
from an `.env.example` by name, not by a broad extension.
