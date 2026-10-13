Bound total retained artifact storage across plugins to a configurable 1 GiB
default using `arbiter.limits.max_artifact_storage_bytes`. Count payload and
metadata bytes, reclaim expired or incomplete artifacts before admission, and
coordinate concurrent creators across processes. Reject creations that cannot
fit with HTTP 503, `resource_exhausted`, and transient retry guidance; roll back
failed writes so they do not strand capacity.
