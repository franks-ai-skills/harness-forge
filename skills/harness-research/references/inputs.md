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
{"command": "compare-requirements", "part_id": "A", "brief_digest": "…", "untrusted": "…", "compact": false,
 "unchanged": [{"requirement": {"id": "idea.repro.a.environment", "…": "…"}, "missing_sources": [],
                "recheckable_sources": [],
                "findings": [{"id": "F3", "source": "https://bugzilla.mozilla.org/page.cgi?id=bug-writing.html",
                              "claim": "…", "quote": "…"}],
                "item_id": "unchanged:idea.repro.a.environment", "display": "…", "digest": "…"}],
 "unsupported": [], "new": [], "removed": [],
 "changed": [{"requirement": {"id": "idea.repro.a.no-guessing", "…": "…"}, "missing_sources": [],
              "recheckable_sources": [],
              "findings": [{"id": "F3", "…": "…"}], "differences": {"statement": {"before": "…", "after": "…"}},
              "item_id": "changed:idea.repro.a.no-guessing", "display": "…", "digest": "…"}],
 "group": {"item_id": "group:unchanged", "rule_ids": ["idea.repro.a.environment"], "display": "…", "digest": "…"}}
```

Each entry also has `item_id` (`<group>:<rule id>`, so a rule of an older
revision and a new proposal with the same id stay apart), `display`
(the Markdown to show as it is: every field acceptance keeps, the
differences, and each finding fenced as untrusted) and `digest`. With
unchanged entries, `group` holds the item `group:unchanged`, its
`rule_ids`, `display` and `digest`, for one question about all of
them; otherwise `group` is null. When everything would not fit in one
output, `compact` is true and each entry holds only `item_id`,
`rule_id` and `digest`: fetch each item whole with `--item <item_id>`
(also `--item group:unchanged`). `--edited <file>`, in the shape of
`--proposed`, returns `edited` with each edited rule's `display` and
`digest`, to show an edit back before it is recorded.

`accept` needs `--relay`, written after the user answered each item:

```json
{"run_id": "<run id>", "part_id": "A", "decisions": [
  {"choice": "accept", "item_ids": ["new:idea.repro.a.environment"], "displayed_digest": "<its digest>"},
  {"choice": "reject", "item_ids": ["new:idea.repro.a.no-guessing"], "displayed_digest": "<its digest>"},
  {"choice": "edit", "item_ids": ["changed:idea.repro.a.steps"], "displayed_digest": "<its digest>",
   "edited_digest": "<the digest --edited returned>"},
  {"choice": "drop", "item_ids": ["removed:idea.repro.a.logs"], "displayed_digest": "<its digest>"}]}
```

`accept` and `reject` agree with the choices file, `edit` with its
`{"edit": …}`, `drop` names a `removed` item, and the unchanged group is
accepted as `{"choice": "accept", "item_ids": ["group:unchanged"], …}`.
Every requirement accepted before needs a decision: accept, edit or
reject when it is still proposed, drop when it is not. A new proposal
without an answer stays pending. `accept` refuses, writing nothing, when
a digest is stale, a choice contradicts the choices file, an edit is not
the one shown back, or an earlier requirement has no decision.
