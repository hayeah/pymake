"""Unit tests for the gitignore parser."""

from __future__ import annotations

from pathlib import Path

from pymake.lstree.gitignore import GitIgnore


def _matcher(text: str) -> GitIgnore:
    return GitIgnore.from_text(text)


class TestBasicPatterns:
    def test_blank_and_comments_ignored(self) -> None:
        gi = _matcher("\n# comment\n  \nfoo\n")
        assert len(gi.rules) == 1

    def test_bare_name_matches_any_depth(self) -> None:
        gi = _matcher("node_modules\n")
        assert gi.is_ignored("node_modules", True)
        assert gi.is_ignored("node_modules/foo.js", False)
        assert gi.is_ignored("src/node_modules", True)
        assert gi.is_ignored("src/node_modules/foo.js", False)
        assert not gi.is_ignored("src/foo.js", False)

    def test_bare_name_does_not_match_substring(self) -> None:
        gi = _matcher("log\n")
        assert gi.is_ignored("log", False)
        assert gi.is_ignored("src/log", False)
        assert not gi.is_ignored("logger.py", False)
        assert not gi.is_ignored("src/mylog.txt", False)

    def test_anchored_leading_slash(self) -> None:
        gi = _matcher("/build\n")
        assert gi.is_ignored("build", True)
        assert gi.is_ignored("build/output.o", False)
        assert not gi.is_ignored("src/build", True)

    def test_anchored_interior_slash(self) -> None:
        gi = _matcher("docs/private\n")
        assert gi.is_ignored("docs/private", True)
        assert gi.is_ignored("docs/private/secret.md", False)
        assert not gi.is_ignored("nested/docs/private", True)


class TestDirOnly:
    def test_trailing_slash_means_dir_only(self) -> None:
        gi = _matcher("drafts/\n")
        assert gi.is_ignored("drafts", True)
        assert not gi.is_ignored("drafts", False)  # a file named "drafts" is OK

    def test_nested_dir_match(self) -> None:
        gi = _matcher("tmp/\n")
        assert gi.is_ignored("tmp", True)
        assert gi.is_ignored("src/tmp", True)


class TestGlobs:
    def test_star_star_leading(self) -> None:
        gi = _matcher("**/foo.txt\n")
        assert gi.is_ignored("foo.txt", False)
        assert gi.is_ignored("a/foo.txt", False)
        assert gi.is_ignored("a/b/foo.txt", False)

    def test_star_star_trailing(self) -> None:
        # git spec: `logs/**` matches everything *inside* logs, not logs
        # itself. Walker still prunes logs's children correctly.
        gi = _matcher("logs/**\n")
        assert gi.is_ignored("logs/a.log", False)
        assert gi.is_ignored("logs/sub/a.log", False)
        assert not gi.is_ignored("logs", True)

    def test_extension_glob(self) -> None:
        gi = _matcher("*.pyc\n")
        assert gi.is_ignored("foo.pyc", False)
        assert gi.is_ignored("sub/bar.pyc", False)
        assert not gi.is_ignored("foo.py", False)

    def test_question_mark(self) -> None:
        gi = _matcher("log?.txt\n")
        assert gi.is_ignored("log1.txt", False)
        assert gi.is_ignored("logA.txt", False)
        assert not gi.is_ignored("log.txt", False)
        assert not gi.is_ignored("log12.txt", False)

    def test_character_class(self) -> None:
        gi = _matcher("[abc].txt\n")
        assert gi.is_ignored("a.txt", False)
        assert gi.is_ignored("b.txt", False)
        assert not gi.is_ignored("d.txt", False)

    def test_character_class_negated(self) -> None:
        gi = _matcher("[!abc].txt\n")
        assert gi.is_ignored("d.txt", False)
        assert not gi.is_ignored("a.txt", False)


class TestNegation:
    def test_negation_reincludes(self) -> None:
        gi = _matcher("*.log\n!important.log\n")
        assert gi.is_ignored("debug.log", False)
        assert not gi.is_ignored("important.log", False)

    def test_last_rule_wins(self) -> None:
        gi = _matcher("!foo\nfoo\n")
        # positive rule after negation should re-ignore
        assert gi.is_ignored("foo", False)

    def test_negation_then_reignore(self) -> None:
        gi = _matcher("*.log\n!keep.log\nkeep.log\n")
        assert gi.is_ignored("keep.log", False)


class TestFileParsing:
    def test_empty_when_missing(self, tmp_path: Path) -> None:
        gi = GitIgnore.parse(tmp_path)
        assert gi.rules == []
        assert not gi.is_ignored("anything", False)

    def test_parse_real_file(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text(
            "# this is a comment\nnode_modules/\n*.pyc\n!keep.pyc\n"
        )
        gi = GitIgnore.parse(tmp_path)
        assert len(gi.rules) == 3
        assert gi.is_ignored("node_modules", True)
        assert gi.is_ignored("foo.pyc", False)
        assert not gi.is_ignored("keep.pyc", False)
