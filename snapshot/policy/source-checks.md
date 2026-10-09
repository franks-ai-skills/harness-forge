# Source checks for extracted rules

Before the rules in `rules/skills.yaml`, `rules/security.yaml` and
`rules/outside-controls.yaml` were retained, each primary source they
cite was fetched again on 2026-10-09 and searched for the cited
statement. All statements below were found. Rules that cite only KB
pages rest on the sources those pages cite.

| Source | Statement found |
| --- | --- |
| https://agentskills.io/specification | name "Max 64 characters", "Lowercase letters, numbers, and hyphens only", "Must match the parent directory name"; description "Max 1024 characters. Non-empty"; compatibility "Max 500 characters"; metadata "map from string keys to string values"; "under 500 lines"; references "one level deep" |
| https://opencode.ai/docs/skills/ | name "1–64 characters" and `^[a-z0-9]+(-[a-z0-9]+)*$`; description "1-1024 characters" |
| https://github.com/openai/codex/blob/main/codex-rs/ext/skills/src/loader/mod.rs | `MAX_NAME_LEN: usize = 64`, `MAX_DESCRIPTION_LEN: usize = 1024` |
| https://code.claude.com/docs/en/skills | "1,536 characters"; `disable-model-invocation`; "You don't want Claude deciding to deploy"; "Workspace trust doesn't gate this field" (`allowed-tools`); "must hold every time"; symlinked skill folders; unexpected keys fail with "a hard error"; "under 500 lines" |
| https://learn.chatgpt.com/docs/build-skills | `allow_implicit_invocation`; "Front-load"; "Keep each skill focused on one job"; `.agents/skills`; "Codex supports symlinked skill folders" |
| https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices | "Keep references one level deep from SKILL.md"; "table of contents"; "Always write in third person"; "inconsistent point-of-view can cause discovery problems"; "old patterns" |
| https://raw.githubusercontent.com/openai/skills/main/skills/.system/skill-creator/SKILL.md | "Do NOT create extraneous documentation or auxiliary files, including: - README.md … - CHANGELOG.md" |
| https://raw.githubusercontent.com/obra/superpowers/main/skills/writing-skills/SKILL.md | "Do NOT summarize the skill's process or workflow in the description" |
| https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md | "ALWAYS or NEVER" … "yellow flag"; "should not surprise the user" |
| https://agentskills.io/skill-creation/optimizing-descriptions.md | imperative phrasing, "Use this skill when" |
| https://agentskills.io/skill-creation/best-practices.md | gotchas |
| https://agentskills.io/skill-creation/using-scripts.md | no interactive prompts |
| https://agentskills.io/skill-creation/evaluating-skills.md | "Reasoning-based instructions" |
| https://owasp.github.io/www-project-agentic-skills-top-10/ast01 | "Hash-pin installed skills" |
| https://learn.microsoft.com/en-us/azure/devops/repos/git/branch-policies | "Bypass policies when pushing" |
| https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets | "someone other than the last person to push" |
| https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets | rulesets on private repositories with "Pro, GitHub Team" |
| https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners | CODEOWNERS on the "base branch" |
