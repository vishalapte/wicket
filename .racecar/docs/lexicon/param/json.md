---
name: json
kind: param
domain: [racecar]
type: boolean
defined: true
summary: emit the report as JSON on stdout instead of prose
pnode: [README.md]
bearing: contract
---

# json

Emit the command's report as JSON on stdout rather than prose. Every verb takes it: the form
racecar builds a command in gives each verb `--json`, so it is delivered.

**It changes the rendering and never the work.** A command with `--json` does the same work,
decides the same way, and exits the same code.

**Not a substitute for a declared output shape.** What the JSON contains is the leaf's own
`output()`, rendered on that verb's node in the nounspace.
