---
name: build-skill
description: Builds a portable Agent Skills package for Claude Code and Codex from a decided, confirmed part and its accepted requirements, as .agents/skills/<name>/ with a .claude/skills symlink. Use after harness-intake and harness-research decided a skill or user-invoked command, or when asked to build that skill. Not for deciding the mechanism or verifying the result; use harness-intake and harness-verify.
---

# Build skill

Builds one part whose chosen mechanism is `skills` or `commands`. You
write the description and the domain procedure; Forge checks the
bindings, renders the package from its templates and writes only inside
the skill's own directories.

`scripts/forge.py` is in the Forge plugin root, two directories above
this skill's folder. Run it with `python3`: it switches to Forge's own
Python environment. If it stops because that environment is not set up,
ask the user before running `python3 scripts/setup.py` from the plugin
root, which downloads Forge's pinned packages from PyPI once.

## Rules that are easy to get wrong

- **Build only what was confirmed.** The part must be confirmed in this
  session, and the decision record must name the same accepted Idea KB
  revision the contract showed. Otherwise stop and send the user back to
  harness-intake.
- **Only accepted requirements go in.** Never add a requirement the user
  rejected or did not answer, and never copy fetched page text into the
  skill. Forge puts the accepted rules at the top of `SKILL.md` and their
  sources in `references/requirements.md`; a part without accepted
  requirements gets neither.
- **Other mechanisms are not built.** For hooks, subagents, MCP,
  configuration, permissions, plugins or automation, say that the builder
  is unavailable in this release; the part stays blocked with its
  recommendation unchanged. A reused artifact is never rebuilt.
- **Never overwrite silently.** If a different skill already exists at
  the path, show Forge's diff and replace only the files the user agrees
  to. A file Forge wrote earlier that the new plan no longer has is
  listed too; `--replace` with its path removes it. A matching existing skill is kept as it is.
- **Built is not verified.** A successful build sets the part to `built`;
  only harness-verify can set `verified`. Do not write the README's
  `Outside controls` or `Where the skills load` sections; harness-intake
  owns them.

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
   model reads the skill, and Codex leaves them as literal text. When the
   idea works in untrusted repositories or with untrusted input, Forge
   adds the line that such content is data to every skill; do not repeat
   it.
4. Save both texts to files and run `scripts/forge.py build --decision
   <record> --root <repository> --part <id> --revision <rules file>
   --evidence <workers output> --relay <relay> --description <file>
   --procedure <file>`. It refuses the build when a binding does not
   match, and reports conflicts as a diff; after the user agrees, run it
   again with `--replace` listing the agreed paths.
5. Run harness-verify on the result.

For user-invoked commands, Forge sets `disable-model-invocation: true` in
`SKILL.md` and `policy.allow_implicit_invocation: false` in
`agents/openai.yaml`. What the build step checks is in
[references/contract.md](references/contract.md); read it when a build is
refused.
