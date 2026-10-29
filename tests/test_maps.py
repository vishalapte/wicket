"""The shared map engine: pure edits over ``{primary: [items]}`` and the whole-file write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wicket.maps import (
    add_items,
    create_entry,
    read_map,
    remove_items,
    render_map,
    update_entry,
    write_maps,
)


def test_read_map_is_empty_when_the_file_is_absent(tmp_path: Path) -> None:
    assert read_map(tmp_path / "nope.json") == {}


def test_read_map_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        read_map(path)


def test_render_map_is_deterministic() -> None:
    assert render_map({"b": ["y", "x"], "a": ["z"]}) == (
        json.dumps({"a": ["z"], "b": ["x", "y"]}, indent=2) + "\n"
    )


def test_create_entry_refuses_an_existing_primary() -> None:
    with pytest.raises(ValueError, match="already exists"):
        create_entry({"travel": ["a"]}, "travel", ["b"])


def test_update_entry_adds_and_removes() -> None:
    assert update_entry({"t": ["a", "b"]}, "t", add=["c"], remove=["a"]) == {
        "t": ["b", "c"]
    }


def test_update_entry_refuses_an_unknown_primary() -> None:
    with pytest.raises(ValueError, match="not found"):
        update_entry({}, "t", add=["a"], remove=[])


def test_add_items_creates_or_merges() -> None:
    assert add_items({}, "t", ["b", "a"]) == {"t": ["a", "b"]}
    assert add_items({"t": ["a"]}, "t", ["a", "c"]) == {"t": ["a", "c"]}


def test_remove_items_skips_what_is_already_gone() -> None:
    assert remove_items({"t": ["a", "b"]}, "t", ["a", "zzz"]) == {"t": ["b"]}
    assert remove_items({}, "t", ["a"]) == {}


def test_remove_items_drops_an_emptied_or_named_primary() -> None:
    assert remove_items({"t": ["a"], "u": ["b"]}, "t", ["a"]) == {"u": ["b"]}
    assert remove_items({"t": ["a"], "u": ["b"]}, "t", None) == {"u": ["b"]}


def test_write_maps_replaces_each_file_and_leaves_no_temp(tmp_path: Path) -> None:
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    one.write_text("{}\n", encoding="utf-8")
    write_maps({one: {"t": ["a"]}, two: {"u": ["b"]}})
    assert json.loads(one.read_text()) == {"t": ["a"]}
    assert json.loads(two.read_text()) == {"u": ["b"]}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one.json", "two.json"]


def test_write_maps_restores_the_first_file_when_the_second_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    one.write_text('{"old": []}\n', encoding="utf-8")
    real = Path.replace
    calls: list[Path] = []

    def replace(self: Path, target: Path) -> Path:
        calls.append(Path(target))
        if Path(target) == two:
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="disk full"):
        write_maps({one: {"new": ["a"]}, two: {"u": ["b"]}})
    assert calls == [one, two]
    assert one.read_text() == '{"old": []}\n'
    assert not two.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one.json"]
