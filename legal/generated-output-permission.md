# Generated-output permission (draft 4)

Status: **draft, not in effect.** The wording needs the project owner's
approval before either repository or the plugin is published. Until
then, `LICENSE` holds the unmodified AGPL-3.0 text and no exception is
granted. This draft is not legal advice; it has not been reviewed by a
lawyer.

## Proposed text

Written in its own words. It follows the concepts of the FSF's
[Autoconf Configure Script Exception 3.0](https://www.gnu.org/licenses/autoconf-exception-3.0.html)
(material the program normally copies into its output; a modified
version that copies other material loses the exception) but does not
reuse its text, which the FSF does not permit to be changed. To be
published as `LICENSE-OUTPUT-EXCEPTION` in both repositories.

```text
HARNESS FORGE GENERATED OUTPUT EXCEPTION
Version 1.0

This is an additional permission under section 7 of the GNU Affero
General Public License, version 3 ("AGPLv3"). It covers a file only
when the file's copyright holder has marked it with a notice saying that
the file is licensed under AGPLv3 together with this exception. It
exists so that what Harness Forge generates in your repository may be
used and licensed on terms you choose, including proprietary terms.

1. Definitions

"Covered Material" is copyrightable material that is licensed under
AGPLv3 together with this exception, in a file bearing that notice,
whether the file belongs to Harness Forge or to a knowledge base
distributed with it.

"Output" is the files and file sections that Harness Forge writes into
a repository it operates on: the skill packages and the links it
creates for them, the records under .harness/, and the sections it
writes into that repository's README.md.

"Normally Copied Material" of a version of Harness Forge is the Covered
Material that this version copies into Output from its own files,
rather than from its input or from content it fetches.

"Ineligible Material" is Covered Material that is not Normally Copied
Material.

2. Permission

Where Output contains Normally Copied Material, you may use, run,
modify, propagate, convey and license that Output, including that
material, on terms of your choice, without being bound by the
conditions of AGPLv3 for that material, including those of its
section 13.

3. Modified versions

If you modify Harness Forge so that Ineligible Material of the version
you received becomes Normally Copied Material of your version, this
exception does not apply to your version. If you convey your version,
you must remove this exception from it, as the second paragraph of
section 7 of AGPLv3 allows.

4. Other material

This exception gives no rights in material in Output that comes from
your input, from content Harness Forge fetches, or from anyone other
than the licensors of the Covered Material. That material keeps its own
copyright and license terms.

5. No weakening of the copyleft

Except for Output as described here, Harness Forge and its knowledge
base remain licensed under AGPLv3 only. This exception does not suggest
that other software is free of the copyleft requirements of AGPLv3.
```

## Proposed placement

- `LICENSE` stays the unchanged AGPL-3.0 text in both repositories; the
  exception is the separate file `LICENSE-OUTPUT-EXCEPTION`, as GCC keeps
  its runtime exception in `COPYING.RUNTIME`.
- Every file whose material Forge copies into Output carries this notice,
  as AGPLv3 section 7 requires for added terms ("a statement of the
  additional terms … or a notice indicating where to find the applicable
  terms"):

  ```text
  SPDX-License-Identifier: AGPL-3.0-only
  Licensed under AGPLv3 together with the Harness Forge Generated Output
  Exception; see LICENSE-OUTPUT-EXCEPTION.
  ```

  In Forge: the skill templates and the code that writes text into
  Output (the requirements, report and README-section writers). In the
  knowledge base: the rule files whose statements and sources Forge
  copies. `tools/pin_snapshot.py` copies those files byte for byte, so
  the snapshot's copies carry the same notice; a test checks it.
  Templates carry the notice as a comment line that Forge drops when it
  renders them, so it never appears in Output.
- SPDX: REUSE 3.3 builds on SPDX 2.3, where `WITH` takes only exceptions
  from the SPDX License List; an unlisted exception would need one
  `LicenseRef-…` for the whole license with the exception. The notice
  keeps the standard `AGPL-3.0-only` identifier and names the exception
  in words instead.
- `CONTRIBUTING.md` in both repositories contains this grant:

  ```text
  By submitting a contribution, you license it under AGPL-3.0-only and,
  where it is part of a file that carries the Harness Forge Generated
  Output Exception notice, under that exception as well.
  ```

- The README's License section names both files.

## Why these choices

- The design settles the license: AGPL-3.0 with an additional permission
  that excludes files Forge generates in a user's repository. The FSF
  says the output of a program is generally not covered by the program's
  copyright; that changes where substantial parts of the output are
  copied from the program, with Bison as its example
  ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput)).
  Template or rule text that Forge copies into a generated skill may
  therefore stay under the AGPL without this permission.
- Copyright in the rest of the output follows its input, often the
  user's ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput));
  section 4 says so in the exception itself.
- Section 2 names more than propagation. AGPLv3 defines "propagate" to
  exclude "executing it on a computer or modifying a private copy", and
  its section 13 applies to a modified version that users interact with
  over a network, so a permission only to propagate would leave
  section 13 in place.
- AGPLv3 section 7 allows additional permissions, lets recipients remove
  them, and allows placing them only on material "for which you have or
  can give appropriate copyright permission"; the FSF adds that people
  who modify a program "do not have to make the same exception for their
  code" ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLIncompatibleLibs)).
  Hence the contribution grant.
- "Covered Material" marked by the file notice lets one text serve both
  repositories; the knowledge base's files are not code of Harness Forge.
- The FSF's notice on the Autoconf exception says "changing it is not
  allowed". Its FAQ accepts modified versions of its licenses under
  another name ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#ModifyGPL))
  but says nothing about exception texts, so this draft uses its own
  wording. Asking the FSF (licensing@gnu.org) for permission to adapt
  the Autoconf text is the alternative.
- The line "Everyone is permitted to copy and distribute verbatim copies
  of this license document, but changing it is not allowed" stands
  before the AGPL's preamble; it is why the exception does not go into
  `LICENSE`.
