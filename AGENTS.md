# Harness Forge contributor instructions

All agents must preserve the public account contract in
[README.md](README.md#model-access): standard vendor CLIs, vendor-default
account locations and an existing subscription login. Do not introduce
the project owner's aliases, profile paths, plugins or shell setup into
product code, fixtures or installation requirements. Personal setup is
a temporary local-development exception only; in the current sibling
workspace its instructions are at
`../playground/plans/harness-forge-local-development.md`.

Apply temporary worker restrictions without changing saved user settings.
Never initiate login/logout, copy credentials, switch accounts or use a
direct model API/API-credit fallback. Document remaining isolation gaps.
Keep historical probe code, raw evidence and evidence indexes unchanged;
new implementation checks need their own evidence.

Use Conventional Commits and record AI attribution as final-paragraph
`Assisted-by: <model> (code generation)` or `(code review)` trailers.
