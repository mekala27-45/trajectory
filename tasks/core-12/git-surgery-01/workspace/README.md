# Handover note

`repo/` is the feedloader checkout. `feature` was three commits of Priya's work on the
vendor's rate limiting, branched off `main` before the CRLF change landed on it.

I rebased it onto `main` this morning and deleted a line out of the todo list by mistake.
The branch came back with two commits on it instead of three. The one in the middle, the
backoff work, is not on the branch any more.

Then I made it worse. While tidying up I ran

    git reflog expire --expire-unreachable=now --all

so the reflog no longer has the entry for where `feature` used to point. What I have not
done is run a garbage collection, and I have not cloned, pushed or fetched anything since,
so whatever is left of that commit is still inside `repo/.git`. There is one copy of it.
Please read before you write.

There is no remote, no stash and no second checkout. Priya is out until Thursday, and the
commit is not on her machine either: she pushed it to this checkout and deleted her branch.

What I need back: `feature` with its three commits, in the order they were written, sitting
on top of `main` where the rebase was trying to put them. Priya's commit has to still be
her commit, with the same content, the same message, her name and her date on it. `main`
has not moved and should not.
