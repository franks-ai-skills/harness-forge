# Generated-output permission (draft 2)

Status: **draft, not in effect.** The wording needs the project owner's
approval before either repository or the plugin is published. Until
then, `LICENSE` holds the unmodified AGPL-3.0 text and no exception is
granted. This draft is not legal advice; it has not been reviewed by a
lawyer.

## Proposed text

To be published as `LICENSE-OUTPUT-EXCEPTION` in Forge, and as the same
text with "Agent Harness KB" in the knowledge-base repository, whose rule
text Forge copies into its output.

```text
Harness Forge Generated-Output Permission

Additional permission under section 7 of the GNU Affero General Public
License, version 3 ("AGPLv3").

The licensors of Harness Forge grant the following additional
permission. It applies to each file of Harness Forge that bears a notice
stating that the file is governed by the AGPLv3 along with this
permission.

1. Definitions.

"Generated Output" means only the files and file sections that Harness
Forge creates in a repository on which it operates: the skill packages
it builds and the links it creates for them; the decision, research,
override and report records under .harness/; and the sections it
writes into that repository's README.md.

"Eligible Forge Material" means the parts of Harness Forge, as released
by its licensors, that it is designed to copy into Generated Output in
normal operation, such as the text of its templates, rules and
knowledge-base snapshot, to the extent the licensors are authorized to
license that material.

2. Grant of additional permission.

To the extent Generated Output contains Eligible Forge Material, you may
use, reproduce, modify, propagate, convey and license that material, as
it appears in the Generated Output, under terms of your choice,
including proprietary terms, without complying with the conditions of
the AGPLv3 for that material. Including Eligible Forge Material does
not by itself make the Generated Output subject to the AGPLv3.

3. Limits.

This permission grants no rights in material in Generated Output that
belongs to you or to third parties; that material keeps its own
copyright and license terms.

This permission does not apply to Harness Forge itself. Its source
code, skills, worker instructions, rules, templates, knowledge-base
snapshot and other project files remain licensed under the AGPLv3 only;
only Eligible Forge Material, as copied into Generated Output, is
covered.

If you modify Harness Forge, material that you cause to be copied into
Generated Output and that the version released by the licensors does
not copy there is not Eligible Forge Material. You may extend this
permission to material you add or modify, if you have the authority to
do so, but you are not required to.

You may remove this permission from a copy or a modified version of
Harness Forge, as section 7 of the AGPLv3 permits.
```

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
  user's ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput)),
  so the grant covers only Forge's own material.
- AGPLv3 section 7 allows additional permissions, lets recipients
  remove them, and allows placing them only on material "for which you
  have or can give appropriate copyright permission"; the FSF adds that
  people who modify a program "do not have to make the same exception
  for their code" ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLIncompatibleLibs)).
  Hence the contribution rule.
- The modification clause follows the
  [Autoconf exception](https://www.gnu.org/licenses/autoconf-exception-3.0.html),
  which limits its permission to "Normally Copied Code". Autoconf voids
  the whole exception for a modified version that copies other code;
  this draft only excludes that material, which keeps the permission
  usable for forks.
- The line "Everyone is permitted to copy and distribute verbatim copies
  of this license document, but changing it is not allowed" stands before
  the AGPL's preamble; it is why the exception does not go into
  `LICENSE`.
