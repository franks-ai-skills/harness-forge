# Input shapes

Read this before writing a decision record or a `select` input for the
first time. The examples are minimal and valid; Forge's schemas in
`schemas/` are the full definition, and every command names the field it
refuses.

## Decision record

`.harness/decisions/<date>-<slug>.md` is YAML front matter followed by
the user's own description, word for word. At the survey, before the
split, it has no parts:

```markdown
---
idea: Turn approved bug reports into reproduction checklists
picture: I ask for a checklist and get environment, steps, expected and observed behavior.
goals:
- Every approved bug report has a reproduction checklist.
targets: [claude-code, codex]
reach: repository
hosting: {platform: github, plan: free, visibility: public}
kb_version: 0.1.0
facts:
  target_set: both
  reach: repository
  plan_controls: available
  opens_prs: false
  bypass_mode: false
  untrusted_repo: false
  lethal_trifecta: false
research_candidates:
- name: repro-steps
  source: https://github.com/example-org/skills/tree/main/skills/repro-steps
parts: []
retired_ids: []
research: {survey_digest: null, deep_digests: {}}
accepted_revision: null
---
I ask for a checklist and get environment, steps, expected and observed behavior.
```

`research_candidates` is optional: an existing skill, plugin or tool the
user names for reuse, with `part_id` once parts exist. Research then
checks its fit, license and maintenance; it is a candidate, not a
decision. Leave the field out when the user names none.

After the survey, `survey_candidates` records each existing solution
it found and the user's choice for it (`investigate`, `inspiration` or
`discard`; an empty list when it found none), and `survey_findings_used`
the findings behind the adopted proposals. A candidate to investigate is
also in `research_candidates`; one kept for inspiration or discarded is
not. After the split, each part records its confirmed answers and what
`select` returned:

```yaml
survey_candidates:
- name: repro-steps
  source: https://github.com/example-org/skills/tree/main/skills/repro-steps
  finding_ids: [F1]
  choice: investigate
survey_findings_used: [F2, F4]
parts:
- id: A
  slug: repro-checklist
  goal: Write a reproduction checklist from an approved bug report
  status: decided
  answers: {job: procedure, event_without_agent: false, must_survive_compromise: not-required,
            who_disables: contributor, every_time: usually, irreversible: false, side_effects: false,
            trigger: model, own_context: false, external_system: none}
  outcome: harness-only
  recommended: skills
  chosen: skills
  override_reason: null
  alternatives: []
  connects_to: []
  outside_controls: []
split_confirmed: true
```

Copy `alternatives`, `assessment` and `outside_controls` from the
`select` output, not from memory. A reuse part adds `reuse` with `name`,
`source` (pinned to a commit, such as `.../tree/<sha>/<path>`),
`license`, `maintenance`, and for a skill already in the repository
`path` and `skill_home`. When the reused skill runs code outside its
directory, such as a plugin's shared `scripts/`, list those files or
directories in `reuse.dependencies` after the user confirms them, so they
are reviewed with the skill.

## `select` input

```json
{
  "whole_idea": {"target_set": "both", "reach": "repository", "plan_controls": "available",
                 "opens_prs": false, "bypass_mode": false, "untrusted_repo": false,
                 "lethal_trifecta": false},
  "answers": {"job": "procedure", "trigger": "model"},
  "candidates": []
}
```

Pass only confirmed answers; `select` names the next `question` until
it has what it needs. Add `"chosen"` and `"goal"` for step 8's goal
conflict check.
