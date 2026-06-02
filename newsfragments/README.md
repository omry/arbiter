# News Fragments

Use towncrier fragments here for user-visible changes to the default
`arbiter-suite` all-in-one meta package. Package-specific release notes live in
that package's own `newsfragments/` directory.

Fragment filenames use:

```text
<id>.<type>.md
```

Use a GitHub issue or PR number as the id when one exists. Use the orphan
prefix for changes that do not have one, for example:

```text
+initial-release.feature.md
```

Common fragment types are `feature`, `bugfix`, `doc`, `removal`, and `misc`.

Dev package releases do not consume these fragments. Before a final release,
preview and build the notes with:

```bash
.venv/bin/python -m towncrier build --draft --version 0.9.0
.venv/bin/python -m towncrier build --yes --version 0.9.0
```
