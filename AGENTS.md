Never run `git push --force` or `git push --force-with-lease` unless I explicitly request it and then confirm again after you explain that it will rewrite remote history.
Never run `git reset --hard` unless I explicitly request it in that turn.
Before starting a new task or switching to unrelated work, check whether the git worktree is dirty.
If the worktree is dirty but we are still iterating on the same feature, it is fine to keep going without committing.
If the worktree is dirty and the pending changes are not ready to commit, call that out and stop before starting unrelated work.
If the worktree is dirty and the pending changes are ready to commit, commit them first, then continue working from a clean tree on the new task.
