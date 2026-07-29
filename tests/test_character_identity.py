from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from assetclaw_matting.services.character_identity import (
    DEFAULT_CHARACTER_ALIASES,
    DEFAULT_CHARACTER_REFERENCE_ROOT,
    CharacterRegistry,
    CharacterRegistryError,
    CharacterResolutionStatus,
    list_characters,
    resolve_character,
)


CHARACTERS = (
    "aaron",
    "creamy",
    "danny",
    "heather",
    "huggy",
    "jessica",
    "juria",
    "marcus",
    "pinky",
    "susan",
    "tasha",
    "valentina",
)


def _reference_root(tmp_path: Path, characters: tuple[str, ...] = CHARACTERS) -> Path:
    root = tmp_path / "Charactor"
    root.mkdir()
    for index, character in enumerate(characters):
        (root / f"{character}.png").write_bytes(f"reference:{character}:{index}".encode("utf-8"))
    return root


def test_discovers_all_character_references_with_hashes_and_metadata(tmp_path: Path) -> None:
    root = _reference_root(tmp_path)

    registry = CharacterRegistry.discover(root)

    assert tuple(reference.canonical_id for reference in registry.references) == CHARACTERS
    assert registry.root == root.resolve()
    juria = registry.require("JURIA")
    assert juria.display_name == "Juria"
    assert juria.aliases == ("juria", "juriaback")
    assert juria.path == (root / "juria.png").resolve()
    assert juria.sha256 == hashlib.sha256((root / "juria.png").read_bytes()).hexdigest()
    assert juria.to_dict()["path"] == str((root / "juria.png").resolve())


def test_default_root_points_to_imageclip_character_assets() -> None:
    assert DEFAULT_CHARACTER_REFERENCE_ROOT == Path(r"C:\imageclip\Charactor")


def test_resolve_applies_nfkc_casefold_and_safe_separators(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    result = registry.resolve(r"C:\Feishu\ＪＥＳＳＩＣＡ-happy（1）.MP4")

    assert result.status is CharacterResolutionStatus.MATCHED
    assert result.canonical_id == "jessica"
    assert result.reference == registry.require("jessica")
    assert result.reason == "unique_boundary_safe_alias_match"


@pytest.mark.parametrize(
    "name",
    (
        "notaaron.mp4",
        "huggybear.zip",
        "susannah_idle.mov",
        "pre_jessicapost.png",
    ),
)
def test_resolve_never_guesses_from_substrings(tmp_path: Path, name: str) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    result = registry.resolve(name)

    assert result.status is CharacterResolutionStatus.UNRESOLVED
    assert result.reference is None
    assert result.candidates == ()
    assert result.reason == "no_boundary_safe_alias_match"


def test_resolve_returns_ambiguous_when_multiple_characters_are_named(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    result = registry.resolve(r"C:\Inbox\huggy\huggy-vs-jessica_idle.mp4")

    assert result.status is CharacterResolutionStatus.AMBIGUOUS
    assert result.reference is None
    assert tuple(candidate.canonical_id for candidate in result.candidates) == ("huggy", "jessica")
    assert result.reason == "multiple_character_aliases_matched"


def test_resolve_returns_unresolved_when_no_character_is_named(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    result = registry.resolve("2-3.mp4")

    assert result.status is CharacterResolutionStatus.UNRESOLVED
    assert result.canonical_id is None
    assert result.to_dict()["status"] == "unresolved"


def test_numbers_form_boundaries_without_enabling_letter_substrings(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    prefixed = registry.resolve("3pinky.png")
    suffixed = registry.resolve("pinky02_idle.mp4")
    unsafe = registry.resolve("notpinky02.mp4")

    assert prefixed.canonical_id == "pinky"
    assert suffixed.canonical_id == "pinky"
    assert unsafe.status is CharacterResolutionStatus.UNRESOLVED


def test_explicit_legacy_aliases_do_not_require_substring_matching(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(_reference_root(tmp_path))

    juria = registry.resolve("juriaback_1_.mp4")
    tasha = registry.resolve("newtashaback_1_.mp4")
    unsafe = registry.resolve("myjuriabackup.mp4")

    assert DEFAULT_CHARACTER_ALIASES["juria"] == ("juriaback",)
    assert juria.canonical_id == "juria"
    assert tasha.canonical_id == "tasha"
    assert unsafe.status is CharacterResolutionStatus.UNRESOLVED


def test_configured_alias_is_exact_and_boundary_checked(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(
        _reference_root(tmp_path),
        aliases={"tasha": ("newtashaback", "塔莎")},
        display_names={"tasha": "Tasha / 塔莎"},
    )

    compact = registry.resolve("newtashaback_1.mp4")
    chinese = registry.resolve("塔莎-开心.zip")
    unsafe = registry.resolve("xnewtashabacky.mp4")

    assert compact.canonical_id == "tasha"
    assert chinese.canonical_id == "tasha"
    assert unsafe.status is CharacterResolutionStatus.UNRESOLVED
    assert registry.require("tasha").display_name == "Tasha / 塔莎"
    assert registry.require("tasha").aliases == ("tasha", "Tasha / 塔莎", "newtashaback", "塔莎")


def test_colliding_explicit_aliases_produce_ambiguous_result(tmp_path: Path) -> None:
    registry = CharacterRegistry.discover(
        _reference_root(tmp_path, ("huggy", "jessica")),
        aliases={"huggy": ("hero",), "jessica": ("hero",)},
    )

    result = registry.resolve("hero_idle.mp4")

    assert result.status is CharacterResolutionStatus.AMBIGUOUS
    assert tuple(candidate.canonical_id for candidate in result.candidates) == ("huggy", "jessica")


def test_discovery_rejects_duplicate_normalized_ids(tmp_path: Path) -> None:
    root = tmp_path / "Charactor"
    root.mkdir()
    (root / "Jessica.png").write_bytes(b"first")
    (root / "ｊｅｓｓｉｃａ.jpg").write_bytes(b"second")

    with pytest.raises(CharacterRegistryError, match="duplicate canonical character id"):
        CharacterRegistry.discover(root)


def test_discovery_rejects_alias_for_unknown_character(tmp_path: Path) -> None:
    root = _reference_root(tmp_path, ("huggy",))

    with pytest.raises(CharacterRegistryError, match="unknown characters"):
        CharacterRegistry.discover(root, aliases={"missing": ("unknown",)})


def test_top_level_api_refreshes_catalog_and_combines_evidence(tmp_path: Path) -> None:
    root = _reference_root(tmp_path, ("huggy", "jessica"))

    catalog = list_characters(root)
    matched = resolve_character("generic.mp4", "folder/HUGGY-idle", root=root)
    ambiguous = resolve_character("huggy.mp4", "jessica", root=root)

    assert tuple(reference.canonical_id for reference in catalog) == ("huggy", "jessica")
    assert matched.canonical_id == "huggy"
    assert ambiguous.status is CharacterResolutionStatus.AMBIGUOUS
