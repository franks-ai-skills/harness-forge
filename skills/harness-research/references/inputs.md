# Input shapes for `accept`

Read this before writing the `--proposed` and `--choices` files of
`scripts/forge.py accept`. The example is minimal and valid; Forge's
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
