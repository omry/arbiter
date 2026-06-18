Fixed Docker helper server readiness checks to call `arbiter info server`
instead of bare `arbiter info`, and let scratch-space deployments rebuild
local checkout wheels through `ARBITER_REPO_ROOT`.
