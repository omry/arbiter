# Arbiter Website

This directory contains the Docusaurus website.

```bash
npm install
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
