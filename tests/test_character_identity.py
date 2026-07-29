from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from assetclaw_matting.config import Settings
from assetclaw_matting.services.character_identity import (
    DEFAULT_CHARACTER_ALIASES,
    DEFAULT_CHARACTER_EMOJI_REFERENCE_ROOT,
    DEFAULT_CHARACTER_FULL_REFERENCE_ROOT,
    DEFAULT_CHARACTER_REFERENCE_ROOT,
    FULL_CHARACTER_REFERENCE_SIZE,
    HALF_CHARACTER_REFERENCE_SIZE,
    CharacterProfileError,
    CharacterReferenceCatalog,
    CharacterReferenceVariant,
    CharacterRegistry,
    CharacterRegistryError,
    CharacterResolutionStatus,
    character_profile_for_output_size,
    list_characters,
    normalize_character_output_size,
    normalize_character_profile,
    normalize_character_reference_variant,
    resolve_character,
    resolve_character_profile,
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


def _variant_roots(
    tmp_path: Path,
    *,
    full_characters: tuple[str, ...] = ("huggy", "jessica"),
    half_characters: tuple[str, ...] = ("jessica", "pinky"),
) -> tuple[Path, Path]:
    full_root = tmp_path / "CharactorFull"
    half_root = tmp_path / "CharactorEmoji"
    full_root.mkdir()
    half_root.mkdir()
    for index, character in enumerate(full_characters):
        Image.new("RGBA", FULL_CHARACTER_REFERENCE_SIZE, (index + 1, 2, 3, 255)).save(
            full_root / f"{character}.png"
        )
    for index, character in enumerate(half_characters):
        Image.new("RGBA", HALF_CHARACTER_REFERENCE_SIZE, (4, index + 1, 6, 255)).save(
            half_root / f"{character}.png"
        )
    return full_root, half_root


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
    assert DEFAULT_CHARACTER_FULL_REFERENCE_ROOT == Path(r"C:\imageclip\CharactorFull")
    assert DEFAULT_CHARACTER_EMOJI_REFERENCE_ROOT == Path(r"C:\imageclip\CharactorEmoji")


def test_settings_derive_both_variant_roots_from_pipeline_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "bot"
    pipeline = tmp_path / "imageclip"

    configured = Settings(assetclaw_root=checkout, matting_pipeline_repo_dir=pipeline)

    assert configured.cherry_character_full_reference_dir == pipeline / "CharactorFull"
    assert configured.cherry_character_emoji_reference_dir == pipeline / "CharactorEmoji"
    assert configured.cherry_character_half_reference_dir == pipeline / "CharactorEmoji"


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


def test_profile_and_output_size_normalization_is_strict() -> None:
    assert normalize_character_reference_variant("FULL") is CharacterReferenceVariant.FULL
    assert normalize_character_reference_variant(CharacterReferenceVariant.HALF) is CharacterReferenceVariant.HALF
    assert normalize_character_profile("full") == "full"
    assert normalize_character_profile("emoji") == "half"
    assert normalize_character_profile("SQUARE") == "half"
    assert normalize_character_output_size("384", 512) == (384, 512)
    assert character_profile_for_output_size(256, 256) == "half"
    assert resolve_character_profile(profile="half", width=256, height=256) == "half"

    with pytest.raises(CharacterProfileError, match="unsupported character reference variant"):
        normalize_character_reference_variant("emoji")
    with pytest.raises(CharacterProfileError, match="unsupported character profile"):
        normalize_character_profile("auto")
    with pytest.raises(CharacterProfileError, match="unsupported character output size"):
        character_profile_for_output_size(512, 512)
    with pytest.raises(CharacterProfileError, match="both output width and height"):
        resolve_character_profile(width=384)
    with pytest.raises(CharacterProfileError, match="conflicts with output size"):
        resolve_character_profile(profile="full", width=256, height=256)


def test_dual_catalog_resolves_union_but_never_falls_back_across_profiles(tmp_path: Path) -> None:
    full_root, half_root = _variant_roots(tmp_path)

    catalog = CharacterReferenceCatalog.discover(full_root, half_root)

    assert tuple(reference.canonical_id for reference in catalog.references) == (
        "huggy",
        "jessica",
        "pinky",
    )
    assert catalog.resolve("huggy_idle.mp4").canonical_id == "huggy"
    assert catalog.resolve("pinky_expression.zip").canonical_id == "pinky"
    assert catalog.available_profiles("jessica") == ("full", "half")
    assert catalog.available_profiles("huggy") == ("full",)
    assert catalog.get("huggy", "half") is None
    assert catalog.get("pinky", "full") is None
    with pytest.raises(CharacterRegistryError, match="no half reference"):
        catalog.require("huggy", "half")
    with pytest.raises(CharacterRegistryError, match="cross-profile fallback is forbidden"):
        catalog.require("pinky", "full")

    full_jessica = catalog.require("jessica", "full")
    half_jessica = catalog.require("jessica", "emoji")
    assert full_jessica.path == (full_root / "jessica.png").resolve()
    assert full_jessica.variant is CharacterReferenceVariant.FULL
    assert (full_jessica.width, full_jessica.height) == FULL_CHARACTER_REFERENCE_SIZE
    assert half_jessica.path == (half_root / "jessica.png").resolve()
    assert half_jessica.variant is CharacterReferenceVariant.HALF
    assert (half_jessica.width, half_jessica.height) == HALF_CHARACTER_REFERENCE_SIZE


def test_dual_catalog_validates_actual_reference_pixels(tmp_path: Path) -> None:
    full_root, half_root = _variant_roots(tmp_path)
    Image.new("RGBA", (512, 512), (0, 0, 0, 0)).save(half_root / "jessica.png")

    with pytest.raises(CharacterRegistryError, match=r"half.*512x512.*expected 256x256"):
        CharacterReferenceCatalog.discover(full_root, half_root)


def test_dual_catalog_custom_aliases_apply_to_union_not_each_variant(tmp_path: Path) -> None:
    full_root, half_root = _variant_roots(tmp_path)

    catalog = CharacterReferenceCatalog.discover(
        full_root,
        half_root,
        aliases={"huggy": ("blue-monster",), "pinky": ("pink-monster",)},
    )

    assert catalog.resolve("blue-monster_walk.mp4").canonical_id == "huggy"
    assert catalog.resolve("pink-monster_idle.zip").canonical_id == "pinky"


def test_catalog_revision_hashes_variant_identity_and_content(tmp_path: Path) -> None:
    full_root, half_root = _variant_roots(
        tmp_path,
        full_characters=("jessica",),
        half_characters=("jessica",),
    )
    catalog = CharacterReferenceCatalog.discover(full_root, half_root)
    full = catalog.require("jessica", "full")
    half = catalog.require("jessica", "half")
    digest = hashlib.sha256()
    digest.update(b"character-reference-catalog-v2\0")
    digest.update(f"full\x00jessica\x00384x512\x00{full.sha256}\n".encode("utf-8"))
    digest.update(f"half\x00jessica\x00256x256\x00{half.sha256}\n".encode("utf-8"))

    assert catalog.catalog_revision == digest.hexdigest()
