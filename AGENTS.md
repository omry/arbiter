## Local Instructions

If `LOCAL-AGENTS.md` exists at the repository root, treat it as additional
local instructions for this checkout. Use it for machine- or user-specific
preferences that should not be committed to the repository.

This is a Sapling repository. Use `sl status` to check the worktree state; do not assume plain `git status` works in this checkout.
Never run `git push --force` or `git push --force-with-lease` unless I explicitly request it and then confirm again after you explain that it will rewrite remote history.
Never run `git reset --hard` unless I explicitly request it in that turn.
Before starting a new task or switching to unrelated work, check whether the Sapling worktree is dirty.
If the Sapling worktree is dirty but we are still iterating on the same feature, it is fine to keep going without committing.
If the Sapling worktree is dirty and the pending changes are not ready to commit, call that out and stop before starting unrelated work.
If the Sapling worktree is dirty and the pending changes are ready to commit, commit them first, then continue working from a clean tree on the new task.
