# Changelog

The version is `version` in `.claude-plugin/plugin.json`, `plugin.json`,
`.claude-plugin/marketplace.json` and `pyproject.toml`, which
`tests/test_packaging.py` keeps equal. Claude Code keeps an installed
plugin on its cached copy until that version changes, and Codex keys
its plugin cache by it, so every release raises it. A release is the
merge commit tagged `v<version>` with git, with a GitHub release whose
notes are its section here. Release tags are never moved. A Claude
Code plugin that depends on Forge with a version range resolves it
against tags named `harness-forge--v<version>` (`claude plugin tag`
creates that format); Forge adds such a tag on the same commit only
once it supports such dependencies.

## Unreleased

### Confirmation

- A summary's details link shows the file's path relative to the
  repository as its text, for the user to copy in one piece; its
  destination stays the absolute path. The guidance says to show the
  summary in full and unchanged as visible reply text before its
  question: a question tool's preview or option text alone does not
  count, and the details line is never shortened or left out (#132).
- Each summary starts with what confirming it decides and what follows:
  for a research proposal by stage (the survey, the deep pass where it
  is due and acceptance, acceptance alone, or more intake), for a
  configuration proposal the build,
  and for a configuration summary the verification. The question names
  what is confirmed instead of saying it is "above" (#134).

### Requirement acceptance

- `compare-requirements` returns, with each item's display, the question
  to ask right after it and its options, each naming what it leads to:
  a decision for the acceptance relay, or another step first (an edit
  shown back with its own question, a re-check, a new deep pass, keeping
  a removed requirement, or one at a time). The unchanged group gets a
  question when it holds two or more; compact entries and edits carry
  theirs. harness-research shows the display as visible reply text
  immediately before asking the returned question, and harness-intake a
  proposed goal's full text before asking about it: a question tool's
  preview or option text alone does not count (#133).

## 0.5.0 (2026-10-28)

Still pins agent-harness-kb 0.6.0. From the owner's prompt-me test of
0.4.0: confirmation summaries stay within a hard size limit and say what
changed since the last confirmation, requirement acceptance is bound to
what the user was shown, build replaces its own or committed output
without asking, and verification chooses its scope, names each attempt,
keeps file judgments when only runtime cases change, reports changed
verdicts and runtime evidence per target harness. Behavior cases gain
git and link fixtures and new assertions. Breaking: `accept` needs
`--relay`; `verify --scope review` and `--scope-relay` are gone (Forge
chooses the scope; `--scope extensive` remains); `prepare-contract`
returns a bounded summary with its own `question`; a Claude Code
behavior case cannot have a shell.

### Verification

- The verification report keeps the text it embeds from review workers,
  runs and the repository contained and says so before the first of it:
  every table cell of such text escapes the characters that start inline
  Markdown, so a `|` cannot end the cell and code, emphasis, Markdown
  links, HTML and entities render as written (GitHub still links a bare
  URL, with its text unchanged); the answers' code spans use a backtick
  run longer than any inside them. Before, the runtime-evidence note,
  observed activations and the reasons of cases not run were not escaped
  (#129).
- harness-verify's results reference says that only a skill Forge built
  needs an observed explicit activation to be `verified`; a reused skill
  available locally is judged by its checks, review and behavior cases.
  Forge's own self-verification now states build-skill's overwrite
  requirement as #101 decided: no question for a file that is the
  part's last build output or the committed version, and every replaced
  or removed file named (#127).
- A Claude Code behavior case can ask for a shell with `shell: true`,
  but it is unavailable, with the reason, and launches nothing: Claude
  Code's sandbox documentation does not establish that writes inside the
  working directory can be limited to the declared paths, so Forge never
  runs such a case with a wider write boundary. `shell` applies only to
  a case for Claude Code alone; a case that needs a shell runs in
  Codex (#113).
- Behavior cases gain three assertions (#112): `only_in`, that a text
  occurs only inside a named Markdown section and optionally inside a
  fenced block there, as CommonMark parses it; `no_output_containing`,
  that no recorded tool result or command output contains a text, which
  an incomplete or unreadable record also fails; and
  `no_file_requests_matching`, that no Claude Code file-tool request,
  refused ones included, names a matching path (unavailable in Codex).
  The report's gaps say that absence from recorded output does not
  prove a file was never read.
- A behavior case can set up a git history and symbolic links before
  it runs (#111). `git` makes its commits one at a time in a repository
  built from nothing (no caller configuration, template or hook; a fixed
  identity and time, so commit ids repeat); text fixtures stay
  untracked. `links` creates links to a file, a directory or nothing,
  inside the workspace or in an `outside/` directory beside it. Any
  change outside the workspace fails the case, whatever it declares to
  write; `.git` is never writable with a git fixture, and `.git/index`
  is left out of the comparison (named as a gap). A Claude Code case
  still has no shell, so it cannot run `git` itself.
- A behavior case names what it checks in `checks`, its part's accepted
  requirement ids and/or `goal`; `scope` and `verify` refuse any other
  id, and the report's Behavior tests show it ("names no requirement"
  for a case file from before). harness-verify derives each assertion
  from what the case checks, demands no more than that, and gives each
  case a request and fixtures from which the assertion follows (#110).
- The report lists runtime evidence for every target harness: per built
  part, the declared activation and behavior cases and how many passed,
  failed, errored, were unavailable or did not run, a declared case of a
  harness the verification did not run from as not run with the reason,
  and "no cases declared" apart from that. A verified part says which
  harness its runtime evidence covers, and `verify` returns
  `runtime_evidence` and `unrun_harnesses` (#103).
- The report and `verify`'s output name each reviewer verdict that
  changed although the part's review inputs did not, compared with the
  report Forge recorded writing: rule, earlier and current verdict and
  the attempts that made them. Only pass and fail, and a met or not met
  goal, are compared; the current verdict counts as usual (#108).
- A change confined to a part's runtime cases or fixtures no longer
  re-judges its files: a part verified last time keeps its file
  judgments and its runtime cases run again, and it stays verified only
  if a fresh run's obligations hold now: an explicit activation passed
  and every behavior case for this harness passed. A part not verified last time is
  judged again in full, and a fixture that is also a reviewed file is a
  file change (#107).
- Every `verify` invocation has its own attempt id, in its output, the
  report data and the report's "Verification attempt" line; the run id
  still names the session confirmation. Kept judgments and kept runtime
  evidence say "kept from attempt <id> of run <run id>" (a report from
  before keeps "kept from run <run id>"), a twice-kept result still
  names the attempt that judged it, and the Review plan binds and names
  the earlier attempt (#106).

### Requirement acceptance

- Accepting requirements is bound to what the user was shown (#100).
  `compare-requirements` runs also for a first acceptance and for an
  older revision that names no single part, and gives every item an
  `item_id`, the `display` the agent shows as it is (every field
  acceptance keeps, the differences, each finding fenced as untrusted)
  and a `digest` over the display and the complete rule; the unchanged
  ones also as one `group`. Too large for one output, it lists ids and
  digests and `--item` gives each whole; `--edited` shows an edit back.
  `accept` needs `--relay` with one decision per item and its digest:
  a stale digest, a contradicting choice, an edit not shown back, or a
  requirement accepted before without an explicit accept, edit, reject
  or drop refuses the whole acceptance. harness-research shows each
  item before asking and records only explicit answers; harness-intake
  and build-skill show what they ask about first.

### Confirmation

- Every confirmation summary stays within 24 lines, 242 words and
  1,428 characters, so with Forge's own one-line question and its two
  options it stays within the owner's limit of 27 lines, 265 words and
  1,575 characters. Its title names only the purpose. It shows, as room
  allows and in this order: what changed since the confirmation the
  agent names with `--compare-with <relay>` (weakened requirements
  first), goal conflicts, overrides and warnings, open drift, parts not
  offered, research to do, then each part's outcome and mechanism. What
  it leaves out is counted by kind before the details link, its last
  line. The details file holds everything in full, now also the parts
  not offered, research to do, override warnings, the comparison and
  the decision record it belongs to; the digest and the relay are
  unchanged. `prepare-contract` returns `question`, `options` and
  `compared_with`, and stops with the cause when the mandatory lines
  cannot fit (#99).

### Build

- `build` replaces or removes an existing file without a question when
  it is the part's last build output or matches the version committed
  in git, on disk and in the index, and names each one under `replaced`
  and `removed` with its basis and how its earlier version can be seen.
  Any other existing file stops the build before it writes, with the
  reason and how to keep or discard the edits; a failed git check never
  counts as a match. build-skill no longer asks the user to approve
  replacements (#101).
- `--replace` may be given more than once; every occurrence adds its
  comma-separated paths (#105).

### Intake

- `select` returns `record`, the part's `alternatives`, `assessment`
  and `outside_controls` in the decision record's shape; harness-intake
  copies them from there, so a record written as `inputs.md` says is
  accepted (#104).
- The README's "Where the skills load" section speaks of one built
  skill in the singular and names its folder; several skills keep the
  plural, and the earlier wording is still read and replaced (#109).

## 0.4.0 (2026-10-27)

Still pins agent-harness-kb 0.6.0. Run files get their own ignored
directory, confirmation shows a short Markdown summary named by its
purpose with the full details in a file, and Forge offers its own
license. Breaking: `prepare-contract` needs `--run-dir` and
`--purpose` and returns a summary and a details file instead of the
full text.

### Security guidance

- harness-intake and harness-research show every fetched claim, quote
  and context inside a backtick fence longer than any backtick run in
  it, after a line labeling it untrusted (#95).
- harness-intake and harness-verify check every path component for a
  symbolic link before the agent writes a record under `.harness/`
  itself, and stop at one (#96).

### Licensing

- Forge inspects every result repository for licensing information, not
  only publicly shared ones, and offers its own license,
  AGPL-3.0-only, with "Yes, please" and "No, thanks" when none is found.
  The new `license` command adds it only after "Yes, please": `LICENSE`
  byte for byte as Forge ships it (refused unless its SHA-256 matches),
  a fixed `License` section in README.md, and the recorded answer. It
  never overwrites or changes existing licensing. A licensing location
  behind a symbolic link now makes the check incomplete instead of
  missing (#91).
- The public licensing question no longer follows a recommendation: it
  asks with two unmarked options, and the recommendation to clarify the
  terms before public reuse comes only after a "continue" answer (#93).

### Confirmation

- What the user confirms is named by its purpose instead of
  "contract": a research proposal, a configuration proposal before
  building, or a configuration summary before verification.
  `prepare-contract` needs `--purpose research|configuration|verification`
  (breaking), which sets the summary's and the details file's title and
  the file's name; it does not change the digest. Forge's diagnostics,
  CLI help and skills follow, and the report labels the digest
  "Confirmed configuration digest". The command name, stored fields and
  file names stay; historical records and reports are unchanged (#85).
- Breaking: `prepare-contract` needs `--run-dir` and returns a Markdown
  `summary` and a `details` file (path and sha256) instead of the full
  `text`. The summary holds what the user decides on: goals, each part's
  goal, outcome and mechanism, reuse, required outside controls with
  readable conditions, every accepted requirement in full, override
  changes and warnings, goal conflicts, refused parts, open research and
  drift decisions. The details file in the run directory holds the full
  contract, the exact bound content, override content and worker
  configurations. The confirmation digest and the relay are unchanged
  (#84).

### Run files

- A run's own files, such as the worker plan and the confirmation
  relays, go into `.harness-forge/runs/<run id>/` at the target
  repository's root instead of an unnamed place such as `.harness/`.
  The new `run-dir` command allocates the directory once per run and
  creates `.harness-forge/.gitignore` with the single line `*` when it
  is missing. Forge reads inputs and writes the drift-issue proposal
  there without following symbolic links, and never overwrites a run
  file. All four skills name the files (#83).

### Verification

- harness-verify ties its note on plugin skills' runtime cases to the
  release instead of saying they cannot run "yet" (#81).
- The not-run reason of a plugin skill's runtime case and the refusal
  of its behavior cases now name the structural limit: the runtime test
  workspace does not install plugins (#81).

## 0.3.0 (2026-10-25)

Pins agent-harness-kb 0.6.0: security guidance and rules for skill
writes, repository metadata, embedded material and credential reads,
readable outside control names and reasons, and checker versions
`selection.match` 1.6.0 and `outside.readme-coverage` 1.3.0. Earlier
pins since 0.2.0 brought `skills.name-format` 1.1.0 (0.5.0), Draft-07
schema copies (0.4.0) and the plugin release-tag guidance (0.3.0).

### Selection and intake

- harness-intake and harness-research present proposed goals, inferred
  answers, findings and requirements as visible text before asking
  about them, ask each inferred fact separately, and ask again when the
  user did not see the content (#44).
- `tools/transcript_checks.py` flags questions in a recorded Claude Code
  session whose content may not have been shown, for review (#44).
- The survey handoff asks about each existing solution first, one at a
  time: investigate it for reuse, explore it for inspiration (its ideas
  are considered, nothing is copied) or discard it, recorded in the new
  optional `survey_candidates` and shown in the contract. Then the other
  findings are presented with the goal, design choice or safeguard each
  suggests, and the user adopts all, some or none (#45).
- After a new deep pass, `forge.py compare-requirements` groups a part's
  proposed requirements against the ones accepted for it before, by
  their full content: unchanged with current source support, changed
  (with each difference), new, unsupported and removed. harness-research
  asks once about the displayed unchanged group, with each item still
  open to edit or rejection, and asks about the others one at a time;
  `accept` stays the only way to record them (#47).

- When a skill will be shared publicly (public reach or a public
  repository), `forge.py licensing` checks the repository root and
  README.md for licensing information. If none is identified,
  harness-intake shows the locations checked, says this is no legal
  determination, links GitHub's licensing guidance and asks once whether
  to point to the terms or continue; the answer is recorded and not
  asked again while those observations stay the same. Forge never chooses
  or adds a license and never holds up the build for this (#58).

### Questions

- harness-intake asks neutral questions: an inferred answer is shown
  with the part of the description it came from before the question,
  and the choices keep a stable order whatever was inferred; only a
  recommendation `select` computed is marked and put first (#72).

### Behavior tests

- Runtime case files take `behavior` cases beside the activation cases.
  Each runs a built skill in a throwaway workspace with only the frozen
  skill, its fixtures and the dependencies it names; the harness lets it
  write only the declared paths (Claude Code through `Edit` rules in
  `dontAsk` mode with reads kept in the workspace, Codex through Forge's
  permission profile, for `dir/**` paths only), without network. A case asserts on the saved files
  and on the recorded tool requests, denials and commands, and passes only
  with the skill's observed activation; anything changed outside the
  declared writes fails it, and what a harness cannot observe makes it
  unavailable. Every declared case must pass in each harness it names for
  the part to be verified. The report shows behavior results in their own
  table with their observation gaps. A behavior case for a skill shipped
  in a plugin is refused before anything runs, since that skill's runtime
  cases cannot run yet (#54).

### README sections and naming

- The result README's Outside controls section explains what outside
  controls are, links the pinned KB guide and names each control by its
  readable name with the reason it is recommended, both from the pinned
  KB by id, which stays for traceability. Availability reads as recorded
  intake information, precautions as "Not recorded as required", and
  "part A" appears only when the decision has several parts. "Where the
  skills load" says "you plan to share them publicly" instead of the
  intake term "reach" (#53).
- Text Harness Forge writes into a target repository names and links
  Harness Forge: the README sections, the requirements reference, the
  verification report's header and the decision record's preference
  text (a plain URL there, as it is YAML). The skills tell the model to
  do the same in files it writes (#50).
- The reader accepts the new and the earlier section forms, so an
  existing README upgrades in place; `outside.readme-coverage` is
  version 1.3.0 and accepts an entry without a part only for a decision
  with one part. `selection.match` is version 1.6.0, for the controls'
  new `name` and `reason` (#50, #53).

### Build

- `forge.py build --checklist` takes one instruction line per accepted
  requirement, written by build-skill for the agent that runs the skill.
  The Requirements section then shows those lines with the label for
  each kind, and `references/requirements.md` keeps the accepted
  statements with their ids and sources. Forge checks the checklist's
  shape; the review worker judges each requirement against its accepted
  statement, so a line that drops part of it fails. Without a checklist
  the output is unchanged (#49).

### Disclosure

- The README and the research workers' reference say that Claude Code
  2.1.310 kept research workers' fetched-page tool results in the
  selected profile after the run, despite `--no-session-persistence`;
  they can hold untrusted content, are not Forge's diagnostics, and
  Forge does not delete profile data (#48).

### Fetched text

- The contract shows each fetched value of a recorded drift, a drift
  decision and an override's description (claim, source, fetch date,
  quote, context) as one escaped JSON string on its own line, under an
  untrusted label, so the text cannot close its quotes or add a line
  such as `Contract digest:`. A contract that shows such text binds the
  rendering, so its digest changes once and earlier confirmations of it
  no longer hold; other contracts keep their digests. The drift issue
  or comment puts the claim and the quote, and a source that is not a
  plain HTTPS URL, in labelled fences longer than any backtick or tilde
  run in them (#71).

### Drift issues

- `drift-issue` writes its proposal and `publish-issue` reads it only
  as a regular file: a symbolic link at that name, also a dangling one,
  a directory or a FIFO is refused before anything is written, read or
  filed, and the check runs on the opened file (#76).

### Conflicts

- `build` reports each conflicting file under `conflicts` with its own
  diff, its full length and whether it was cut: at most 20,000
  characters per file and 60,000 together. Every diff cut or left out is
  marked and named in `incomplete`, and `--show-diff <path>` with
  `--offset` shows a file's whole diff page by page without changing a
  file or record. build-skill shows an incomplete diff whole before
  asking, and asks about each file (#78).

### Provenance

- The verification report names the Harness Forge copy that verified:
  its declared version and, for a Git checkout of Forge, the full commit
  and whether it had uncommitted changes; each value is `unknown` on its
  own when it cannot be established (#52).
- A successful build records the building copy's provenance and the
  bytes and links it left in the part's decision record; the report
  shows that recorded builder separately and whether the checked outputs
  still match that build. A refused build keeps the earlier record, and
  verification never writes it (#55).

### Tokens

- Whole-token checks refuse a trailing line feed or carriage return and
  line feed: schema patterns through fastjsonschema, and Forge's own
  checks (skill names, reviewer command numbers and `sed` scripts, skill
  paths in a Codex run) with full matches. `skills.name-format` is
  version 1.1.0 (#51).

### Dependencies

- Forge's runtime dependencies ship inside the plugin in `vendor/`:
  pure-Python PyYAML, markdown-it-py, mdurl and fastjsonschema, and the
  Draft-07 metaschema. Nothing is downloaded: `scripts/setup.py`,
  `requirements.lock` and the switch to a Forge environment are gone.
  Each skill first runs `forge.py environment`, which checks Python and
  the bundle and names what to repair (#56, #46).
- Validation uses fastjsonschema instead of jsonschema; the schemas are
  Draft-07, and every schema passes the restrictions in
  `packaging/fastjsonschema-audit.md` before it is compiled. An anchored
  pattern no longer accepts a trailing newline (#56).

### Verification

- The review worker judges each requested requirement and the goal
  against every procedure step, exception and output instruction, and
  checks that a closed list of allowed actions covers what the procedure
  requires and the phase it governs; its verdicts are a static
  assessment. Unexecuted reviewer cases from three such contradictions
  are in `evals/harness-verify/review-consistency.yaml` (#57).

## 0.2.0 (2026-10-24)

Pins agent-harness-kb 0.2.0, the first KB version tagged as a release
after 0.1.0.

### Releases

- `tools/pin_snapshot.py --pin` pins only a released KB version: HEAD
  must be the commit tagged `v<kb_version>`, with a dated changelog
  section for that version. Before, every pin since 0.1.0 recorded KB
  0.1.0 while the rules changed, so an override's `pinned_kb_version`
  never showed a KB update (#41).

### Verification

- Skills shipped in a plugin are verified by the plugin layout
  (`skills/<name>/` beside `plugin.json`), and a reused plugin skill's
  shared code is reviewed with it (#5, #9).
- A user-invoked skill's explicit case runs as the user starts it:
  `/<name>` in Claude Code, observed through the replayed command
  expansion, and `$name` in Codex, observed temporarily through the
  run's session rollout (#6, #7, #9; the Codex observer is #18).
- The reviewer learns which outside controls a goal relies on, and each
  gate condition is judged against the controls that list it (#10, #20).
- Each runtime launch's output is kept for diagnosis, and a failed
  skill-listing launch is named with how it ended (#8, #22).
- A review reply that validation rejects is retried once with a fresh
  reviewer (#32).

### Research and requirements

- `research --recheck` checks only the lost sources of requirements the
  user accepted earlier, and a research worker names the requirement a
  re-check finding is for (#31, #34).
- A reuse candidate the user names reaches research, and
  harness-research no longer files a public issue itself (#9).
- `prepare-contract` names the parts whose edited inputs need research
  again (#21).
- Accepting requirements for one part no longer changes the
  requirements reference of other built skills (#33).
- Fetched content is labelled untrusted wherever Forge shows it (#19).

### Selection and intake

- A part that changes external state or needs timing the model should
  not choose excludes model-started mechanisms and prefers a
  user-invoked skill when the user starts it (KB `side_effects`, #26).
- harness-intake records only links and controls the user confirmed,
  and names refused parts instead of refusing the whole contract (#6).

### Markdown, environment and skills

- Markdown is read with markdown-it-py (CommonMark) instead of Forge's
  own scanner (#2).
- Forge rebuilds its environment when its Python no longer starts (#39).
- harness-verify keeps its verified conditions in one place, and
  harness-research says when to read its worker reference (#25, #37).
- The routing evaluation of Forge's own skills, with offline re-judging
  of recorded runs (#3, #4).
- Forge's verification of its own four skills is recorded in `.harness/`
  (#11, #23, #28, #35, #40, #41).

## 0.1.0 (2026-10-16)

First release, published with agent-harness-kb 0.1.0. Tagged `v0.1.0`
afterwards at `05dd37b`, the publication commit.

- The plugin for Claude Code and Codex: harness-intake, harness-research,
  build-skill and harness-verify, run through the vendors' CLIs with
  the user's existing login, against a pinned snapshot of the KB.
