# fastjsonschema 2.22.2: code generation audit

Date: 2026-10-24, for #56. fastjsonschema turns a schema into Python
source and executes it, so every schema Forge compiles is input to code
generation. This note records how schema values reach that code and the
restrictions Forge applies before compiling. Read: `generator.py`,
`draft04.py`, `draft06.py`, `draft07.py` and `ref_resolver.py` of the
vendored 2.22.2, with probes on the vendored copy.

## How schema values reach the generated code

| Path | How it is written | Result |
|---|---|---|
| String values in messages and key lookups (`enum`, `required` in messages, property names, `dependencies`) | `CodeGenerator.e`: `unicode_escape`, then `"` escaped, inside a double-quoted literal | A quote, backslash or newline cannot leave the literal (probed with `a"); import os; ("` as a property name) |
| `enum`, `const`, regexes, `required` in code, definitions in exceptions | `repr`, `repr_default`, `re.compile(repr(...))` | Python literals of JSON values |
| Numeric keywords (`minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minLength`, `maxLength`, `minItems`, `maxItems`, `minProperties`, `maxProperties`) | inserted as written, through `str.format` | Safe only for numbers; a non-finite number becomes `nan` or `inf`, an undefined name, and validation fails with `NameError` |
| Property names in error names | the name string is formatted again with `.format(**locals())` at validation time when it contains `{` | **Braces in a property name are evaluated against the validator's local variables:** `{nope}` aborts validation with `KeyError`; `{data.__class__.__mro__}` writes attribute values into the message. `str.format` cannot call functions, so this is not code execution. Fixed on fastjsonschema's master, unreleased |
| Property names in variable names | non-alphanumeric characters removed | Distinct names can share a variable (`a-b`, `ab`); each value is assigned right before its own checks, and the probe validated both correctly |
| Unknown keywords | ignored | A keyword from another draft (`prefixItems`, `$defs`) or a typo silently weakens the schema |
| `$ref` | resolved by `RefResolver`; a scheme without a handler goes to `urllib.request.urlopen` | Retrieval attempts for any unhandled scheme, including `urn:` |
| Local `$ref` inside a schema reached through a `$ref` | resolved against the outer schema | Can accept invalid input (evaluation report, probe 2) |

Compiling is not a schema check: malformed schemas such as
`{"required": [1]}` or `{"minItems": -1}` compile without error.

## Restrictions Forge applies before compiling

Every schema Forge compiles, its own and each knowledge-base checker's
parameter schema, must pass all of these, or Forge refuses it with the
reason before any code is generated:

1. **Draft-07 only.** `$schema`, when present, is Draft-07's and stands
   only at the root; Forge compiles every schema as Draft-07 (otherwise
   fastjsonschema picks its generator from `$schema`, and a schema
   declaring Draft-04 would be checked against Draft-07's metaschema but
   validated by Draft-04's rules). The schema must satisfy the Draft-07
   metaschema, validated with formats on, so `pattern` and
   `patternProperties` keys must be valid regular expressions. The
   metaschema is the trusted bootstrap input: the bundled copy of the file
   json-schema.org serves, pinned by sha256 in `vendor/manifest.json`.
2. **Draft-07 vocabulary only:** every keyword of every schema object is
   a Draft-07 keyword. Only schema positions are checked, not literal
   objects under `const`, `enum`, `default` or `examples`. This rejects `$defs`, `prefixItems` and typos
   that would otherwise be ignored.
3. **No `{` or `}` in property names** in `properties`, `required` and
   `dependencies`, because of the runtime formatting above.
   `patternProperties` keys are regular expressions written with `repr`,
   so their braces (quantifiers) are allowed.
4. **Finite numbers only.**
5. **References:** `$ref` names one of Forge's own schema ids, with an
   optional fragment, or a local `#` fragment. Forge's own schemas write
   their references to themselves as absolute references to their own
   `$id`. Every other reference is refused, and resolution claims every
   URI scheme so that nothing is retrieved. Only Forge's own schemas
   declare `$id`.

Validation then runs with `use_default=False` (inputs are never
changed), `use_formats=False` (formats are annotations, as before) and
`fast_fail=False` (every error is reported).

## Not covered

No fuzzing of the generator. The audit covers the keywords Draft-07
defines; restriction 2 keeps everything else out. Upstream reporting is
out of scope (#56).
