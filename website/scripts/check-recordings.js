#!/usr/bin/env node

const { createHash } = require("node:crypto");
const { existsSync, readFileSync, readdirSync, statSync } = require("node:fs");
const { basename, join, resolve } = require("node:path");

const allowStale = new Set(["1", "true", "yes", "on"]);
const allowValue = (process.env.ARBITER_WEBSITE_ALLOW_STALE_RECORDINGS || "")
  .trim()
  .toLowerCase();

if (allowStale.has(allowValue)) {
  console.warn(
    "warning: skipping website recording freshness checks because " +
      "ARBITER_WEBSITE_ALLOW_STALE_RECORDINGS is set",
  );
  process.exit(0);
}

const repoRoot = process.env.ARBITER_WEBSITE_RECORDING_REPO_ROOT
  ? resolve(process.env.ARBITER_WEBSITE_RECORDING_REPO_ROOT)
  : resolve(__dirname, "..", "..");
const recordingDir = join(repoRoot, "media", "recording-scripts");
const castsDir = join(repoRoot, "website", "static", "casts");
const audioDir = join(repoRoot, "website", "static", "audio", "casts");

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function relativePath(path) {
  return path.replace(`${repoRoot}/`, "");
}

function recordFailure(recordingId, message) {
  failed = true;
  console.error(`website recording check failed: ${recordingId}`);
  console.error(`  ${message}`);
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error(`could not read ${relativePath(path)}: ${error.message}`);
  }
}

const recordingIds = readdirSync(recordingDir)
  .filter((name) => name.endsWith(".md"))
  .map((name) => basename(name, ".md"))
  .filter((id) => statSync(join(recordingDir, `${id}.md`)).isFile())
  .sort();

let failed = false;
for (const recordingId of recordingIds) {
  const requiredArtifacts = [
    join(castsDir, `${recordingId}.cast`),
    join(castsDir, `${recordingId}.retimed.cast`),
    join(castsDir, `${recordingId}.timeline.jsonl`),
    join(castsDir, `${recordingId}.recording.json`),
  ];
  for (const artifact of requiredArtifacts) {
    if (!existsSync(artifact)) {
      recordFailure(recordingId, `missing artifact: ${relativePath(artifact)}`);
    }
  }
  const audioMetadataPath = join(audioDir, `${recordingId}.json`);
  if (existsSync(audioMetadataPath)) {
    const audioMetadata = readJson(audioMetadataPath);
    if (typeof audioMetadata.audio === "string") {
      const audioPath = join(repoRoot, audioMetadata.audio);
      if (!existsSync(audioPath)) {
        recordFailure(recordingId, `missing audio artifact: ${audioMetadata.audio}`);
      }
    }
  }
  const fingerprintPath = join(castsDir, `${recordingId}.recording.json`);
  if (!existsSync(fingerprintPath)) {
    continue;
  }

  const fingerprint = readJson(fingerprintPath);
  if (!Array.isArray(fingerprint.dependencies)) {
    recordFailure(
      recordingId,
      `invalid fingerprint dependencies: ${relativePath(fingerprintPath)}`,
    );
    continue;
  }

  const dependencyPaths = new Set(
    fingerprint.dependencies
      .map((dependency) => dependency?.path)
      .filter((path) => typeof path === "string"),
  );
  const recordingSource = relativePath(join(recordingDir, `${recordingId}.md`));
  if (!dependencyPaths.has(recordingSource)) {
    recordFailure(
      recordingId,
      `stale artifact: recording fingerprint does not include ${recordingSource}`,
    );
  }

  for (const dependency of fingerprint.dependencies) {
    if (
      !dependency ||
      typeof dependency.path !== "string" ||
      typeof dependency.sha256 !== "string"
    ) {
      recordFailure(
        recordingId,
        `invalid fingerprint dependency entry: ${relativePath(fingerprintPath)}`,
      );
      continue;
    }
    const dependencyPath = join(repoRoot, dependency.path);
    if (!existsSync(dependencyPath)) {
      recordFailure(recordingId, `missing dependency: ${dependency.path}`);
      continue;
    }
    const actual = sha256(dependencyPath);
    if (actual !== dependency.sha256) {
      recordFailure(
        recordingId,
        `stale artifact: ${dependency.path} changed since recording build`,
      );
    }
  }
}

if (failed) {
  console.error("");
  console.error(
    "Regenerate stale recordings before publishing the website, for example:",
  );
  console.error(
    "  cd .. && studio recording=install-and-bootstrap action=build",
  );
  console.error("");
  console.error(
    "To publish with a known temporary recording breakage, rerun with:",
  );
  console.error("  ARBITER_WEBSITE_ALLOW_STALE_RECORDINGS=1 npm run build");
  process.exit(1);
}
