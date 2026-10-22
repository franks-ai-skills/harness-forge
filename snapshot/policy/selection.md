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
4. **Variant.** A variant rule names the selected mechanism's form, such
   as a hook's handler. The first variant rule that holds applies, on
   every route that offers the mechanism: built, reused or shown as an
   alternative. An alternative whose variant is still open names the
   facts that would decide it; Forge asks them only if the user considers
   that alternative. A reused candidate is described by its own evidence,
   never by the pros and cons of a rule for building a mechanism, and its
   recorded form must be one the variant rule accepts.
5. **Dependencies.** Every matching control rule adds an outside control
   with its prerequisites, as a goal dependency or a precaution.

A mechanism whose eligibility or preference depends on an unanswered
fact is unresolved.

Eligibility applies to reuse too. A researched candidate whose mechanism
is ruled out, or whose recorded form (`variant`, such as a hook's
handler) is missing or not accepted by its variant rule, is not reused,
even with confirmed fit: the result names the candidate and the
requirement it conflicts with, and selection
continues as if no candidate qualified. Forge asks a question only when its answer could
change the outcome, the recommended mechanism, its variant or the controls.

If the outcome needs a harness mechanism but every matching mechanism is
ruled out, no supported standalone mechanism fits: the outcome becomes
nothing fits, or outside control instead when an outside control is
still recommended, and the result says that combinations of mechanisms
were not assessed. A combination, such as a skill whose irreversible
action an ask rule guards, may still fit; it is not ruled out.

Whether this release can build the recommended mechanism does not affect
selection; an unbuildable recommendation stays recommended.

## Requirements and the mechanisms they rule out

Each confirmed requirement rules out every mechanism that cannot meet it
on its own. The eligibility rules cite the "When not" condition or the
mechanism description each exclusion rests on.

| Confirmed requirement | Ruled out | Why |
| --- | --- | --- |
| Must hold every time | instructions, skills, commands, subagents, automation, MCP | Each depends on a model decision or applies outside the tool call |
| Irreversible, about money, or publishes or discloses data | instructions, skills, subagents | The model would decide on its own to start it; delegation starts on the model's decision or on instructions or a skill it follows, in both harnesses |
| Changes external state, such as a commit, a push, a deploy, an issue edit or a message, or needs timing the model should not choose | instructions, skills, subagents | The same: the model would decide on its own when to start it. Unlike an irreversible action, a reversible change needs no outside gate |
| Needs its own context, other tools or another model | instructions, skills, commands | They run in the session's own context; a subagent can use a skill |
| Needs judgment, or a model of its own, for Codex or both harnesses | hooks | Portable handlers are commands and MCP tool calls; prompt and agent hooks are Claude Code only |
| Needs judgment or a model of its own, at an event where a prompt or agent verdict has no effect | hooks | Such a verdict takes effect only at eleven events; elsewhere the handler does not run or, at `PermissionRequest` and `PermissionDenied`, its `ok: false` has no effect |
| An event a target harness does not have | hooks | Claude Code has 33 events, Codex 12; eleven are shared, and `Interrupt` is Codex only |
| Its result must prevent the event's action, at an event where a target harness cannot | hooks | After a tool call the call has run; a `policy_settings` change cannot be blocked |
| Its result must stop the agent's further work, at an event where a target harness is not documented to | hooks | Codex stops after `PostCompact` but documents a stop at `SessionStart` only after compaction; Claude Code discards `continue` at `PostCompact` |
| Its result must reach the agent, at an event where a target harness does not deliver it | hooks | Per harness and event, for example `Notification` or `PreCompact`; at `DirectoryAdded` only for `/add-dir` |
| Needs judgment or a model of its own, and must prevent, stop or reach the agent where a prompt or agent verdict cannot | hooks | At `PostToolUseFailure` and `TaskCreated` the verdict only feeds back; at `PostToolBatch` it only ends the turn |
| A hard limit on access that must hold every time | hooks | Tool switching and unhooked paths bypass hooks |
| A procedure the model starts | commands | Only the user starts a command |
| A procedure an event starts | instructions, skills, commands, subagents | None of them starts on an event |

Needing its own context covers three capabilities: its own context
window, another model, or only other tools. When it is needed and a hook
could be chosen, Forge asks which: a command handler gives a process with
its own tools, but no model; only Claude Code's prompt and agent handlers
give a model of its own. The variant rules name the hook's handler from
these answers and from the event and effect, including every setting
that changes what the hook does: an async command hook cannot prevent,
stop or feed back in time, and at a tool call or a teammate a prompt hook
stops the work only without `continueOnBlock`, while feedback and keeping
a teammate working need `continueOnBlock` or an agent hook. A reused
hook records its form in these terms (synchronous or async command
handler, prompt handler with or without `continueOnBlock`, agent
handler), and Forge asks whatever decides the handler, whether the hook
is built or reused.

For a hook, Forge asks the event by name, and what the hook's result
must do there: prevent the action the event is about, stop the agent's
further work, reach the agent as feedback, or only report through the
hook's own side effects, such as a log, a file or a notification.
Feedback must arrive before the agent's next step. An async command
hook's output arrives later, at the next model request in Codex and on
the next conversation turn in Claude Code (after the next user
interaction if the session is idle), so an async hook serves only
report.
Preventing and stopping are separate answers: a hook after a tool call
can stop the agent but cannot undo the call. That a step must run every
time does not mean it must prevent or stop anything: a hook runs at every
occurrence of its event either way. Where a capability holds only for
one source of the event, Forge asks that source: a Codex `SessionStart`
after compaction, a `ConfigChange` outside managed policy settings, a
`TaskCompleted` from a teammate finishing its turn, a `DirectoryAdded`
from `/add-dir`. A capability the vendor does not document counts as
missing, and the reason says it is not documented. Each event's capabilities are recorded per harness, in
`docs/vendors/claude-code/hooks.md` and `docs/vendors/codex/hooks.md`,
never inferred from a grouping or from the other harness; for both
harnesses the event must allow the effect in each.

## Preference order and rationale

| Order | Rule | Rationale |
| --- | --- | --- |
| 1 | `selection.mechanism.early-feedback-mirror` | The user asked for earlier feedback than the outside gate gives; only a hook gives it on every relevant event. |
| 2 | `selection.mechanism.must-hold-access` | A capability that must never be used is a permission rule before anything else; permissions are the harness's hard limit. |
| 3 | `selection.mechanism.judgment-hook` | On Claude Code alone, a check that needs judgment or a model of its own at an event is what prompt and agent hooks are for. It goes before the scripted check, which matches the same check whenever a script can decide it but would give no model. |
| 4 | `selection.mechanism.must-hold-check` | A rule that must hold every time and that a script can check is the hook's defining use; it goes before job-based preferences so a must-hold requirement decides first. |
| 5 | `selection.mechanism.fresh-context` | A stated need for its own context, other tools or another model is specific; it outranks the general job-based preferences. |
| 6 | `selection.mechanism.event-procedure` | A procedure an event starts runs where events are handled: a hook at that event. |
| 7 | `selection.mechanism.event-procedure-unattended` | A schedule or CI event starts an unattended run instead; offered as the alternative to a hook. |
| 8 | `selection.mechanism.guidance` | Standing guidance is what instruction files are for. |
| 9 | `selection.mechanism.user-procedure` | A procedure the user starts, or one with irreversible effects, other side effects or timing the model should not choose, is a user-invoked skill before a model-invoked one. |
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
