from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

ArticleKind = Literal["scp", "tale"]
SCHEMA_VERSION = 1


class ValidationError(ValueError):
    pass


def utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_datetime(value: datetime) -> str:
    return utc_datetime(value).isoformat().replace("+00:00", "Z")


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or host not in {
        "scp-wiki.wikidot.com",
        "scpwiki.com",
        "www.scpwiki.com",
    }:
        raise ValidationError(f"Unsupported SCP article URL: {value!r}")
    path = parsed.path.rstrip("/")
    if not path or any(segment in {"", ".", ".."} for segment in path.split("/")[1:]):
        raise ValidationError(f"Invalid SCP article path: {value!r}")
    return urlunsplit(("https", "scp-wiki.wikidot.com", path, "", ""))


@dataclass(frozen=True, slots=True)
class Attribution:
    type: str
    name: str
    order: int
    is_current: bool
    date: datetime | None = None

    def __post_init__(self) -> None:
        if not self.type.strip() or not self.name.strip() or self.order < 0:
            raise ValidationError("Attribution requires a role, name, and non-negative order")

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type.upper(),
            "name": self.name,
            "order": self.order,
            "isCurrent": self.is_current,
        }
        if self.date is not None:
            result["date"] = iso_datetime(self.date)
        return result


@dataclass(frozen=True, slots=True)
class Article:
    id: str
    wikidot_id: int | None
    canonical_url: str
    kind: ArticleKind
    wiki_title: str
    display_title: str
    attributions: tuple[Attribution, ...]
    tags: tuple[str, ...]
    rating: int
    rating_fetched_at: datetime
    created_at: datetime
    revision_count: int
    object_class: str | None = None
    series: int | None = None
    tale_year: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_url", canonical_url(self.canonical_url))
        object.__setattr__(self, "created_at", utc_datetime(self.created_at))
        object.__setattr__(self, "rating_fetched_at", utc_datetime(self.rating_fetched_at))
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))
        if self.kind not in {"scp", "tale"}:
            raise ValidationError(f"Unsupported article type: {self.kind!r}")
        if not self.id or not self.wiki_title.strip() or not self.display_title.strip():
            raise ValidationError("Article identity and titles must not be empty")
        if self.wikidot_id is not None:
            expected_id = f"wikidot:{self.wikidot_id}"
            if self.id != expected_id:
                raise ValidationError(f"Article ID {self.id!r} does not match {expected_id!r}")
        elif self.id != self.canonical_url:
            raise ValidationError("URL-backed article ID must equal its canonical URL")
        if self.revision_count < 0:
            raise ValidationError("Revision count cannot be negative")
        if self.kind == "tale" and self.tale_year != self.created_at.year:
            raise ValidationError("Tale year must match its creation year")
        if self.kind == "scp" and self.tale_year is not None:
            raise ValidationError("SCP articles cannot have a tale year")

    def to_json(self, *, latest: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "wikidotId": self.wikidot_id,
            "canonicalURL": self.canonical_url,
            "type": self.kind,
            "wikiTitle": self.wiki_title,
            "displayTitle": self.display_title,
            "attributions": [item.to_json() for item in self.attributions],
            "tags": list(self.tags),
            "rating": self.rating,
            "ratingFetchedAt": iso_datetime(self.rating_fetched_at),
            "createdAt": iso_datetime(self.created_at),
            "revisionCount": self.revision_count,
            "objectClass": self.object_class,
            "series": self.series,
            "taleYear": self.tale_year,
        }
        if latest:
            result["deltaVersion"] = None
        return result


def sorted_articles(articles: list[Article]) -> list[Article]:
    return sorted(articles, key=lambda article: (-article.created_at.timestamp(), article.id))


def validate_articles(articles: list[Article], *, expected_identities: set[str] | None = None) -> None:
    identities = [article.id for article in articles]
    if len(identities) != len(set(identities)):
        raise ValidationError("Duplicate stable article identities")
    urls = [article.canonical_url for article in articles]
    if len(urls) != len(set(urls)):
        raise ValidationError("Duplicate canonical article URLs")
    missing = (expected_identities or set()) - set(identities)
    if missing:
        raise ValidationError(f"Baseline is missing {len(missing)} expected article identities")
