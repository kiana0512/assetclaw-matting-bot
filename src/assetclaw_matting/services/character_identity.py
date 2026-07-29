from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence


DEFAULT_CHARACTER_REFERENCE_ROOT = Path(r"C:\imageclip\Charactor")
SUPPORTED_REFERENCE_SUFFIXES = frozenset({".jpeg", ".jpg", ".png", ".webp"})
DEFAULT_CHARACTER_ALIASES: Mapping[str, tuple[str, ...]] = {
    # These are explicit legacy production names.  Keeping them here lets us
    # support old filenames without weakening the boundary-safe matcher.
    "juria": ("juriaback",),
    "tasha": ("newtashaback",),
}


class CharacterRegistryError(RuntimeError):
    """Raised when the on-disk character reference registry is invalid."""


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

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "path": str(self.path),
            "sha256": self.sha256,
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
    ) -> "CharacterRegistry":
        """Build a fresh registry from a configurable flat reference folder.

        Discovery is intentionally uncached so callers can safely refresh the
        registry when the reference folder is hot-updated.  Custom aliases are
        exact aliases, not fuzzy hints; they receive the same boundary checks as
        canonical names.
        """

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
            references.append(
                CharacterReference(
                    canonical_id=canonical_id,
                    display_name=display_name,
                    aliases=alias_values,
                    path=path.resolve(),
                    sha256=_sha256(path),
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
    """Normalize a path/name into Unicode alphanumeric boundary tokens."""

    normalized = normalize_text(value)
    tokens: list[str] = []
    current: list[str] = []
    current_is_number: bool | None = None
    for character in normalized:
        if not character.isalnum():
            if current:
                tokens.append("".join(current))
                current = []
            current_is_number = None
            continue
        is_number = character.isnumeric()
        if current and is_number != current_is_number:
            tokens.append("".join(current))
            current = []
        current.append(character)
        current_is_number = is_number
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
