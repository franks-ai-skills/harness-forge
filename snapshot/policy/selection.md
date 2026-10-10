# Selection policy

How Forge turns confirmed answers into a recommendation. The conditions
in `rules/selection.yaml` cite the KB pages they come from. The order of
the preference rules below is **Forge policy**: an engineering choice
with the rationale given here, not a claim the sources make.

## Layers

1. **Outcome.** Outcome rules apply in file order; the first match wins.
   They decide whether a harness mechanism, an outside control, both,
   reuse or nothing fits.
2. **Eligibility.** An eligibility rule rules a mechanism out when a
   confirmed answer conflicts with a "When not" condition on the
   overview or guide page it cites, for example "a rule that must hold
   every time" against an advisory mechanism. Only a cited conflict
   rules a mechanism out.
3. **Preference.** Among eligible mechanisms, preference rules apply in
   the order below. The first match is recommended; later matches are
   viable alternatives, shown with their pros and cons. An eligible
   mechanism without a matching preference rule is not preferred: it
   may work, but nothing shows an advantage for this goal.
4. **Dependencies.** Every matching control rule adds an outside control
   with its prerequisites, as a goal dependency or a precaution.

A mechanism whose eligibility or preference depends on an unanswered
fact is unresolved.

Eligibility applies to reuse too. A researched candidate whose mechanism
is ruled out is not reused, even with confirmed fit: the result names
the candidate and the requirement it conflicts with, and selection
continues as if no candidate qualified. Forge asks a question only when its answer could
change the outcome, the recommended mechanism or the controls.

If the outcome needs a harness mechanism but every matching mechanism is
ruled out, no harness mechanism fits: the outcome becomes nothing fits,
or outside control instead when an outside control is still recommended.

Whether this release can build the recommended mechanism does not affect
selection; an unbuildable recommendation stays recommended.

## Requirements and the mechanisms they rule out

Each confirmed requirement rules out every mechanism that cannot meet it
on its own. The eligibility rules cite the "When not" condition or the
mechanism description each exclusion rests on.

| Confirmed requirement | Ruled out | Why |
| --- | --- | --- |
| Must hold every time | instructions, skills, commands, subagents, automation, MCP | Each depends on a model decision or applies outside the tool call |
| Irreversible, about money, or publishes or discloses data | instructions, skills; subagents when Claude Code is a target | The model would decide on its own to start it; Claude Code delegates on a subagent's description |
| Needs its own context, other tools or another model | instructions, skills, commands | They run in the session's own context; a subagent can use a skill |
| Needs judgment, for Codex or both harnesses | hooks | Portable handlers are commands and MCP tool calls; prompt and agent hooks are Claude Code only |
| A hard limit on access that must hold every time | hooks | Tool switching and unhooked paths bypass hooks |
| A procedure the model starts | commands | Only the user starts a command |
| A procedure an event starts | instructions, skills, commands, subagents | None of them starts on an event |

A requirement that rules out no mechanism is not a gap. A hook handler,
for example, runs as its own process outside the session's context and
returns only its decision or added context, so needing its own context
does not rule out a hook (Forge policy, an inference from
`docs/overview.md#hooks`).

## Preference order and rationale

| Order | Rule | Rationale |
| --- | --- | --- |
| 1 | `selection.mechanism.early-feedback-mirror` | The user asked for earlier feedback than the outside gate gives; only a hook gives it on every relevant event. |
| 2 | `selection.mechanism.must-hold-access` | A capability that must never be used is a permission rule before anything else; permissions are the harness's hard limit. |
| 3 | `selection.mechanism.must-hold-check` | A rule that must hold every time and that a script can check is the hook's defining use; it goes before job-based preferences so a must-hold requirement decides first. |
| 4 | `selection.mechanism.judgment-hook` | On Claude Code alone, a check that needs judgment at an event is what prompt and agent hooks are for; it follows the scripted check because a script is deterministic and portable. |
| 5 | `selection.mechanism.fresh-context` | A stated need for its own context, other tools or another model is specific; it outranks the general job-based preferences. |
| 6 | `selection.mechanism.event-procedure` | A procedure an event starts runs where events are handled: a hook at that event. |
| 7 | `selection.mechanism.event-procedure-unattended` | A schedule or CI event starts an unattended run instead; offered as the alternative to a hook. |
| 8 | `selection.mechanism.guidance` | Standing guidance is what instruction files are for. |
| 9 | `selection.mechanism.user-procedure` | A procedure the user starts, or one with irreversible effects, is a user-invoked skill before a model-invoked one. |
| 10 | `selection.mechanism.procedure` | A recurring procedure needed only sometimes is a skill: cheap until used. |
| 11 | `selection.mechanism.procedure-as-instructions` | Instructions can carry a procedure too, at a cost in every session; offered as the alternative to a skill. |
| 12 | `selection.mechanism.setting` | A harness setting belongs to configuration. |
| 13 | `selection.mechanism.access-control` | Allowing, asking or refusing actions without a must-hold requirement is still a permission rule. |
| 14 | `selection.mechanism.integration-cli` | With a known CLI, a skill keeps the work inside the sandbox; it goes before MCP. |
| 15 | `selection.mechanism.integration` | Without a usable CLI, an MCP server provides the connection; with one, MCP is the alternative. |
| 16 | `selection.mechanism.check` | A check at an event without a must-hold requirement is still a hook. |
| 17 | `selection.mechanism.delegate` | Delegating a task is a subagent. |
| 18 | `selection.mechanism.bundle` | A setup for other repositories or teams is a plugin. |
| 19 | `selection.mechanism.unattended` | Running without a person is automation. |
