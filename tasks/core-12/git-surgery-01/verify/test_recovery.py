"""Hidden verification suite for git-surgery-01.

Everything here is checked against content, messages and authorship rather than against
commit names, because a legitimate recovery rewrites the committer and therefore the
commit name of every replayed commit. The two object names that are pinned are the ones
no correct answer touches: main's tip, and the blobs.

Three of these guard a plausible wrong answer:

  - test_the_recovered_commit_has_its_original_content compares blob names, so a commit
    whose subject is right and whose file was typed out again from the description fails.
    The same check covers the whole tree at that commit, which is what says the commit
    went back where it was rather than on the end.
  - test_the_recovered_commit_kept_its_author_and_date fails for anything authored by
    whoever ran the recovery. A rebase or a cherry pick keeps the author; a fresh commit
    does not, and neither does `git commit --amend` on the wrong commit.
  - test_main_did_not_move fails for the answer that rebases main onto the recovered tip,
    or force pushes the shape of the history around until the counts come out right.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

WORKSPACE = Path.cwd()
REPO = WORKSPACE / "repo"

MAIN_SHA = "abd75563f844b776897affe039ccc6fede42edc5"
"""main's tip as the builder leaves it. Every identity and date in that script is fixed,
so this name is the same on every run."""

FEATURE_COMMITS = [
    ("Add a retry wrapper around the fetch step", "Priya Raman", "priya@example.com", "2026-02-10T08:55:13+00:00"),
    ("Back off when the vendor answers 429", "Priya Raman", "priya@example.com", "2026-02-11T09:14:22+00:00"),
    ("Put the fetch path behind the retry wrapper", "Priya Raman", "priya@example.com", "2026-02-12T14:03:47+00:00"),
]
"""The three commits that were on feature, oldest first, with their authors."""

LOST = FEATURE_COMMITS[1]
"""The one the rebase dropped."""

LOST_TREE = {
    "README.md": "24e20a8e13b0d083ac06d9f803a23f0bf7361004",
    "src/csvio.py": "6f4723fb8b8dac801f7edb09a004bd096d952c30",
    "src/fetcher.py": "717206cb5e8b6d36cb249220f1bbbf8e9665cc2d",
    "src/retry.py": "6c68f1bec2a5ad18c9435e3ab42fae4d2123010e",
    "tests/test_backoff.py": "c3494418c77f050b93f047851e42fad2f7bc911b",
}
"""Every blob in the dropped commit's tree. A blob's name is the hash of its bytes, so
this is a byte for byte comparison of the file content, and fetcher.py being the version
from before the last commit is what says the recovered commit is in the middle of the
branch rather than on the end of it."""

TIP_TREE = {
    "README.md": "24e20a8e13b0d083ac06d9f803a23f0bf7361004",
    "src/csvio.py": "6f4723fb8b8dac801f7edb09a004bd096d952c30",
    "src/fetcher.py": "e43b3623d021042480cc2a5b0684bbecbe7a8142",
    "src/retry.py": "6c68f1bec2a5ad18c9435e3ab42fae4d2123010e",
    "tests/test_backoff.py": "c3494418c77f050b93f047851e42fad2f7bc911b",
}
"""Every blob at the restored tip of feature."""

IN_PROGRESS = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git against the repository. Never raises on a non-zero exit."""
    return subprocess.run(  # noqa: S603
        ["git", "-C", "repo", *args],  # noqa: S607
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def out(*args: str) -> str:
    """Run git and return stdout, failing the test with stderr when it exits non-zero."""
    proc = git(*args)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr.strip()}"
    return proc.stdout.strip()


def log_fields(rev_range: str, fields: str) -> list[str]:
    """Return one formatted line per commit in a range, oldest first."""
    return [line for line in out("log", "--reverse", f"--format={fields}", rev_range).splitlines()]


def tree_of(rev: str) -> dict[str, str]:
    """Return {path: blob name} for every file in a revision's tree."""
    listing: dict[str, str] = {}
    for line in out("ls-tree", "-r", rev).splitlines():
        meta, path = line.split("\t", 1)
        _mode, _kind, blob = meta.split()
        listing[path] = blob
    return listing


def lost_commit() -> str:
    """The name of the commit on feature carrying the dropped commit's subject."""
    matches = [
        line.split(" ", 1)[0]
        for line in out("log", "--format=%H %s", "main..feature").splitlines()
        if line.split(" ", 1)[1:] == [LOST[0]]
    ]
    assert matches, f"no commit on feature has the subject {LOST[0]!r}"
    assert len(matches) == 1, f"{len(matches)} commits on feature claim to be {LOST[0]!r}"
    return matches[0]


def test_the_repository_is_where_it_was() -> None:
    assert REPO.is_dir(), "repo/ is gone"
    assert (REPO / ".git").exists(), "repo/ is no longer a git repository"
    assert out("rev-parse", "--abbrev-ref", "main") == "main", "there is no main branch"
    assert out("rev-parse", "--abbrev-ref", "feature") == "feature", "there is no feature branch"


def test_feature_has_the_three_commits_in_order() -> None:
    count = out("rev-list", "--count", "main..feature")
    subjects = log_fields("main..feature", "%s")
    assert count == "3", (
        f"feature has {count} commits above main, want 3. Subjects, oldest first: {subjects}"
    )
    assert subjects == [commit[0] for commit in FEATURE_COMMITS], (
        "feature's commits are not the three that were on it, in the order they were "
        f"written. Found, oldest first: {subjects}"
    )


def test_feature_sits_on_top_of_main() -> None:
    assert git("merge-base", "--is-ancestor", "main", "feature").returncode == 0, (
        "main is not an ancestor of feature, so the branch was not rebased onto it"
    )
    base = git("rev-parse", "--verify", "feature~3")
    assert base.returncode == 0, "feature does not have three commits above anything"
    assert base.stdout.strip() == out("rev-parse", "main"), (
        "the third commit back from feature's tip is not main's tip, so there is "
        "something else in between"
    )


def test_the_history_is_linear() -> None:
    merges = out("rev-list", "--merges", "main..feature")
    assert merges == "", (
        "there are merge commits between main and feature. Merging the recovered commit "
        f"in is not restoring the branch, it is a different history: {merges.splitlines()}"
    )


def test_the_recovered_commit_has_its_original_content() -> None:
    assert tree_of(lost_commit()) == LOST_TREE, (
        "the recovered commit's tree is not the tree it had. The content has to come out "
        "of the object database, not be typed out again from the description of it"
    )


def test_the_recovered_commit_kept_its_author_and_date() -> None:
    got = out("log", "-1", "--format=%an|%ae|%aI", lost_commit())
    want = f"{LOST[1]}|{LOST[2]}|{LOST[3]}"
    assert got == want, (
        f"the recovered commit is authored {got}, want {want}. A rebase and a cherry pick "
        "both keep the original author and author date; a commit written from scratch "
        "carries whoever ran the recovery"
    )


def test_every_commit_on_feature_kept_its_author() -> None:
    got = log_fields("main..feature", "%an|%ae|%aI")
    want = [f"{commit[1]}|{commit[2]}|{commit[3]}" for commit in FEATURE_COMMITS]
    assert got == want, f"feature's authorship is {got}, want {want}"


def test_main_did_not_move() -> None:
    assert out("rev-parse", "main") == MAIN_SHA, (
        f"main is at {out('rev-parse', 'main')}, and it was at {MAIN_SHA}. Nothing about "
        "recovering a commit on feature involves rewriting main"
    )


def test_the_files_at_the_tip_are_exactly_right() -> None:
    assert tree_of("feature") == TIP_TREE, (
        "the files at feature's tip are not the files that were there. Every blob is "
        "compared by name, which is the hash of its bytes"
    )


def test_the_working_tree_is_clean_and_nothing_is_half_done() -> None:
    status = out("status", "--porcelain")
    assert status == "", f"the working tree is not clean:\n{status}"

    head = git("symbolic-ref", "-q", "HEAD")
    assert head.returncode == 0, (
        "HEAD is detached. Recovery is not finished until a branch points at the result"
    )

    git_dir = Path(out("rev-parse", "--absolute-git-dir"))
    left_behind = [name for name in IN_PROGRESS if (git_dir / name).exists()]
    assert not left_behind, f"an operation was left in progress: {left_behind}"
