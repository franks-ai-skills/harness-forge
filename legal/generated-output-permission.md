# Generated-output permission (draft)

Status: **draft, not in effect.** The wording needs the project owner's
approval before either repository or the plugin is published. Until
then, `LICENSE` holds the unmodified AGPL-3.0 text and no exception is
granted. This draft is not legal advice; it has not been reviewed by a
lawyer.

## Proposed text

```text
Additional permission under GNU AGPL version 3 section 7

As a special exception, the copyright holders of Harness Forge give you
permission to use, copy, modify and distribute Generated Output under
terms of your choice, without the conditions of the GNU Affero General
Public License, even where the Generated Output contains parts of
Harness Forge's templates, rules or knowledge-base text that Harness
Forge copied into it.

"Generated Output" means the files and file sections that Harness Forge
writes into a repository it is run on: skill packages and their links,
the decision, research, override and report records under .harness/,
and the sections it writes into that repository's README.md.

This permission does not cover Harness Forge itself, in source or any
other form: its code, skills, worker instructions, templates and
knowledge-base snapshot remain licensed under the GNU Affero General
Public License version 3 only.

If you modify Harness Forge, you may extend this permission to your
version, but you are not obliged to do so. If you do not wish to, delete
this permission from your version.
```

## Proposed placement

- Append the text above to the end of `LICENSE`, after the unchanged
  AGPL-3.0 text and its "How to Apply These Terms" appendix, separated
  by a line of dashes. The AGPL text itself stays byte-for-byte as
  published by the FSF, as its preamble requires.
- Change the README's License section to: "AGPL-3.0-only, with an
  additional permission for the files Forge generates in your
  repository; see the end of [LICENSE](LICENSE)."
- Keep this file as the record of the approved wording and its sha256.

## Why these choices

- The parent design settles the license: AGPL-3.0 with an additional
  permission that excludes files Forge generates in a user's repository.
  Without it, a generated skill that contains template or rule text could
  be argued to be a covered work.
- Section 7 of the AGPL allows additional permissions that make
  exceptions from its conditions, and lets a recipient remove them. The
  last paragraph states that explicitly.
- The definition lists what Forge writes today: `build` writes
  `.agents/skills/<name>/` and its `.claude/skills/<name>` link,
  `readme` writes the README's `Outside controls` section, and the other
  commands write records under `.harness/`.
