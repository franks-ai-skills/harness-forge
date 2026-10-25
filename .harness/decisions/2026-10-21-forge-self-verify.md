---
idea: Verify Harness Forge's own four skills (build-skill, harness-intake, harness-research, harness-verify),
  shipped in this repository as the harness-forge plugin, against the pinned Harness KB rules and each
  skill's goal.
picture: Forge's four skills ship in this repository as the harness-forge plugin. I want each one checked
  against the pinned Harness KB rules and its goal, the way Forge checks any other skill.
goals:
- 'build-skill: Builds a portable Agent Skills package for Claude Code and Codex from a decided, confirmed
  part and its accepted requirements, as .agents/skills/<name>/ with a .claude/skills symlink. Use after
  harness-intake and harness-research decided a skill or user-invoked command, or when asked to build
  that skill. Not for deciding the mechanism or verifying the result; use harness-intake and harness-verify.'
- 'harness-intake: Turns an idea for coding-agent tooling in Claude Code or Codex into a decided, user-confirmed
  plan with one recommended mechanism or outside control per part, and records it. Use when someone wants
  to build a skill, hook, subagent, command, plugin or automation, or asks which harness mechanism fits
  a goal. Not for building or verifying files; use build-skill and harness-verify.'
- 'harness-research: Researches an idea for coding-agent tooling with web sources, keeps only findings
  whose quotes are verified and independently judged to support them, and turns accepted requirements
  into a hashed Idea KB revision. Use when harness-intake needs its survey or deep pass, or when asked
  to research an idea''s prior art, pitfalls or requirements before building. Not for questions about
  the harnesses themselves; use the knowledge base.'
- 'harness-verify: Verifies built Claude Code and Codex skills against the pinned Harness KB rules, the
  accepted requirements and each part''s confirmed goal, and writes a contract-bound report. Use after
  build-skill, after fixing a part, or when asked whether a built harness setup meets its goals. Not for
  deciding what to build or for checking platform settings such as branch protection.'
targets:
- claude-code
- codex
reach: public
hosting:
  platform: github
  plan: free
  visibility: public
kb_version: 0.6.0
facts:
  target_set: both
  reach: public
  plan_controls: available
  opens_prs: false
  bypass_mode: false
  untrusted_repo: false
  lethal_trifecta: true
research_candidates:
- name: build-skill
  source: https://github.com/franks-ai-skills/harness-forge/tree/5ff7fc0282a8c8890838ccd7a8178e3a9356977d/skills/build-skill
  part_id: A
- name: harness-intake
  source: https://github.com/franks-ai-skills/harness-forge/tree/93f5285ec649b036ce2abaaa59ec04a270410ce4/skills/harness-intake
  part_id: B
- name: harness-research
  source: https://github.com/franks-ai-skills/harness-forge/tree/8b9bb9111a81c77d8ea7fbd92346a93f1916726b/skills/harness-research
  part_id: C
- name: harness-verify
  source: https://github.com/franks-ai-skills/harness-forge/tree/93f5285ec649b036ce2abaaa59ec04a270410ce4/skills/harness-verify
  part_id: D
parts:
- id: A
  slug: build-skill
  goal: Builds a portable Agent Skills package for Claude Code and Codex from a decided, confirmed part
    and its accepted requirements, as .agents/skills/<name>/ with a .claude/skills symlink. Use after
    harness-intake and harness-research decided a skill or user-invoked command, or when asked to build
    that skill. Not for deciding the mechanism or verifying the result; use harness-intake and harness-verify.
  status: decided
  answers:
    job: procedure
    event_without_agent: false
    must_survive_compromise: not-required
    who_disables: contributor
    every_time: usually
    irreversible: false
    side_effects: false
    trigger: model
    own_context: false
    external_system: none
  outcome: reuse
  recommended: skills
  chosen: skills
  override_reason: null
  alternatives:
  - concept: instructions
    pros:
    - Always in context; cannot be missed
    cons:
    - Costs context in every session
    rules:
    - selection.mechanism.procedure-as-instructions
    why_not_chosen: the outcome reuse builds no harness mechanism
  assessment:
  - concept: instructions
    state: viable alternative
    rules:
    - selection.mechanism.procedure-as-instructions
    reasons:
    - A procedure can also live in the instruction file, as a pointer or a compressed index, when its
      omission would silently break output
    missing_facts: []
  - concept: configuration
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: permissions-and-sandbox
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: mcp
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: skills
    state: recommended
    rules: []
    reasons: []
    missing_facts: []
  - concept: commands
    state: ruled out
    rules:
    - selection.eligibility.model-started-commands
    reasons:
    - A procedure the model should start on its own cannot be a user-invoked skill
    missing_facts: []
  - concept: subagents
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: hooks
    state: unresolved
    rules:
    - selection.eligibility.judgment-hooks
    - selection.eligibility.model-handler-event-hooks
    - selection.eligibility.claude-code-event-hooks
    - selection.eligibility.codex-event-hooks
    - selection.eligibility.claude-code-prevent-hooks
    - selection.eligibility.claude-code-stop-hooks
    - selection.eligibility.claude-code-feedback-hooks
    - selection.eligibility.codex-prevent-hooks
    - selection.eligibility.codex-stop-hooks
    - selection.eligibility.codex-feedback-hooks
    - selection.eligibility.model-handler-prevent-hooks
    - selection.eligibility.model-handler-stop-hooks
    - selection.eligibility.model-handler-feedback-hooks
    reasons: []
    missing_facts:
    - config_change_source
    - directory_added_source
    - hook_effect
    - hook_event
    - own_model
    - script_checkable
    - session_start_source
    - task_completed_trigger
  - concept: plugins
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: automation
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  connects_to:
  - to: D
    how: starts
  outside_controls:
  - control: codeowners-agent-config
    prerequisites:
    - outside.ci.workflow-protected
    - outside.codeowners.agent-config
    - outside.gate.no-broader-bypass
    - outside.gate.plan-enforced
    - outside.merge.no-agent-bypass
    - outside.merge.requester-cannot-approve
    availability: available
    required_for_goal: false
  - control: outer-isolation
    prerequisites:
    - outside.gate.no-broader-bypass
    - outside.merge.no-agent-bypass
    availability: available
    required_for_goal: false
  reuse:
    name: build-skill
    source: https://github.com/franks-ai-skills/harness-forge/tree/3dd5ad78f9c9d7f146af30c10092f62a8944ef43/skills/build-skill
    license: AGPL-3.0-only
    maintenance: maintained
    path: skills/build-skill
    skill_home: plugin
    dependencies:
    - scripts/
    - src/harness_forge/
    - workers/
    - templates/
    - schemas/
    - vendor/
    - snapshot/kb-manifest.json
    - snapshot/rules/selection.yaml
    - snapshot/rules/skills.yaml
    - snapshot/rules/security.yaml
    - snapshot/rules/outside-controls.yaml
    - snapshot/checks/catalog.yaml
    - snapshot/docs/sources.md
- id: B
  slug: harness-intake
  goal: Turns an idea for coding-agent tooling in Claude Code or Codex into a decided, user-confirmed
    plan with one recommended mechanism or outside control per part, and records it. Use when someone
    wants to build a skill, hook, subagent, command, plugin or automation, or asks which harness mechanism
    fits a goal. Not for building or verifying files; use build-skill and harness-verify.
  status: decided
  answers:
    job: procedure
    event_without_agent: false
    must_survive_compromise: not-required
    who_disables: contributor
    every_time: usually
    irreversible: false
    side_effects: false
    trigger: model
    own_context: false
    external_system: none
  outcome: reuse
  recommended: skills
  chosen: skills
  override_reason: null
  alternatives:
  - concept: instructions
    pros:
    - Always in context; cannot be missed
    cons:
    - Costs context in every session
    rules:
    - selection.mechanism.procedure-as-instructions
    why_not_chosen: the outcome reuse builds no harness mechanism
  assessment:
  - concept: instructions
    state: viable alternative
    rules:
    - selection.mechanism.procedure-as-instructions
    reasons:
    - A procedure can also live in the instruction file, as a pointer or a compressed index, when its
      omission would silently break output
    missing_facts: []
  - concept: configuration
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: permissions-and-sandbox
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: mcp
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: skills
    state: recommended
    rules: []
    reasons: []
    missing_facts: []
  - concept: commands
    state: ruled out
    rules:
    - selection.eligibility.model-started-commands
    reasons:
    - A procedure the model should start on its own cannot be a user-invoked skill
    missing_facts: []
  - concept: subagents
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: hooks
    state: unresolved
    rules:
    - selection.eligibility.judgment-hooks
    - selection.eligibility.model-handler-event-hooks
    - selection.eligibility.claude-code-event-hooks
    - selection.eligibility.codex-event-hooks
    - selection.eligibility.claude-code-prevent-hooks
    - selection.eligibility.claude-code-stop-hooks
    - selection.eligibility.claude-code-feedback-hooks
    - selection.eligibility.codex-prevent-hooks
    - selection.eligibility.codex-stop-hooks
    - selection.eligibility.codex-feedback-hooks
    - selection.eligibility.model-handler-prevent-hooks
    - selection.eligibility.model-handler-stop-hooks
    - selection.eligibility.model-handler-feedback-hooks
    reasons: []
    missing_facts:
    - config_change_source
    - directory_added_source
    - hook_effect
    - hook_event
    - own_model
    - script_checkable
    - session_start_source
    - task_completed_trigger
  - concept: plugins
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: automation
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  connects_to:
  - to: C
    how: starts
  outside_controls:
  - control: codeowners-agent-config
    prerequisites:
    - outside.ci.workflow-protected
    - outside.codeowners.agent-config
    - outside.gate.no-broader-bypass
    - outside.gate.plan-enforced
    - outside.merge.no-agent-bypass
    - outside.merge.requester-cannot-approve
    availability: available
    required_for_goal: false
  - control: outer-isolation
    prerequisites:
    - outside.gate.no-broader-bypass
    - outside.merge.no-agent-bypass
    availability: available
    required_for_goal: false
  reuse:
    name: harness-intake
    source: https://github.com/franks-ai-skills/harness-forge/tree/3dd5ad78f9c9d7f146af30c10092f62a8944ef43/skills/harness-intake
    license: AGPL-3.0-only
    maintenance: maintained
    path: skills/harness-intake
    skill_home: plugin
    dependencies:
    - scripts/
    - src/harness_forge/
    - workers/
    - templates/
    - schemas/
    - vendor/
    - snapshot/kb-manifest.json
    - snapshot/rules/selection.yaml
    - snapshot/rules/skills.yaml
    - snapshot/rules/security.yaml
    - snapshot/rules/outside-controls.yaml
    - snapshot/checks/catalog.yaml
    - snapshot/docs/sources.md
- id: C
  slug: harness-research
  goal: Researches an idea for coding-agent tooling with web sources, keeps only findings whose quotes
    are verified and independently judged to support them, and turns accepted requirements into a hashed
    Idea KB revision. Use when harness-intake needs its survey or deep pass, or when asked to research
    an idea's prior art, pitfalls or requirements before building. Not for questions about the harnesses
    themselves; use the knowledge base.
  status: decided
  answers:
    job: procedure
    event_without_agent: false
    must_survive_compromise: not-required
    who_disables: contributor
    every_time: usually
    irreversible: false
    side_effects: false
    trigger: model
    own_context: false
    external_system: cli
  outcome: reuse
  recommended: skills
  chosen: skills
  override_reason: null
  alternatives:
  - concept: instructions
    pros:
    - Always in context; cannot be missed
    cons:
    - Costs context in every session
    rules:
    - selection.mechanism.procedure-as-instructions
    why_not_chosen: the outcome reuse builds no harness mechanism
  assessment:
  - concept: instructions
    state: viable alternative
    rules:
    - selection.mechanism.procedure-as-instructions
    reasons:
    - A procedure can also live in the instruction file, as a pointer or a compressed index, when its
      omission would silently break output
    missing_facts: []
  - concept: configuration
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: permissions-and-sandbox
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: mcp
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: skills
    state: recommended
    rules: []
    reasons: []
    missing_facts: []
  - concept: commands
    state: ruled out
    rules:
    - selection.eligibility.model-started-commands
    reasons:
    - A procedure the model should start on its own cannot be a user-invoked skill
    missing_facts: []
  - concept: subagents
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: hooks
    state: unresolved
    rules:
    - selection.eligibility.judgment-hooks
    - selection.eligibility.model-handler-event-hooks
    - selection.eligibility.claude-code-event-hooks
    - selection.eligibility.codex-event-hooks
    - selection.eligibility.claude-code-prevent-hooks
    - selection.eligibility.claude-code-stop-hooks
    - selection.eligibility.claude-code-feedback-hooks
    - selection.eligibility.codex-prevent-hooks
    - selection.eligibility.codex-stop-hooks
    - selection.eligibility.codex-feedback-hooks
    - selection.eligibility.model-handler-prevent-hooks
    - selection.eligibility.model-handler-stop-hooks
    - selection.eligibility.model-handler-feedback-hooks
    reasons: []
    missing_facts:
    - config_change_source
    - directory_added_source
    - hook_effect
    - hook_event
    - own_model
    - script_checkable
    - session_start_source
    - task_completed_trigger
  - concept: plugins
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: automation
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  connects_to: []
  outside_controls:
  - control: codeowners-agent-config
    prerequisites:
    - outside.ci.workflow-protected
    - outside.codeowners.agent-config
    - outside.gate.no-broader-bypass
    - outside.gate.plan-enforced
    - outside.merge.no-agent-bypass
    - outside.merge.requester-cannot-approve
    availability: available
    required_for_goal: false
  - control: outer-isolation
    prerequisites:
    - outside.gate.no-broader-bypass
    - outside.merge.no-agent-bypass
    availability: available
    required_for_goal: false
  reuse:
    name: harness-research
    source: https://github.com/franks-ai-skills/harness-forge/tree/3dd5ad78f9c9d7f146af30c10092f62a8944ef43/skills/harness-research
    license: AGPL-3.0-only
    maintenance: maintained
    path: skills/harness-research
    skill_home: plugin
    dependencies:
    - scripts/
    - src/harness_forge/
    - workers/
    - templates/
    - schemas/
    - vendor/
    - snapshot/kb-manifest.json
    - snapshot/rules/selection.yaml
    - snapshot/rules/skills.yaml
    - snapshot/rules/security.yaml
    - snapshot/rules/outside-controls.yaml
    - snapshot/checks/catalog.yaml
    - snapshot/docs/sources.md
- id: D
  slug: harness-verify
  goal: Verifies built Claude Code and Codex skills against the pinned Harness KB rules, the accepted
    requirements and each part's confirmed goal, and writes a contract-bound report. Use after build-skill,
    after fixing a part, or when asked whether a built harness setup meets its goals. Not for deciding
    what to build or for checking platform settings such as branch protection.
  status: decided
  answers:
    job: procedure
    event_without_agent: false
    must_survive_compromise: not-required
    who_disables: contributor
    every_time: usually
    irreversible: false
    side_effects: false
    trigger: model
    own_context: false
    external_system: none
  outcome: reuse
  recommended: skills
  chosen: skills
  override_reason: null
  alternatives:
  - concept: instructions
    pros:
    - Always in context; cannot be missed
    cons:
    - Costs context in every session
    rules:
    - selection.mechanism.procedure-as-instructions
    why_not_chosen: the outcome reuse builds no harness mechanism
  assessment:
  - concept: instructions
    state: viable alternative
    rules:
    - selection.mechanism.procedure-as-instructions
    reasons:
    - A procedure can also live in the instruction file, as a pointer or a compressed index, when its
      omission would silently break output
    missing_facts: []
  - concept: configuration
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: permissions-and-sandbox
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: mcp
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: skills
    state: recommended
    rules: []
    reasons: []
    missing_facts: []
  - concept: commands
    state: ruled out
    rules:
    - selection.eligibility.model-started-commands
    reasons:
    - A procedure the model should start on its own cannot be a user-invoked skill
    missing_facts: []
  - concept: subagents
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: hooks
    state: unresolved
    rules:
    - selection.eligibility.judgment-hooks
    - selection.eligibility.model-handler-event-hooks
    - selection.eligibility.claude-code-event-hooks
    - selection.eligibility.codex-event-hooks
    - selection.eligibility.claude-code-prevent-hooks
    - selection.eligibility.claude-code-stop-hooks
    - selection.eligibility.claude-code-feedback-hooks
    - selection.eligibility.codex-prevent-hooks
    - selection.eligibility.codex-stop-hooks
    - selection.eligibility.codex-feedback-hooks
    - selection.eligibility.model-handler-prevent-hooks
    - selection.eligibility.model-handler-stop-hooks
    - selection.eligibility.model-handler-feedback-hooks
    reasons: []
    missing_facts:
    - config_change_source
    - directory_added_source
    - hook_effect
    - hook_event
    - own_model
    - script_checkable
    - session_start_source
    - task_completed_trigger
  - concept: plugins
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  - concept: automation
    state: not preferred
    rules: []
    reasons:
    - no preference rule shows an advantage for this goal
    missing_facts: []
  connects_to:
  - to: B
    how: uses
  outside_controls:
  - control: codeowners-agent-config
    prerequisites:
    - outside.ci.workflow-protected
    - outside.codeowners.agent-config
    - outside.gate.no-broader-bypass
    - outside.gate.plan-enforced
    - outside.merge.no-agent-bypass
    - outside.merge.requester-cannot-approve
    availability: available
    required_for_goal: false
  - control: outer-isolation
    prerequisites:
    - outside.gate.no-broader-bypass
    - outside.merge.no-agent-bypass
    availability: available
    required_for_goal: false
  reuse:
    name: harness-verify
    source: https://github.com/franks-ai-skills/harness-forge/tree/3dd5ad78f9c9d7f146af30c10092f62a8944ef43/skills/harness-verify
    license: AGPL-3.0-only
    maintenance: maintained
    path: skills/harness-verify
    skill_home: plugin
    dependencies:
    - scripts/
    - src/harness_forge/
    - workers/
    - templates/
    - schemas/
    - vendor/
    - snapshot/kb-manifest.json
    - snapshot/rules/selection.yaml
    - snapshot/rules/skills.yaml
    - snapshot/rules/security.yaml
    - snapshot/rules/outside-controls.yaml
    - snapshot/checks/catalog.yaml
    - snapshot/docs/sources.md
split_confirmed: true
retired_ids: []
research:
  survey_digest: null
  deep_digests: {}
accepted_revision:
  digest: d57c0a8922498e9adeb1d67a1ca1cc971c50bab9b63365dc64d13ae032ce4a1e
  rule_ids:
  - idea.forge-self-verify.a.no-silent-overwrite
  - idea.forge-self-verify.a.only-accepted-requirements
  - idea.forge-self-verify.a.records-are-data
  - idea.forge-self-verify.a.built-not-verified
  - idea.forge-self-verify.a.scripts-only-when-needed
  - idea.forge-self-verify.b.no-own-pick
  - idea.forge-self-verify.b.proposed-until-confirmed
  - idea.forge-self-verify.b.untrusted-separate
  - idea.forge-self-verify.b.one-question
  - idea.forge-self-verify.b.no-leading-questions
  - idea.forge-self-verify.d.not-run-not-pass
  - idea.forge-self-verify.d.codex-best-effort
  - idea.forge-self-verify.d.independent-judge
  - idea.forge-self-verify.d.checked-files-are-data
  - idea.forge-self-verify.d.user-settles-not-applicable
  - idea.forge-self-verify.d.scripts-before-judgment
  - idea.forge-self-verify.c.page-text-not-instructions
  - idea.forge-self-verify.c.untrusted-marked
  - idea.forge-self-verify.c.user-files-issues
  - idea.forge-self-verify.c.citation-precision
  - idea.forge-self-verify.c.separate-judgment
  - idea.forge-self-verify.c.brief-bound
  - idea.forge-self-verify.c.user-answers-requirements
  - idea.forge-self-verify.c.validated-format
survey_findings_used:
- F5
- F7
---
Forge's four skills ship in this repository as the harness-forge plugin. I want each one checked against the pinned Harness KB rules and its goal, the way Forge checks any other skill.
