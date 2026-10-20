# Arbiter Website

This directory contains the Docusaurus website.

```bash
npm install --global npm@11.9.0
npm ci
npm run start
npm run build
```

`npm run build` runs a cheap fingerprint check before Docusaurus so published
terminal recordings cannot silently drift from their source scripts. If a
recording is stale, regenerate it first:

```bash
cd .. && studio recording=install-and-bootstrap action=build
```

For a known temporary breakage, such as an in-progress deployment-flow rewrite,
you can explicitly bypass the recording freshness gate:

```bash
ARBITER_WEBSITE_ALLOW_STALE_RECORDINGS=1 npm run build
```

Terminal recordings live under `static/casts/`. Rendered images for docs live
under `static/img/casts/`.

## Dependency maintenance

Use Node 24 and npm 11.9.0, matching the project declarations. Install the
pinned npm version with `npm install --global npm@11.9.0`, then use `npm ci`.

The [Node maintenance workflows](https://github.com/hydra-ecosystem/github-actions/blob/main/docs/node-maintenance.md)
check security every Monday at 02:00 UTC and propose routine upgrades quarterly,
on January 1, April 1, July 1, and October 1 at 02:00 UTC. New releases have a
ten-day cooldown. The repository-root `.github/node-maintenance.json` points to the shared
policy; edit its overrides to change project policy, and keep caller cron entries
in `.github/workflows/dependency-audit.yml` consistent with the resolved schedule.

To run an audit sooner, open **Actions → Node dependency maintenance → Run workflow**
on `main` and select `security` or `upgrades`. The action validates dependency
changes before opening or updating one maintenance PR. A toolchain or schedule
mismatch fails preflight and opens or updates a configuration issue. The action
does not rewrite toolchain declarations.

Publication uses the built-in token and requires **Allow GitHub Actions to create
and approve pull requests** in **Settings → Actions → General → Workflow
permissions**. Default token permissions remain read-only. The workflow does
not approve or merge its own PRs.
