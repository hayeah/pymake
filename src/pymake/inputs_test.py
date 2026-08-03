"""Tests for inputs.py — the Input contract and the value() built-in."""

import re
from pathlib import Path

import pytest

from pymake.inputs import ValueInput, input_defsite, is_input, value


class TestValueInput:
    def test_fingerprint_is_stable(self) -> None:
        assert value("a", "hello").fingerprint() == value("b", "hello").fingerprint()

    def test_fingerprint_changes_with_value(self) -> None:
        assert value("a", "one").fingerprint() != value("b", "two").fingerprint()

    def test_json_values_hash_independent_of_key_order(self) -> None:
        v1 = value("a", {"x": 1, "y": [1, 2]})
        v2 = value("b", {"y": [1, 2], "x": 1})
        assert v1.fingerprint() == v2.fingerprint()

    def test_str_and_bytes_and_json_are_distinct(self) -> None:
        # "1" the string, b"1" the bytes, and 1 the number are different
        # world states and must not collide.
        fps = {
            value("a", "1").fingerprint(),
            value("b", b"1").fingerprint(),
            value("c", 1).fingerprint(),
        }
        assert len(fps) == 3

    def test_non_jsonable_value_fails_at_construction(self) -> None:
        with pytest.raises(TypeError, match="JSON-able"):
            value("bad", object())

    def test_fingerprint_reads_current_state(self) -> None:
        # A mutable value is re-read on every fingerprint() call: mutation
        # after construction is visible (and mid-run mutation is what the
        # divergence warning exists to catch).
        config = {"opt": False}
        v = ValueInput("cfg", config)
        before = v.fingerprint()
        config["opt"] = True
        assert v.fingerprint() != before

    def test_empty_id_is_a_construction_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty id"):
            value("", "x")

    def test_defsite_points_at_the_caller(self) -> None:
        v = value("here", "x")
        site = input_defsite(v)
        assert site is not None
        assert Path(__file__).name in site
        assert re.search(r":\d+$", site)


class TestIsInput:
    def test_value_is_an_input(self) -> None:
        assert is_input(value("a", "x"))

    def test_paths_strings_and_callables_are_not(self) -> None:
        assert not is_input("src/main.c")
        assert not is_input(Path("src/main.c"))
        assert not is_input(lambda: None)

    def test_custom_object_with_fingerprint_is_an_input(self) -> None:
        class Custom:
            id = "custom"

            def fingerprint(self) -> str:
                return "fp"

        assert is_input(Custom())

    def test_fingerprint_bearing_object_without_id_still_detected(self) -> None:
        # Detection is by capability so registration can fail loudly on the
        # missing id instead of misreading the object as something else.
        class NoId:
            def fingerprint(self) -> str:
                return "fp"

        assert is_input(NoId())
