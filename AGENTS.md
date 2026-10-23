# Harness Forge contributor instructions

All agents must preserve the public account contract in
[README.md](README.md#model-access): standard vendor CLIs, vendor-default
account locations and the user's existing login, with a cost warning and
a continue/stop question for an API-key or unknown login. Do not introduce
the project owner's aliases, profile paths, plugins or shell setup into
product code, fixtures or installation requirements. Personal setup is
a temporary local-development exception only and is documented outside
this repository.

Apply temporary worker restrictions without changing saved user settings.
Never initiate login/logout, copy credentials, switch accounts or use a
direct model API/API-credit fallback. Document remaining isolation gaps.
Keep historical probe code, raw evidence and evidence indexes unchanged;
new implementation checks need their own evidence.

Use Conventional Commits and record AI attribution as final-paragraph
`Assisted-by: <model> (code generation)` or `(code review)` trailers.

Run tests with the locked dependencies, as in
[packaging/assessment.md](packaging/assessment.md#reproduce):
`python3 -m venv .venv`, install `requirements.lock` with
`--require-hashes --only-binary :all:`, then
`.venv/bin/python -m unittest discover -s tests -t . -v`.

Pin the knowledge base only from a release: `tools/pin_snapshot.py
--pin` refuses a KB checkout that is not the commit tagged
`v<kb_version>`. Every Forge release raises the version in
`.claude-plugin/plugin.json`, `plugin.json`,
`.claude-plugin/marketplace.json` and `pyproject.toml` and adds a dated
section to [CHANGELOG.md](CHANGELOG.md); installed copies stay on their
cached version until it changes. After the merge, tag the merge commit
`harness-forge--v<version>` and create a GitHub release from its
changelog section. Never move a release tag.

Changes reach `main` only through a pull request, merged with a merge
commit (the only method this repository allows). Keep the PR
description in step with what the PR contains. Merge only after the
repository's required checks (the test suite above) pass and an
independent review of the latest pushed head
finds the PR ready; a push after that review needs a new one. The
owner's AI agents (Claude implementing, Codex planning and reviewing)
follow the working mode in
[playground's AGENTS.md](https://github.com/franks-ai-skills/playground/blob/ai-stuff/AGENTS.md#working-mode-claude-and-codex-owner-2026-10-22).
