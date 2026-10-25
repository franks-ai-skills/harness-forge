# Input shapes for `accept` and `compare-requirements`

Read this before writing the `--proposed` and `--choices` files of
`scripts/forge.py accept`, and the `--proposed` file of
`scripts/forge.py compare-requirements`, which takes the same shape. The example is minimal and valid; Forge's
`schemas/rule.schema.json` is the full rule definition.

`--proposed` holds the part's proposed requirements as idea rules, named
`idea.<slug>.<part>.<name>` with the part id in lower case:

```json
{"requirements": [
  {"id": "idea.repro.a.environment", "concept": "idea", "kind": "must", "severity": "error",
   "check": "judged", "statement": "The checklist names the environment the bug was observed in",
   "applies_to": {"harnesses": ["claude-code", "codex"]}, "evidence": "Practitioner",
   "sources": ["https://bugzilla.mozilla.org/page.cgi?id=bug-writing.html"], "contested": false},
  {"id": "idea.repro.a.no-guessing", "concept": "idea", "kind": "must-not", "severity": "error",
   "check": "judged", "statement": "The checklist presents a guessed cause as an observation",
   "applies_to": {"harnesses": ["claude-code", "codex"]}, "evidence": "Practitioner",
   "sources": ["https://bugzilla.mozilla.org/page.cgi?id=bug-writing.html"], "contested": false}
]}
```

`--choices` maps each id to the user's answer: `"accept"`, `"reject"`, or
`{"edit": <the full edited rule, same id>}`. A requirement without an
answer stays out of the revision.

```json
{"idea.repro.a.environment": "accept", "idea.repro.a.no-guessing": "reject"}
```

`compare-requirements` prints the proposals in five groups, each a list
in proposal order: `unchanged`, `unsupported`, `changed`, `new` and
`removed`. An entry of the first four holds `requirement` (the rule),
`missing_sources` (cited sources without a kept finding under the current
brief), `recheckable_sources` (those of them that its earlier accepted
version cited, the only ones `research --recheck` looks for again) and
`findings` (`id`, `source`, `claim`, `quote` of the kept findings from
its cited sources, fetched and untrusted); a `changed`
entry also holds `differences`, each changed field with its `before` and
`after`. A `removed` entry holds only `requirement`, as accepted before.

```json
{"command": "compare-requirements", "part_id": "A", "brief_digest": "…", "untrusted": "…",
 "unchanged": [{"requirement": {"id": "idea.repro.a.environment", "…": "…"}, "missing_sources": [],
                "recheckable_sources": [],
                "findings": [{"id": "F3", "source": "https://bugzilla.mozilla.org/page.cgi?id=bug-writing.html",
                              "claim": "…", "quote": "…"}]}],
 "unsupported": [], "new": [], "removed": [],
 "changed": [{"requirement": {"id": "idea.repro.a.no-guessing", "…": "…"}, "missing_sources": [],
              "recheckable_sources": [],
              "findings": [{"id": "F3", "…": "…"}], "differences": {"statement": {"before": "…", "after": "…"}}}]}
```
