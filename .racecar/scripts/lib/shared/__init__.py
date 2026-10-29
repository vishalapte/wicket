"""What more than one noun's checker needs. No noun owns it, so it has its own home.

`lib/<noun>/` holds one noun's implementation, and the test is the entry that runs it:
`lib/packaging/` is what `check_packaging.py` does, `lib/lexicon/` is what `lexicon.py`
does.

  `_root`          find the repo root; decide whether two paths are the same repository
  `_files`         list what git tracks, and scope it
  `_constants`     the delivered records and the names a repo may not carry
  `_readme_block`  splice a generated block between its markers, or leave the file alone
  `_slug`          the repo's own short name, and the brief that should carry it
  `_spec`          where a package keeps `surface.jsonl`, and its rows
  `_commits`       COMMITS.md's rules: type to bump, the subject, the version home
  `_agent_docs`    the filenames that carry agent instructions
  `_frontmatter`   the `---` block at the top of a markdown file, and what it declares
  `_markdown`      the one reader of a markdown file: frontmatter, code and text lines
  `_report`        a checker's findings, and printing them as `<prog>: <severity>: ...`
  `_switches`      how `RACECAR_STRICT` is read
  `_as_json`       `--json` for a verb that prints as it works
  `_imports`       walk the `@path` import chain an instruction file declares
  `_templates`     mirror a template tree into an output tree, substituting placeholders
  `_results`       read JUnit XML test records, and say what they add up to
  `_test_files`    which files are tests, per language, by each runner's naming rule
  `_python`        which Python runs a repo's own code: its virtualenv's, where it has one
  `error/`         the error packet: its schema as data, its types, and `packet()`

Each is a leaf: it imports only the standard library, except that any may import `_root` or
`_python`, which import nothing here. A module that needs another sibling belongs under its
noun.

`error/` is a package because it is copied whole into each adopter's `<pkg>/lib/error/`. Its
modules import each other and nothing else here.

Complexity: O(1) -- seventeen leaves and the rule that keeps them leaves
"""
