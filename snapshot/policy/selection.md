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
fact is unresolved. Forge asks a question only when its answer could
change the outcome, the recommended mechanism or the controls.

If the outcome needs a harness mechanism but every matching mechanism is
ruled out, no harness mechanism fits: the outcome becomes nothing fits,
or outside control instead when an outside control is still recommended.

Whether this release can build the recommended mechanism does not affect
selection; an unbuildable recommendation stays recommended.

## Preference order and rationale

| Order | Rule | Rationale |
| --- | --- | --- |
| 1 | `selection.mechanism.early-feedback-mirror` | The user asked for earlier feedback than the outside gate gives; only a hook gives it on every relevant event. |
| 2 | `selection.mechanism.must-hold-access` | A capability that must never be used is a permission rule before anything else; permissions are the harness's hard limit. |
| 3 | `selection.mechanism.must-hold-check` | A rule that must hold every time and that a script can check is the hook's defining use; it goes before job-based preferences so a must-hold requirement decides first. |
| 4 | `selection.mechanism.fresh-context` | A stated need for its own context, other tools or another model is specific; it outranks the general job-based preferences. |
| 5 | `selection.mechanism.guidance` | Standing guidance is what instruction files are for. |
| 6 | `selection.mechanism.user-procedure` | A procedure the user starts, or one with irreversible effects, is a user-invoked skill before a model-invoked one. |
| 7 | `selection.mechanism.procedure` | A recurring procedure needed only sometimes is a skill: cheap until used. |
| 8 | `selection.mechanism.procedure-as-instructions` | Instructions can carry a procedure too, at a cost in every session; offered as the alternative to a skill. |
| 9 | `selection.mechanism.setting` | A harness setting belongs to configuration. |
| 10 | `selection.mechanism.access-control` | Allowing, asking or refusing actions without a must-hold requirement is still a permission rule. |
| 11 | `selection.mechanism.integration-cli` | With a known CLI, a skill keeps the work inside the sandbox; it goes before MCP. |
| 12 | `selection.mechanism.integration` | Without a usable CLI, an MCP server provides the connection; with one, MCP is the alternative. |
| 13 | `selection.mechanism.check` | A check at an event without a must-hold requirement is still a hook. |
| 14 | `selection.mechanism.delegate` | Delegating a task is a subagent. |
| 15 | `selection.mechanism.bundle` | A setup for other repositories or teams is a plugin. |
| 16 | `selection.mechanism.unattended` | Running without a person is automation. |
