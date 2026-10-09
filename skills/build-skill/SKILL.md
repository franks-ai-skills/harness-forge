---
name: build-skill
description: Builds a portable Agent Skills package for Claude Code and Codex from a decided, confirmed part and its accepted requirements, as .agents/skills/<name>/ with a .claude/skills symlink. Use after harness-intake and harness-research decided a skill or user-invoked command, or when asked to build that skill. Not for deciding the mechanism or verifying the result; use harness-intake and harness-verify.
---

# Build skill

Builds one part whose chosen mechanism is `skills` or `commands`. You
write the description and the domain procedure; Forge checks the
bindings, renders the package from its templates and writes only inside
the skill's own directories.

## Rules that are easy to get wrong

- **Build only what was confirmed.** The part must be confirmed in this
  session, and the decision record must name the same accepted Idea KB
  revision the contract showed. Otherwise stop and send the user back to
  harness-intake.
- **Only accepted requirements go in.** Never add a requirement the user
  rejected or did not answer, and never copy fetched page text into the
  skill. Forge puts the accepted rules at the top of `SKILL.md` and their
  sources in `references/requirements.md`.
- **Other mechanisms are not built.** For hooks, subagents, MCP,
  configuration, permissions, plugins or automation, say that the builder
  is unavailable in this release; the part stays blocked with its
  recommendation unchanged. A reused artifact is never rebuilt.
- **Never overwrite silently.** If a different skill already exists at
  the path, show Forge's diff and replace only the files the user agrees
  to. A matching existing skill is kept as it is.
- **Built is not verified.** A successful build sets the part to `built`;
  only harness-verify can set `verified`. Do not write the README's
  `Outside controls` section; harness-intake owns it.

## Procedure

1. Read the part's goal, its accepted requirements and the deep-pass
   findings for the part in `.harness/research/`.
2. Write the description: what the skill does with concrete verbs, then
   "Use when …" with phrases a user would say, then what it is not for.
   One line, key words first, about 500 characters or less, no workflow
   steps.
3. Write the procedure the agent follows at run time: only what the model
   would otherwise get wrong, as numbered steps, taking inputs from the
   user's message. Use no argument placeholders, skill-directory variables
   or shell-injection syntax: only Claude Code processes them, before the
   model reads the skill, and Codex leaves them as literal text.
4. Hand both texts to Forge's build step for the part. It refuses the
   build when a binding does not match, and reports conflicts as a diff.
5. Run harness-verify on the result.

For user-invoked commands, Forge sets `disable-model-invocation: true` in
`SKILL.md` and `policy.allow_implicit_invocation: false` in
`agents/openai.yaml`. What the build step checks is in
[references/contract.md](references/contract.md); read it when a build is
refused.
