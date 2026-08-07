from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from PIL import Image, UnidentifiedImageError


DEFAULT_CHARACTER_REFERENCE_ROOT = Path(r"C:\imageclip\Charactor")
DEFAULT_CHARACTER_FULL_REFERENCE_ROOT = Path(r"C:\imageclip\CharactorFull")
DEFAULT_CHARACTER_EMOJI_REFERENCE_ROOT = Path(r"C:\imageclip\CharactorEmoji")
FULL_CHARACTER_REFERENCE_SIZE = (384, 512)
HALF_CHARACTER_REFERENCE_SIZE = (256, 256)
SUPPORTED_REFERENCE_SUFFIXES = frozenset({".jpeg", ".jpg", ".png", ".webp"})
DEFAULT_CHARACTER_ALIASES: Mapping[str, tuple[str, ...]] = {
    # These are explicit legacy production names.  Keeping them here lets us
    # support old filenames without weakening the boundary-safe matcher.
    "juria": ("juriaback",),
    "tasha": ("newtashaback",),
}


class CharacterRegistryError(RuntimeError):
    """Raised when the on-disk character reference registry is invalid."""


class CharacterProfileError(ValueError):
    """Raised when a reference profile or output size is not deterministic."""


class CharacterReferenceVariant(str, Enum):
    FULL = "full"
    HALF = "half"

    @property
    def output_size(self) -> tuple[int, int]:
        return FULL_CHARACTER_REFERENCE_SIZE if self is self.FULL else HALF_CHARACTER_REFERENCE_SIZE


class CharacterResolutionStatus(str, Enum):
    MATCHED = "matched"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class CharacterReference:
    """One immutable view of a character color-reference asset."""

    canonical_id: str
    display_name: str
    aliases: tuple[str, ...]
    path: Path
    sha256: str
    variant: CharacterReferenceVariant | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "path": str(self.path),
            "sha256": self.sha256,
            "variant": self.variant.value if self.variant is not None else None,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class CharacterCandidateMatch:
    reference: CharacterReference
    matched_aliases: tuple[str, ...]

    @property
    def canonical_id(self) -> str:
        return self.reference.canonical_id


@dataclass(frozen=True, slots=True)
class CharacterResolution:
    """Strict filename/path resolution result.

    ``reference`` is populated only for a unique match.  Callers must pause and
    ask the user when the status is ``unresolved`` or ``ambiguous``; this
    service deliberately has no fuzzy or best-effort fallback.
    """

    status: CharacterResolutionStatus
    query: str
    normalized_tokens: tuple[str, ...]
    candidates: tuple[CharacterCandidateMatch, ...]
    reason: str

    @property
    def reference(self) -> CharacterReference | None:
        if self.status is CharacterResolutionStatus.MATCHED and len(self.candidates) == 1:
            return self.candidates[0].reference
        return None

    @property
    def canonical_id(self) -> str | None:
        reference = self.reference
        return reference.canonical_id if reference is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "query": self.query,
            "normalized_tokens": list(self.normalized_tokens),
            "canonical_id": self.canonical_id,
            "reason": self.reason,
            "candidates": [
                {
                    "canonical_id": candidate.canonical_id,
                    "matched_aliases": list(candidate.matched_aliases),
                    "reference": candidate.reference.to_dict(),
                }
                for candidate in self.candidates
            ],
        }


class CharacterRegistry:
    """Discover reference images and resolve explicit character name tokens."""

    def __init__(self, root: Path, references: Sequence[CharacterReference]) -> None:
        self.root = root.resolve()
        ordered = tuple(sorted(references, key=lambda item: item.canonical_id))
        self.references = ordered
        self._by_id = {item.canonical_id: item for item in ordered}
        if len(self._by_id) != len(ordered):
            raise CharacterRegistryError("duplicate canonical character ids")

        alias_index: dict[tuple[str, ...], list[CharacterReference]] = {}
        alias_labels: dict[tuple[str, tuple[str, ...]], str] = {}
        for reference in ordered:
            for alias in reference.aliases:
                tokens = normalize_name_tokens(alias)
                if not tokens:
                    continue
                alias_index.setdefault(tokens, []).append(reference)
                alias_labels[(reference.canonical_id, tokens)] = alias
        self._alias_index = {
            tokens: tuple(sorted(items, key=lambda item: item.canonical_id))
            for tokens, items in alias_index.items()
        }
        self._alias_labels = alias_labels

    @classmethod
    def discover(
        cls,
        root: str | Path = DEFAULT_CHARACTER_REFERENCE_ROOT,
        *,
        aliases: Mapping[str, Iterable[str]] | None = None,
        display_names: Mapping[str, str] | None = None,
        suffixes: Iterable[str] = SUPPORTED_REFERENCE_SUFFIXES,
        variant: CharacterReferenceVariant | str | None = None,
    ) -> "CharacterRegistry":
        """Build a fresh registry from a configurable flat reference folder.

        Discovery is intentionally uncached so callers can safely refresh the
        registry when the reference folder is hot-updated.  Custom aliases are
        exact aliases, not fuzzy hints; they receive the same boundary checks as
        canonical names.
        """

        normalized_variant = (
            normalize_character_reference_variant(variant) if variant is not None else None
        )
        reference_root = Path(root).expanduser().resolve()
        if not reference_root.is_dir():
            raise CharacterRegistryError(f"character reference root does not exist: {reference_root}")

        allowed_suffixes = {
            suffix.casefold() if suffix.startswith(".") else f".{suffix.casefold()}"
            for suffix in suffixes
        }
        files = sorted(
            (
                path
                for path in reference_root.iterdir()
                if path.is_file() and path.suffix.casefold() in allowed_suffixes
            ),
            key=lambda path: normalize_text(path.name),
        )
        if not files:
            raise CharacterRegistryError(f"no character reference images found: {reference_root}")

        default_aliases = _normalize_mapping_keys(DEFAULT_CHARACTER_ALIASES)
        custom_aliases = _normalize_mapping_keys(aliases or {})
        normalized_aliases = dict(default_aliases)
        for canonical_id, configured in custom_aliases.items():
            normalized_aliases[canonical_id] = (*normalized_aliases.get(canonical_id, ()), *configured)
        normalized_display_names = {
            canonical_character_id(key): str(value).strip()
            for key, value in (display_names or {}).items()
        }
        references: list[CharacterReference] = []
        seen_ids: dict[str, Path] = {}
        for path in files:
            canonical_id = canonical_character_id(path.stem)
            if not canonical_id:
                raise CharacterRegistryError(f"reference filename has no usable character id: {path.name}")
            if canonical_id in seen_ids:
                raise CharacterRegistryError(
                    "duplicate canonical character id "
                    f"{canonical_id!r}: {seen_ids[canonical_id].name}, {path.name}"
                )
            seen_ids[canonical_id] = path

            display_name = normalized_display_names.get(canonical_id) or _default_display_name(path.stem)
            configured_aliases = normalized_aliases.get(canonical_id, ())
            alias_values = _deduplicate_aliases((canonical_id, display_name, *configured_aliases))
            width: int | None = None
            height: int | None = None
            if normalized_variant is not None:
                width, height = _validated_image_size(path, normalized_variant)
            references.append(
                CharacterReference(
                    canonical_id=canonical_id,
                    display_name=display_name,
                    aliases=alias_values,
                    path=path.resolve(),
                    sha256=_sha256(path),
                    variant=normalized_variant,
                    width=width,
                    height=height,
                )
            )

        unknown_alias_targets = sorted(set(custom_aliases) - set(seen_ids))
        if unknown_alias_targets:
            raise CharacterRegistryError(
                "aliases configured for unknown characters: " + ", ".join(unknown_alias_targets)
            )
        unknown_display_targets = sorted(set(normalized_display_names) - set(seen_ids))
        if unknown_display_targets:
            raise CharacterRegistryError(
                "display names configured for unknown characters: " + ", ".join(unknown_display_targets)
            )
        return cls(reference_root, references)

    def get(self, canonical_id: str) -> CharacterReference | None:
        return self._by_id.get(canonical_character_id(canonical_id))

    def require(self, canonical_id: str) -> CharacterReference:
        reference = self.get(canonical_id)
        if reference is None:
            raise KeyError(f"unknown character: {canonical_id}")
        return reference

    def resolve(self, filename_or_path: str | Path) -> CharacterResolution:
        """Resolve only exact alias token sequences in a filename or path.

        NFKC normalization and case folding are applied first.  Punctuation,
        whitespace, underscores and path separators are token boundaries.  An
        alias therefore matches ``Jessica-happy.mp4`` but not
        ``notjessica.mp4``.  A query containing two different character aliases
        is returned as ambiguous instead of selecting one by order.
        """

        query = str(filename_or_path)
        query_tokens = normalize_name_tokens(query)
        matched: dict[str, set[str]] = {}
        references: dict[str, CharacterReference] = {}
        for alias_tokens, alias_references in self._alias_index.items():
            if not _contains_token_sequence(query_tokens, alias_tokens):
                continue
            for reference in alias_references:
                references[reference.canonical_id] = reference
                label = self._alias_labels[(reference.canonical_id, alias_tokens)]
                matched.setdefault(reference.canonical_id, set()).add(label)

        candidates = tuple(
            CharacterCandidateMatch(
                reference=references[canonical_id],
                matched_aliases=tuple(sorted(matched[canonical_id], key=normalize_text)),
            )
            for canonical_id in sorted(references)
        )
        if not candidates:
            return CharacterResolution(
                status=CharacterResolutionStatus.UNRESOLVED,
                query=query,
                normalized_tokens=query_tokens,
                candidates=(),
                reason="no_boundary_safe_alias_match",
            )
        if len(candidates) > 1:
            return CharacterResolution(
                status=CharacterResolutionStatus.AMBIGUOUS,
                query=query,
                normalized_tokens=query_tokens,
                candidates=candidates,
                reason="multiple_character_aliases_matched",
            )
        return CharacterResolution(
            status=CharacterResolutionStatus.MATCHED,
            query=query,
            normalized_tokens=query_tokens,
            candidates=candidates,
            reason="unique_boundary_safe_alias_match",
        )


@dataclass(frozen=True, slots=True)
class CharacterReferenceCatalog:
    """Two-profile reference catalog with a union identity registry.

    Identity matching is deliberately independent of asset availability.  A
    character found in only one folder can still be identified; requesting its
    missing profile then fails closed instead of silently using the wrong body
    crop.
    """

    full_registry: CharacterRegistry
    half_registry: CharacterRegistry
    identity_registry: CharacterRegistry
    catalog_revision: str

    @classmethod
    def discover(
        cls,
        full_root: str | Path = DEFAULT_CHARACTER_FULL_REFERENCE_ROOT,
        emoji_root: str | Path = DEFAULT_CHARACTER_EMOJI_REFERENCE_ROOT,
        *,
        aliases: Mapping[str, Iterable[str]] | None = None,
        display_names: Mapping[str, str] | None = None,
        suffixes: Iterable[str] = SUPPORTED_REFERENCE_SUFFIXES,
    ) -> "CharacterReferenceCatalog":
        full = CharacterRegistry.discover(
            full_root,
            suffixes=suffixes,
            variant=CharacterReferenceVariant.FULL,
        )
        half = CharacterRegistry.discover(
            emoji_root,
            suffixes=suffixes,
            variant=CharacterReferenceVariant.HALF,
        )
        all_ids = {item.canonical_id for item in full.references} | {
            item.canonical_id for item in half.references
        }
        custom_aliases = _normalize_mapping_keys(aliases or {})
        normalized_display_names = {
            canonical_character_id(key): str(value).strip()
            for key, value in (display_names or {}).items()
        }
        unknown_alias_targets = sorted(set(custom_aliases) - all_ids)
        if unknown_alias_targets:
            raise CharacterRegistryError(
                "aliases configured for unknown characters: " + ", ".join(unknown_alias_targets)
            )
        unknown_display_targets = sorted(set(normalized_display_names) - all_ids)
        if unknown_display_targets:
            raise CharacterRegistryError(
                "display names configured for unknown characters: " + ", ".join(unknown_display_targets)
            )

        default_aliases = _normalize_mapping_keys(DEFAULT_CHARACTER_ALIASES)

        def apply_metadata(registry: CharacterRegistry) -> CharacterRegistry:
            references: list[CharacterReference] = []
            for reference in registry.references:
                canonical_id = reference.canonical_id
                display_name = normalized_display_names.get(canonical_id) or reference.display_name
                alias_values = _deduplicate_aliases(
                    (
                        canonical_id,
                        display_name,
                        *default_aliases.get(canonical_id, ()),
                        *custom_aliases.get(canonical_id, ()),
                    )
                )
                references.append(
                    CharacterReference(
                        canonical_id=canonical_id,
                        display_name=display_name,
                        aliases=alias_values,
                        path=reference.path,
                        sha256=reference.sha256,
                        variant=reference.variant,
                        width=reference.width,
                        height=reference.height,
                    )
                )
            return CharacterRegistry(registry.root, references)

        full = apply_metadata(full)
        half = apply_metadata(half)
        identity_references = tuple(
            full.get(canonical_id) or half.require(canonical_id)
            for canonical_id in sorted(all_ids)
        )
        identity_root = Path(full.root.parent)
        identity = CharacterRegistry(identity_root, identity_references)
        revision = _reference_catalog_revision(full, half)
        return cls(
            full_registry=full,
            half_registry=half,
            identity_registry=identity,
            catalog_revision=revision,
        )

    @property
    def references(self) -> tuple[CharacterReference, ...]:
        """Union character identities, not a profile-specific asset list."""

        return self.identity_registry.references

    def resolve(self, filename_or_path: str | Path) -> CharacterResolution:
        return self.identity_registry.resolve(filename_or_path)

    def registry_for(self, profile: object) -> CharacterRegistry:
        normalized = normalize_character_profile(profile)
        return self.full_registry if normalized == "full" else self.half_registry

    def get(self, canonical_id: str, profile: object) -> CharacterReference | None:
        """Return only the requested profile; never cross-profile fallback."""

        return self.registry_for(profile).get(canonical_id)

    def require(self, canonical_id: str, profile: object) -> CharacterReference:
        normalized = normalize_character_profile(profile)
        reference = self.get(canonical_id, normalized)
        if reference is None:
            size = CharacterReferenceVariant(normalized).output_size
            raise CharacterRegistryError(
                f"character {canonical_character_id(canonical_id)!r} has no {normalized} "
                f"reference ({size[0]}x{size[1]}); cross-profile fallback is forbidden"
            )
        return reference

    def available_profiles(self, canonical_id: str) -> tuple[str, ...]:
        return tuple(
            profile
            for profile in ("full", "half")
            if self.get(canonical_id, profile) is not None
        )


def normalize_character_reference_variant(value: object) -> CharacterReferenceVariant:
    """Normalize only canonical variants; aliases belong to profile parsing."""

    if isinstance(value, CharacterReferenceVariant):
        return value
    normalized = normalize_text(value)
    try:
        return CharacterReferenceVariant(normalized)
    except ValueError as exc:
        raise CharacterProfileError(
            f"unsupported character reference variant {value!r}; expected 'full' or 'half'"
        ) from exc


def normalize_character_profile(profile: object) -> str:
    """Return the canonical Cherry output profile (``full`` or ``half``).

    ``emoji`` and ``square`` are existing names for the 256x256 half-body
    profile.  ``auto`` is intentionally rejected because a concrete output
    size must be selected before a reference asset can be frozen.
    """

    if isinstance(profile, CharacterReferenceVariant):
        return profile.value
    normalized = normalize_text(profile)
    aliases = {
        "full": "full",
        "half": "half",
        "emoji": "half",
        "square": "half",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise CharacterProfileError(
            f"unsupported character profile {profile!r}; expected full or half "
            "(emoji/square are accepted half-profile aliases)"
        ) from exc


def normalize_character_output_size(width: object, height: object) -> tuple[int, int]:
    """Validate that an output size belongs to one of the two supported profiles."""

    size = (_strict_dimension(width, "width"), _strict_dimension(height, "height"))
    if size not in {FULL_CHARACTER_REFERENCE_SIZE, HALF_CHARACTER_REFERENCE_SIZE}:
        raise CharacterProfileError(
            f"unsupported character output size {size[0]}x{size[1]}; "
            "expected 384x512 (full) or 256x256 (half)"
        )
    return size


def character_profile_for_output_size(width: object, height: object) -> str:
    size = normalize_character_output_size(width, height)
    return "full" if size == FULL_CHARACTER_REFERENCE_SIZE else "half"


def resolve_character_profile(
    *,
    profile: object | None = None,
    width: object | None = None,
    height: object | None = None,
) -> str:
    """Resolve profile and/or size evidence, rejecting missing or conflicting data."""

    candidates: list[str] = []
    if profile is not None:
        candidates.append(normalize_character_profile(profile))
    if width is not None or height is not None:
        if width is None or height is None:
            raise CharacterProfileError("both output width and height are required")
        candidates.append(character_profile_for_output_size(width, height))
    if not candidates:
        raise CharacterProfileError("a concrete character profile or output size is required")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise CharacterProfileError(
            f"character profile conflicts with output size: {', '.join(candidates)}"
        )
    return candidates[0]


def list_characters(
    root: str | Path | None = None,
    *,
    aliases: Mapping[str, Iterable[str]] | None = None,
    display_names: Mapping[str, str] | None = None,
) -> tuple[CharacterReference, ...]:
    """Discover and return the current character reference catalog."""

    registry = CharacterRegistry.discover(
        root or DEFAULT_CHARACTER_REFERENCE_ROOT,
        aliases=aliases,
        display_names=display_names,
    )
    return registry.references


def resolve_character(
    *evidence: str | Path,
    root: str | Path | None = None,
    aliases: Mapping[str, Iterable[str]] | None = None,
    display_names: Mapping[str, str] | None = None,
) -> CharacterResolution:
    """Resolve combined filename/path evidence against a freshly loaded registry.

    Combining evidence never changes the strict policy: evidence naming two
    different characters produces ``ambiguous`` and evidence naming none
    produces ``unresolved``.
    """

    registry = CharacterRegistry.discover(
        root or DEFAULT_CHARACTER_REFERENCE_ROOT,
        aliases=aliases,
        display_names=display_names,
    )
    return registry.resolve(" | ".join(str(value) for value in evidence))


def normalize_text(value: object) -> str:
    """Return the shared Unicode normalization used by the registry."""

    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def normalize_name_tokens(value: object) -> tuple[str, ...]:
    """Normalize a path/name into boundary-safe Unicode tokens.

    ASCII character ids are commonly joined directly to a Chinese action name
    in production filenames (for example ``danny微笑关键帧.zip``).  A script
    transition is a real human-visible boundary even when no punctuation is
    present, so keep it distinct without enabling arbitrary substring matches.
    """

    normalized = normalize_text(value)
    tokens: list[str] = []
    current: list[str] = []
    current_kind = ""
    for character in normalized:
        if not character.isalnum():
            if current:
                tokens.append("".join(current))
                current = []
            current_kind = ""
            continue
        kind = _name_token_kind(character)
        if current and kind != current_kind:
            tokens.append("".join(current))
            current = []
        current.append(character)
        current_kind = kind
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _name_token_kind(character: str) -> str:
    if character.isnumeric():
        return "number"
    if character.isascii():
        return "ascii"
    codepoint = ord(character)
    if (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x3134F
    ):
        return "cjk"
    return "unicode"


def canonical_character_id(value: object) -> str:
    """Create a stable lowercase id while preserving explicit token boundaries."""

    return "-".join(normalize_name_tokens(value))


def _normalize_mapping_keys(values: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for key, aliases in values.items():
        canonical_id = canonical_character_id(key)
        if not canonical_id:
            raise CharacterRegistryError(f"invalid character alias target: {key!r}")
        if isinstance(aliases, str):
            alias_values = (aliases,)
        else:
            alias_values = tuple(str(alias) for alias in aliases)
        normalized[canonical_id] = alias_values
    return normalized


def _default_display_name(stem: str) -> str:
    tokens = normalize_name_tokens(stem)
    return " ".join(token[:1].upper() + token[1:] for token in tokens)


def _deduplicate_aliases(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        label = str(value).strip()
        tokens = normalize_name_tokens(label)
        if not tokens or tokens in seen:
            continue
        seen.add(tokens)
        result.append(label)
    return tuple(result)


def _contains_token_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


def _strict_dimension(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise CharacterProfileError(f"output {label} must be an integer")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise CharacterProfileError(f"output {label} must be an integer")
    return int(text)


def _validated_image_size(
    path: Path,
    variant: CharacterReferenceVariant,
) -> tuple[int, int]:
    expected = variant.output_size
    try:
        with Image.open(path) as image:
            actual = tuple(int(value) for value in image.size)
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise CharacterRegistryError(f"invalid character reference image: {path}") from exc
    if actual != expected:
        raise CharacterRegistryError(
            f"{variant.value} character reference has wrong pixel size: {path.name} "
            f"is {actual[0]}x{actual[1]}, expected {expected[0]}x{expected[1]}"
        )
    return actual


def _reference_catalog_revision(
    full_registry: CharacterRegistry,
    half_registry: CharacterRegistry,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"character-reference-catalog-v2\0")
    for variant, registry in (
        (CharacterReferenceVariant.FULL, full_registry),
        (CharacterReferenceVariant.HALF, half_registry),
    ):
        for reference in registry.references:
            digest.update(
                (
                    f"{variant.value}\0{reference.canonical_id}\0"
                    f"{reference.width}x{reference.height}\0{reference.sha256}\n"
                ).encode("utf-8")
            )
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
