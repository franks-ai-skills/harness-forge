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
