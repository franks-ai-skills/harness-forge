# Results and their precedence

Read this before explaining a verification result.

## Harness result

| Result | When |
| --- | --- |
| `verified` | Confirmed in this session; every applicable rule has a result; no error rule failed, errored or stayed unresolved; the links between parts hold; the recorded selection is current and no goal conflict remains, a kept one included; no security or compatibility drift is open; the goal verdict is met; executed runtime tests passed; every declared behavior case passed in this harness; for a skill Forge built, its explicit activation was observed live (a reused skill available locally needs no observed activation: its checks, review and behavior cases decide); no bound input changed. With adopted overrides the report says "verified with N overrides" and names them |
| `not verified` | Something was built or vendored, and at least one condition above fails |
| `not applicable` | Nothing is built: an outside-only or nothing-fits part, or a mechanism without a builder in this release |
| `not checked` | A reused artifact that is not available locally |

The review worker's verdicts, including whether the procedure is
consistent with its own requirements and allowlists, are a static
assessment of the built files: they do not demonstrate live behavior or
certify isolation, and `verified` does not mean every inconsistency was
found. Runtime results are reported separately.

## Behavior tests

A behavior case checks what a built skill does once it runs. Forge runs
it in a throwaway workspace with only the frozen skill, the case's
fixtures and the dependencies it lists, never a copy of the repository,
and the harness lets the skill write only the declared paths while it
runs: Claude Code through `Edit` rules in `dontAsk` mode, with reads
blocked outside the workspace and no shell (Claude Code 2.1.257 or later;
older or unknown versions make the case unavailable); Codex through
Forge's permission profile, which reads only the workspace, writes only
the declared paths and has no network. Codex's sandbox grants a whole
subtree for a directory or a path that does not exist yet, and a command
can turn a granted file into a directory, so in Codex a case with an
exact write is unavailable: declare `dir/**` there, or test exact paths
in Claude Code. Anything changed outside the
declared writes fails the case; the comparison of the workspace before
and after the run is further evidence, since a file written and removed
again leaves no trace in it.

A case passes only when the skill was observed to activate, as an
activation case sees it, and every assertion held: files the fixtures
already held prove nothing alone. `no_denied` counts the refusals Claude
Code reports; Codex does not report refused file operations inside its
commands, so a case asking for it there is unavailable. A command check
sees the recorded command text, not what a script it starts does.

`no_commands_matching` searches each recorded command's whole text with
its regular expression: arguments, quoted strings and here-document
bodies included, as the harness recorded it (in Codex the full
`/bin/zsh -lc '…'` script). A pattern for reading a file must therefore
not also match writing text that names it, as in `cat > draft.md
<<'EOF'` followed by a line that mentions `.env`. Anchor the reading
command at the start of a command and the path as its argument, for
example `(^|[;&|\n'"(]\s*)(cat|head|tail|less|more|strings|od|xxd|bat|nl|source)((\s+-\S+)*)\s+([^\s<>|;&'"]*/)?\.env(?![\w.])`,
which matches `cat .env`, `head -n1 ./.env` and `zsh -lc 'cat .env'` but
not that here-document, `cat .env.example` or `grep KEY .env`. Such a
pattern is a heuristic, not a shell parser: it misses readers that take
other arguments first (`grep`, `sed`, `awk`), quoting, variables,
substitutions and other command forms, and it still matches a
here-document line that itself starts with such a command. It cannot
show that a file was not read: keep `no_output_containing` (the file's
content in no recorded output) and, in Claude Code,
`no_file_requests_matching` where they apply.

A Claude Code case never gets a shell. Asking for one with `shell: true`
makes the case unavailable: Claude Code's sandbox documentation does not
establish that writes inside the working directory can be limited to the
declared paths, so a shell there would widen the case's write boundary.
A case that needs a shell runs in Codex.

`only_in` requires each text to occur in the saved file, every occurrence
wholly inside the named section (from its heading to the next heading of
the same or a higher level) and, with `fenced`, inside one fenced block's
content there; a missing or repeated section fails it.
`no_output_containing` fails when a text appears in any recorded tool
result or command output, and also when the record is incomplete or an
output cannot be read, since absence cannot be shown then; its absence
does not prove a file was never read. `no_file_requests_matching` checks
the paths of Claude Code's file-tool requests (`Read`, `Edit` and `Write`
by `file_path`, `Grep` by `path`, `Glob` by `path` and `pattern`),
refused ones too; Codex records no file-tool reads, so a case asking for
it there is unavailable.

Each declared case needs exactly one result in each harness it names. A
failed case keeps the part from `verified`; an unavailable or erroring
one does too, as unverified behavior, which is no claim that the build is
wrong. Results are bound to the case, its fixtures and the bytes of its
dependencies: changing any of them means testing again.

`codex exec --json` shows no record when Codex injects a user-invoked
skill named with `$name`. For such a skill's explicit and behavior cases
in Codex, Forge reads that run's own session file and deletes it: the
rollout observer, a workaround tracked by FORGE #18. When it cannot
tell which file is the run's, the case's reason says a file may remain,
and the case is an error unless it already failed for another reason. The report names
each result whose activation came from it; it shows that the
instructions reached the model, not that the procedure ran. When
neither the stream nor the session file shows the activation, the
explicit case is not run and a behavior case is unavailable: unobserved,
which is no claim that the build is wrong.

## Goal result, decided in this order

1. `not assessed`: the configuration summary is unconfirmed, the part is blocked, the
   harness result is `not checked`, or a bound input of a part without a
   build (its goal, its README hand-off) changed after the run was
   prepared.
2. `not met`: the harness result is `not verified`, or the confirmed
   outcome is nothing fits. A failed build is a failure, never a
   dependency.
3. `depends on outside controls`: the goal needs at least one outside
   control (listed by name), and the harness result is `verified` or
   `not applicable`. Forge never checks platform settings.
4. `met by the verified build`: verified, and the goal needs no outside
   control. Precautionary controls do not count as goal dependencies.

## Bound inputs

The result is bound to the sha256 of every checked file and link, the
Harness KB version and rules, overrides, the accepted Idea KB revision,
each part's record, the checker versions and the worker evidence. A
change to any of them returns the affected parts to `built`; a shared
file such as the README affects every part that owns it.

## Provenance

The report names the Harness Forge copy that verified (its declared
version and, for a Git checkout of Forge itself, the commit and whether
it had uncommitted changes) and, per part, the copy that last built it
successfully, as the build recorded it in the decision record. Any value
that could not be established is `unknown`. "Built outputs" compares the
checked files and links with the ones that build left; a hand edit since
then shows as changed. It is information only and changes no result.

## Not applicable from the reviewer

Allowed only for rules whose applicability needs judgment. For an error
rule it stays unresolved until the user confirms the reason in this
session; that confirmation settles only whether the rule applies.

## Re-verification scope

Forge chooses the scope itself and asks nothing. A Review run sends the
reviewer only its scope: the fixed parts, the parts linked to them,
every part whose bound inputs or confirmation changed and every part
whose earlier result or runtime evidence cannot be kept. It keeps
results only from the report Forge recorded writing for this repository
(outside it, in Forge's own data directory), unchanged; an edited,
foreign or unrecorded report, or `--scope extensive`, judges every part,
and the report's "Review scope" line says which and why. Every
part's script rules and link checks run again in every run. A part
outside the scope keeps its earlier judged results and goal verdict,
each marked "kept from attempt A of run X" (or "kept from run X" from a
report made before attempts were recorded), with its runtime evidence
marked the same way, and stays verified only if it was
verified then and its scripts pass now. Runtime cases and fixtures are
not shown to the reviewer: when only they changed, or a part's earlier
runtime evidence no longer holds, a part verified last time keeps its
judgments and its runtime cases run again, and it stays verified only if
the obligations of a fresh run hold now: an explicit activation passed
and every behavior case for this harness passed; a part not verified
last time is judged again in full.
