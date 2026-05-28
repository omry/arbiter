# Local Agent Instructions

Prefer to use AWD for all but the most trivial prompts.

If you used AWD in a meaningful way, provide a short summary at the end of the
response describing how you used it and how many steps it covered.

This checkout is a Sapling repository. Use Sapling commands such as
`sl status` to inspect repository state.

Never force-push or otherwise rewrite remote history unless I explicitly
request it and then confirm again after you explain the effect.

Never hard-reset or otherwise discard uncommitted work unless I explicitly
request it in that turn.

Before starting a new task or switching to unrelated work, check whether the
Sapling worktree is dirty. If the worktree is dirty but we are still iterating
on the same feature, it is fine to keep going without committing. If the
worktree is dirty and the pending changes are not ready to commit, call that
out and stop before starting unrelated work. If the worktree is dirty and the
pending changes are ready to commit, commit them first, then continue working
from a clean tree on the new task.
