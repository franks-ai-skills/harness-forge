# Severity policy

Every rule has a `severity` and an `evidence` label. This policy sets the
severity; the evidence label only caps it.

## Severities

- `error`: a failure keeps the part from `verified`.
- `warning`: a failure is reported but does not block `verified`.

## How severity is set

1. A rule is `error` when breaking it makes the result fail to work in a
   target harness, or exposes credentials, code execution or data the
   user did not intend to expose. Examples: a missing or over-long skill
   description, a hook that runs unreviewed input in a shell.
2. Every other rule is `warning`: style, structure and practices whose
   breach makes a result worse but still working.
3. Vendor advice is not automatically `error`. It is `error` only under
   point 1.

## Caps from evidence

| Evidence | Highest severity |
| --- | --- |
| `Vendor`, `Specification`, `Research`, `Empirical` | `error` |
| `Advisory`, `Practitioner`, `Inference` | `warning`, unless `severity_rationale` records why the rule meets point 1 |

The labels are the ones the best-practices pages use: `Advisory` marks an
open specification or standards body's guidance, `Empirical` a study or
measured data.

A rule with `contested: true` is always `warning`, and its `positions`
list each side with its sources.

`validate-kb` enforces the caps and the contested rule.
