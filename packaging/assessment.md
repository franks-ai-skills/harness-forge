# Runtime dependency distribution

Status: decided on 2026-10-09 (plan task T3). Default: **(c) one-time
isolated setup from a hashed lock**. The YAML formats and JSON Schema
Draft 2020-12 stay as designed.

## Requirement

The core reads YAML records and validates them against closed JSON Schema
Draft 2020-12 schemas. The plan forbids writing an own YAML parser or
schema validator and forbids changing the formats to ease packaging.

## Candidates

Package data is from the PyPI JSON API (`https://pypi.org/pypi/<name>/json`),
fetched on 2026-10-09.

| Package | Pinned | Requires Python | Native code |
| --- | --- | --- | --- |
| `jsonschema` | 4.26.0 | >=3.10 | no |
| `referencing` | 0.37.0 | >=3.10 | no |
| `rpds-py` | 2026.6.3 | >=3.11 | yes, Rust; platform wheels only |
| `attrs` | 26.1.0 | >=3.9 | no |
| `jsonschema-specifications` | 2025.9.1 | >=3.9 | no |
| `pyyaml` | 6.0.3 | >=3.8 | optional libyaml extension; the wheels are per platform |
| `typing-extensions` | 4.16.0 (Python < 3.13 only) | >=3.9 | no |
| `markdown-it-py` | 4.2.0 | >=3.10 | no |
| `mdurl` | 0.1.2 | >=3.7 | no |

`jsonschema` 4.26.0 requires `attrs`, `jsonschema-specifications`,
`referencing` and `rpds-py`; `referencing` 0.37.0 adds `typing-extensions`
below Python 3.13. `rpds-py` is pinned to 2026.6.3, not the newest
2026.9.1 (published 2026-10-04): pins prefer releases at least 14 days old.

### (a) Platform-specific bundles

Ship the locked wheels inside the plugin, one set per platform and
Python minor version.

- Works offline and needs no package index.
- Python cannot import the native `rpds-py` module from a wheel archive,
  so the plugin must still unpack it into a writable directory at first
  use. The setup step of (c) remains; only its download is avoided.
- One platform set is 0.7–1.4 MB (measured for macOS arm64 / Python 3.14
  and Linux glibc x86_64 / Python 3.12). The eight platforms below times
  four Python versions would add roughly 30 MB of binary files to the
  repository with every dependency update.

### (b) Maintained pure-Python Draft 2020-12 validator

None found that qualifies.

- `fastjsonschema` implements drafts 04, 06 and 07 only
  ([documentation](https://horejsek.github.io/python-fastjsonschema/)).
- `jschon` supports 2019-09 and 2020-12, but its repository
  [marksparkza/jschon](https://github.com/marksparkza/jschon) is archived;
  its last release, 0.11.1, is from 2023-10-22.
- `jsonschema` itself has no pure-Python mode since it depends on
  `rpds-py`; its older pure-Python releases are unmaintained.

### (c) Documented one-time isolated setup (selected)

Create a virtual environment owned by Forge and install exactly the
locked wheels into it:

```
python3 -m venv <forge-env>
<forge-env>/bin/python -m pip install --require-hashes --only-binary :all: -r requirements.lock
```

- `requirements.lock` lists every published sha256 of each pinned release,
  so pip refuses any other file. `--only-binary :all:` means no package
  build code runs during installation.
- It needs network access to PyPI once. Offline machines use a wheelhouse
  prepared elsewhere with the same lock:
  `pip download --require-hashes --only-binary :all: -r requirements.lock -d wheelhouse`,
  then `pip install --no-index --find-links wheelhouse --require-hashes --only-binary :all: -r requirements.lock`.
- It never changes user configuration or the user's Python environment.
- Maintenance is one lock regeneration per update.
- T13 decides where `<forge-env>` lives for each harness and how the skills
  find it. Claude Code provides `${CLAUDE_PLUGIN_DATA}` for persistent
  plugin data; Codex documents `PLUGIN_DATA` for plugin hooks.

## Markdown parser

Added on 2026-10-17 (owner decision of 2026-10-16). Forge's own scanner
modelled fenced code, HTML blocks and ATX headings but no list items,
block quotes, indented code or setext headings, so a fence inside a list
item could hide a broken link or a heading. `markdown-it-py` (MIT, the
Executable Books project) is a CommonMark parser in pure Python whose
only dependency is `mdurl` (MIT); both ship one universal wheel, so the
platform table below is unchanged. Forge uses its `commonmark` preset,
keeps link destinations as written (`normalizeLink` is the identity,
`validateLink` accepts every destination, since Forge classifies schemes
itself) and replaces the rules where it reads other than CommonMark
0.31.2, as two Codex review rounds found: HTML blocks and inline HTML
(markdown-it-py's patterns use Python's Unicode `\s` and
`re.IGNORECASE`, so `</scrıpt>` closed a `<script>` block and a form
feed counted as whitespace; declarations needed an uppercase letter),
reference definitions (no 999-character label limit, measured here as
the paragraph reads the label; the next line started a block where
CommonMark continues the paragraph in the same container) and indented
code below a definition. Reference labels are normalized as CommonMark
says, with Unicode case folding and only spaces, tabs and line endings
collapsed; markdown-it-py collapsed Unicode whitespace and folded with
`lower().upper()`, which matched `ı` with `I`. Its link, image and
reference modules look the function up by name, so Forge sets it there.
Inline HTML finds the end of a comment, instruction or CDATA section
once per paragraph instead of once per opener; markdown-it-py's own
rule took 26 s for 64 KiB of `<?`. markdown-it-py's text accumulation
stays quadratic on a paragraph of alternating punctuation, also without
HTML: about 10 s for 1 MiB, Forge's input limit. Wrappers at the entry of its block and inline
tokenizers notice nesting that reaches the parser's limit, where it
skips content, and Forge refuses such files. Token trees are cached only
for documents up to 64 KiB. A substitution of the affected characters
before parsing was tried and dropped: it changed link destinations,
headings and reference labels.

`cmarkgfm`, the bindings to GitHub's cmark-gfm, would read exactly as
GitHub renders, but it publishes no wheel for macOS x86_64 (and its
newest release none for Windows arm64), which Forge's binary-only
installation needs.

## Library behavior Forge must configure

Observed with the locked versions on macOS arm64, Python 3.14.8
(`tests/test_dependency_conformance.py`).

- **Remote references.** By default `Draft202012Validator` calls
  `urllib.request.urlopen` for a remote `$ref`. Forge always passes an
  empty `referencing.Registry()`, which makes the reference unresolvable
  without any network call.
- **YAML 1.1 scalars.** `yaml.SafeLoader` turns `yes`, `no`, `on` and `off`
  into booleans and `2026-10-09` into a date, and keeps the last of
  duplicate keys. A `SafeLoader` subclass without the YAML 1.1 bool and
  timestamp resolvers, with a strict `str`/`map` constructor, rejects
  ambiguous boolean words and duplicate keys, keeps dates as strings and
  accepts `true`/`false`. T4's document loader adopts this configuration.
- **Python tags** such as `!!python/object/apply` are rejected by
  `SafeLoader`.
- **Formats.** Draft 2020-12 treats `format` as an annotation; Forge does
  not rely on it for validation.

## Supported environments

| Platform | 3.11 | 3.12 | 3.13 | 3.14 | 3.15 |
| --- | --- | --- | --- | --- | --- |
| macOS arm64 | wheels | wheels | wheels | **tested** | no PyYAML wheel |
| macOS x86_64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Linux glibc x86_64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Linux glibc aarch64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Linux musl x86_64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Linux musl aarch64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Windows amd64 | wheels | wheels | wheels | wheels | no PyYAML wheel |
| Windows arm64 | no PyYAML wheel | wheels | wheels | wheels | no PyYAML wheel |

- **tested**: the lock installed into a fresh virtual environment and the
  conformance suite passed (13 tests).
- **wheels**: `pip download --require-hashes --only-binary :all:` resolved
  every locked file for that platform and CPython version. The install and
  tests were not run there, so these environments are not claimed as
  supported until a later task tests them.
- **no PyYAML wheel**: PyYAML 6.0.3 publishes no wheel for that
  combination, so the binary-only install fails.

The CPython builds must include `venv` and `pip` (`ensurepip`); some Linux
distributions package them separately.

## Reproduce

```
python3 tools/lock_requirements.py packaging/requirements.in requirements.lock
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary :all: -r requirements.lock
.venv/bin/python -m unittest tests.test_dependency_conformance -v
```
