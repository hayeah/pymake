"""Tests for the git input, against real temp repos."""

import subprocess
from pathlib import Path

import pytest

from pymake.inputs import GitInput, git


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "master")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    return repo


def _commit_file(repo: Path, rel: str, content: str, message: str = "c") -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", message)


class TestGitInputCleanTree:
    def test_clean_fingerprint_is_the_commit_id(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "int main() {}\n")
        head = _run(repo, "rev-parse", "HEAD")

        fp = git("native-sources", repo).fingerprint()
        assert fp == f"commit:{head}"
        # Idempotent read: same world, same fingerprint.
        assert git("native-sources2", repo).fingerprint() == fp

    def test_new_commit_changes_the_fingerprint(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        before = git("src", repo).fingerprint()
        _commit_file(repo, "main.c", "v2\n")
        assert git("src", repo).fingerprint() != before


class TestGitInputDirt:
    def test_tracked_edit_dirties_then_settles(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        clean = git("src", repo).fingerprint()

        (repo / "main.c").write_text("edited\n")
        dirty = git("src", repo).fingerprint()
        assert dirty != clean
        assert "dirty:" in dirty

        # Returning to clean settles back to the commit id — even though
        # the restore bumps the file's mtime.
        _run(repo, "checkout", "--", ".")
        assert git("src", repo).fingerprint() == clean

    def test_edits_retrigger_while_dirty(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        (repo / "main.c").write_text("edit one\n")
        first = git("src", repo).fingerprint()
        (repo / "main.c").write_text("edit two, longer\n")
        assert git("src", repo).fingerprint() != first

    def test_untracked_files_count_as_dirt(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        clean = git("src", repo).fingerprint()

        (repo / "new_source.c").write_text("fresh\n")
        dirty = git("src", repo).fingerprint()
        assert dirty != clean
        assert dirty.endswith(":1u")

        (repo / "new_source.c").unlink()
        assert git("src", repo).fingerprint() == clean

    def test_deleted_tracked_file_counts_as_dirt(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        clean = git("src", repo).fingerprint()
        (repo / "main.c").unlink()
        assert git("src", repo).fingerprint() != clean

    def test_gitignored_residue_never_enters_the_fingerprint(
        self, tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, ".gitignore", "target/\n")
        clean = git("src", repo).fingerprint()

        (repo / "target").mkdir()
        (repo / "target" / "junk.o").write_text("build residue")
        assert git("src", repo).fingerprint() == clean

    def test_pymake_state_is_never_dirt(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        clean = git("src", repo).fingerprint()

        state = repo / ".pymake" / "state"
        state.mkdir(parents=True)
        (state / "build.json").write_text("{}")
        assert git("src", repo).fingerprint() == clean


class TestGitInputPaths:
    def test_out_of_scope_changes_do_not_retrigger(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "core/lib.c", "core v1\n")
        _commit_file(repo, "docs/readme.md", "docs v1\n")

        scoped = git("core-src", repo, paths=["core"])
        before = scoped.fingerprint()

        # A commit touching only docs/ does not move the scoped tree.
        _commit_file(repo, "docs/readme.md", "docs v2\n")
        assert scoped.fingerprint() == before

        # A dirty edit outside the scope does not count either.
        (repo / "docs" / "readme.md").write_text("dirty docs\n")
        assert scoped.fingerprint() == before

    def test_in_scope_changes_retrigger(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "core/lib.c", "core v1\n")
        scoped = git("core-src", repo, paths=["core"])
        before = scoped.fingerprint()

        (repo / "core" / "lib.c").write_text("dirty core\n")
        dirty = scoped.fingerprint()
        assert dirty != before

        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "core v2")
        committed = scoped.fingerprint()
        assert committed != before
        assert committed != dirty
        assert "dirty:" not in committed

    def test_glob_pathspec_scopes_by_suffix(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "ui/app.ts", "ts v1\n")
        _commit_file(repo, "ui/app.css", "css v1\n")

        scoped = git("ui-ts", repo, paths=[":(glob)ui/**/*.ts"])
        before = scoped.fingerprint()

        _commit_file(repo, "ui/app.css", "css v2\n")
        assert scoped.fingerprint() == before

        _commit_file(repo, "ui/app.ts", "ts v2\n")
        assert scoped.fingerprint() != before


class TestGitInputRef:
    def test_ref_follows_the_branch_tip(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "proto/defs.proto", "v1\n")

        pinned = git("proto-defs", repo, ref="master", paths=["proto"])
        before = pinned.fingerprint()

        # Working-tree dirt is not published state: no retrigger.
        (repo / "proto" / "defs.proto").write_text("dirty\n")
        assert pinned.fingerprint() == before
        _run(repo, "checkout", "--", ".")

        # The branch moving IS a change.
        _commit_file(repo, "proto/defs.proto", "v2\n")
        assert pinned.fingerprint() != before

    def test_unresolvable_ref_is_a_loud_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.c", "v1\n")
        with pytest.raises(ValueError, match="cannot resolve ref"):
            git("src", repo, ref="no-such-branch").fingerprint()


class TestGitInputEdgeCases:
    def test_not_a_repository_is_a_loud_error(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(ValueError, match="not a git repository"):
            git("src", plain).fingerprint()

    def test_repo_with_no_commits_yet(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        empty = git("src", repo).fingerprint()
        assert "no-commit" in empty

        (repo / "main.c").write_text("v1\n")
        assert git("src", repo).fingerprint() != empty

    def test_empty_id_is_a_construction_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty id"):
            GitInput("", tmp_path)

    def test_defsite_is_captured(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        g = git("src", repo)
        assert g.defsite is not None
        assert Path(__file__).name in g.defsite


class TestGitInputThroughExecutor:
    def test_git_gated_task_settles_and_retriggers(self, tmp_path: Path) -> None:
        from pymake import Executor, TaskRegistry

        repo = _make_repo(tmp_path)
        _commit_file(repo, "core/lib.c", "v1\n")
        runs: list[int] = []

        def make_executor() -> Executor:
            registry = TaskRegistry()
            registry.register(
                lambda: runs.append(1),
                name="build_native",
                inputs=[git("native-sources", repo, paths=["core"])],
            )
            return Executor(registry, verbose=False, state_dir=tmp_path / "state")

        make_executor().run("build_native")
        assert len(runs) == 1

        make_executor().run("build_native")
        assert len(runs) == 1  # clean tree: one rev-parse, no rebuild

        (repo / "core" / "lib.c").write_text("v2 edited\n")
        make_executor().run("build_native")
        assert len(runs) == 2
