# Generated-output permission (draft 3)

Status: **draft, not in effect.** The wording needs the project owner's
approval before either repository or the plugin is published. Until
then, `LICENSE` holds the unmodified AGPL-3.0 text and no exception is
granted. This draft is not legal advice; it has not been reviewed by a
lawyer.

## Proposed text

Adapted from the FSF's
[Autoconf Configure Script Exception 3.0](https://www.gnu.org/licenses/autoconf-exception-3.0.html).
Only the program's name, the license (AGPLv3 instead of GPLv3), the
title and what counts as the program's output differ; the structure
and the remaining wording are Autoconf's. To be published as
`LICENSE-OUTPUT-EXCEPTION`.

```text
HARNESS FORGE GENERATED OUTPUT EXCEPTION
Version 1.0

This Exception is an additional permission under section 7 of the GNU
Affero General Public License, version 3 ("AGPLv3"). It applies to a
given file that bears a notice placed by the copyright holder of the
file stating that the file is governed by AGPLv3 along with this
Exception.

The purpose of this Exception is to allow distribution of Harness
Forge's typical output under terms of the recipient's choice
(including proprietary).

0. Definitions.

"Covered Code" is the source or object code of a version of Harness
Forge that is a covered work under this License, including the
knowledge-base snapshot it contains.

"Output" means the files and file sections that Harness Forge writes
in a repository it operates on: skill packages and the links it creates
for them, the records under .harness/, and the sections it writes into
that repository's README.md.

"Normally Copied Code" for a version of Harness Forge means all parts
of its Covered Code which that version can copy from its code (i.e.,
not from its input or from research it fetches) into its Output.

"Ineligible Code" is Covered Code that is not Normally Copied Code.

1. Grant of Additional Permission.

You have permission to propagate Output of Harness Forge, even if such
propagation would otherwise violate the terms of AGPLv3. However, if
by modifying Harness Forge you cause any Ineligible Code of the version
you received to become Normally Copied Code of your modified version,
then you void this Exception for the resulting covered work. If you
convey that resulting covered work, you must remove this Exception in
accordance with the second paragraph of Section 7 of AGPLv3.

2. No Weakening of Harness Forge Copyleft.

The availability of this Exception does not imply any general
presumption that third-party software is unaffected by the copyleft
requirements of the license of Harness Forge.
```

The Agent Harness KB repository adds the same exception for its files
that Forge copies into its snapshot (its rule files), so the notice
travels with them.

## Proposed placement

- `LICENSE` stays the unchanged AGPL-3.0 text. The exception goes into
  its own file, `LICENSE-OUTPUT-EXCEPTION`, as GCC keeps its runtime
  exception in `COPYING.RUNTIME`.
- Every file whose material Forge copies into its output carries a short
  notice, as AGPLv3 section 7 requires for added terms ("a statement of
  the additional terms … or a notice indicating where to find the
  applicable terms"):

  ```text
  SPDX-License-Identifier: AGPL-3.0-only
  Additional permission for generated output: see LICENSE-OUTPUT-EXCEPTION.
  ```

  These are Forge's templates, the code that writes text into the
  output (the report, README section and requirements writers), and in
  the knowledge base the rule files whose statements and sources Forge
  copies. Templates get the notice as a comment line that Forge drops
  when it renders them, so it never appears in the output itself. No
  custom SPDX `WITH` identifier: REUSE 3.3 builds on SPDX 2.3, which
  allows only exceptions from the SPDX License List.
- `CONTRIBUTING.md` in both repositories says that contributions are
  accepted under AGPL-3.0-only together with this permission, so that
  the permission also covers contributed material.
- The README's License section points to both files.

## Why these choices

- The design settles the license: AGPL-3.0 with an additional permission
  that excludes files Forge generates in a user's repository. According
  to the FSF, the output of a program is generally not covered by the
  program's license; copyrightable parts of the program that are copied
  into the output are ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput),
  with Bison as the example). Without the permission, AGPL-licensed
  template or rule text copied into a generated skill would stay under
  the AGPL and could impose its conditions on that file.
- Copyright in the rest of the output follows its input, often the
  user's ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput)).
- AGPLv3 section 7 allows additional permissions, lets recipients
  remove them, and allows placing them only on material "for which you
  have or can give appropriate copyright permission"; the FSF adds that
  people who modify a program "do not have to make the same exception
  for their code" ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLIncompatibleLibs)).
  Hence the contribution rule.
- The text reuses the Autoconf exception's structure and wording,
  including its rule that a modified version which copies other code
  into its output voids the exception. The Autoconf text says "changing
  it is not allowed"; for its own license texts the FSF accepts modified
  terms under another name and without its preamble, and asks people to
  use the GPLv3 exception mechanism instead of new licenses
  ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#ModifyGPL)).
  Applying that to an exception text is an inference; hence the new
  title and no GNU name in it.
- Like Autoconf's, the grant only lifts AGPLv3 conditions for
  propagating the output; it gives no rights in the user's own or third
  parties' material in that output, which the licensors could not grant.
- The line "Everyone is permitted to copy and distribute verbatim copies
  of this license document, but changing it is not allowed" stands before
  the AGPL's preamble; it is why the exception does not go into
  `LICENSE`.
