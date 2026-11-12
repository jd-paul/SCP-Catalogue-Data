from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

from .models import (
    Article,
    ArticleKind,
    Attribution,
    ValidationError,
    canonical_url,
    sorted_articles,
    utc_datetime,
)

OBJECT_CLASSES = {
    "safe": "Safe",
    "euclid": "Euclid",
    "keter": "Keter",
    "thaumiel": "Thaumiel",
    "apollyon": "Apollyon",
    "archon": "Archon",
    "explained": "Explained",
    "neutralized": "Neutralized",
    "pending": "Pending",
    "decommissioned": "Decommissioned",
    "ticonderoga": "Ticonderoga",
}


def _tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({item.strip().lower() for item in value if isinstance(item, str) and item.strip()}))


def _object_class(tags: Iterable[str]) -> str | None:
    return next((OBJECT_CLASSES[tag] for tag in tags if tag in OBJECT_CLASSES), None)


def _series(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        digits = value.removeprefix("crom:").removeprefix("series-")
        if digits.isdigit() and int(digits) > 0:
            return int(digits)
    return None


def _identity(page_id: Any, url: str) -> tuple[str, int | None]:
    try:
        numeric_id = int(page_id)
    except (TypeError, ValueError):
        numeric_id = 0
    return (f"wikidot:{numeric_id}", numeric_id) if numeric_id > 0 else (url, None)


def _fallback_attribution(name: Any) -> tuple[Attribution, ...]:
    if not isinstance(name, str) or not name.strip():
        return ()
    return (Attribution(type="SUBMITTER", name=name.strip(), order=0, is_current=False),)


def article_from_scp_api(record: dict[str, Any], kind: ArticleKind, fetched_at: datetime) -> Article:
    url = canonical_url(record.get("url") or f"https://scp-wiki.wikidot.com/{record['link']}")
    identity, wikidot_id = _identity(record.get("page_id"), url)
    created_at = utc_datetime(record["created_at"])
    tags = _tags(record.get("tags"))
    title = str(record.get("title") or record.get("scp") or record["link"]).strip()
    history = record.get("history")
    return Article(
        id=identity,
        wikidot_id=wikidot_id,
        canonical_url=url,
        kind=kind,
        wiki_title=title,
        display_title=title,
        attributions=_fallback_attribution(record.get("creator") or record.get("created_by")),
        tags=tags,
        rating=int(record.get("rating") or 0),
        rating_fetched_at=fetched_at,
        created_at=created_at,
        revision_count=len(history) if isinstance(history, list) else 0,
        object_class=_object_class(tags),
        series=_series(record.get("series")) if kind == "scp" else None,
        tale_year=created_at.year if kind == "tale" else None,
    )


def _crom_attributions(value: Any) -> tuple[Attribution, ...]:
    if not isinstance(value, list):
        return ()
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("user"), dict):
            continue
        name = item["user"].get("name")
        role = item.get("type")
        if not isinstance(name, str) or not name.strip() or not isinstance(role, str) or not role.strip():
            continue
        order = item.get("order") if isinstance(item.get("order"), int) else index
        date = utc_datetime(item["date"]) if isinstance(item.get("date"), str) else None
        result.append(
            Attribution(
                type=role.upper(),
                name=name.strip(),
                order=order,
                is_current=bool(item.get("isCurrent")),
                date=date,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.order, item.type, item.name)))


def article_from_crom(
    node: dict[str, Any],
    fetched_at: datetime,
    base: Article | None = None,
) -> Article:
    info = node.get("wikidotInfo")
    if not isinstance(info, dict):
        raise ValidationError("Crom record has no Wikidot metadata")
    url = canonical_url(node["url"])
    identity, wikidot_id = _identity(info.get("wikidotId"), url)
    tags = _tags(info.get("tags"))
    if "scp" not in tags and "tale" not in tags:
        raise ValidationError(f"Crom record is neither SCP nor tale: {url}")
    kind: ArticleKind = base.kind if base else ("scp" if "scp" in tags else "tale")
    created_value = info.get("createdAt")
    created_at = utc_datetime(created_value) if isinstance(created_value, str) else base.created_at if base else None
    if created_at is None:
        raise ValidationError(f"Crom record has no creation date: {url}")
    title = str(info.get("title") or (base.wiki_title if base else "")).strip()
    if not title:
        raise ValidationError(f"Crom record has no title: {url}")
    alternate_titles = node.get("alternateTitles")
    display_title = title
    if isinstance(alternate_titles, list):
        alternate = next(
            (
                item.get("title").strip()
                for item in alternate_titles
                if isinstance(item, dict) and isinstance(item.get("title"), str) and item.get("title").strip()
            ),
            None,
        )
        if alternate:
            display_title = alternate
    attributions = _crom_attributions(node.get("attributions"))
    rating = info.get("rating")
    revision_count = info.get("revisionCount")
    series = base.series if base and base.series is not None else _series(
        next((tag for tag in tags if tag.startswith("crom:series-")), None)
    )
    return Article(
        id=identity,
        wikidot_id=wikidot_id,
        canonical_url=url,
        kind=kind,
        wiki_title=title,
        display_title=display_title,
        attributions=attributions or (base.attributions if base else ()),
        tags=tags or (base.tags if base else ()),
        rating=int(rating) if isinstance(rating, (int, float)) else base.rating if base else 0,
        rating_fetched_at=fetched_at,
        created_at=created_at,
        revision_count=int(revision_count) if isinstance(revision_count, int) else base.revision_count if base else 0,
        object_class=_object_class(tags),
        series=series if kind == "scp" else None,
        tale_year=created_at.year if kind == "tale" else None,
    )


def load_scp_api_index(path: str | Path, kind: ArticleKind, fetched_at: datetime) -> list[Article]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValidationError(f"scp-api index must be an object: {path}")
    articles = [article_from_scp_api(record, kind, fetched_at) for record in payload.values() if isinstance(record, dict)]
    if len(articles) != len(payload):
        raise ValidationError(f"scp-api index contains a non-object record: {path}")
    return articles


def download_json(client: httpx.Client, url: str, *, max_bytes: int = 100_000_000) -> Any:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        if response.url.scheme != "https":
            raise ValidationError("Metadata downloads must use HTTPS")
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise ValidationError(f"Metadata response exceeds {max_bytes} bytes")
    return json.loads(chunks)


def merge_catalogue(
    base_articles: Iterable[Article],
    crom_nodes: Iterable[dict[str, Any]],
    fetched_at: datetime,
) -> list[Article]:
    merged = {article.id: article for article in base_articles}
    by_url = {article.canonical_url: article for article in merged.values()}
    for node in crom_nodes:
        info = node.get("wikidotInfo") if isinstance(node, dict) else None
        identity, _ = _identity(info.get("wikidotId") if isinstance(info, dict) else None, canonical_url(node["url"]))
        base = merged.get(identity) or by_url.get(canonical_url(node["url"]))
        article = article_from_crom(node, fetched_at, base)
        merged[article.id] = article
        by_url[article.canonical_url] = article
    return sorted_articles(list(merged.values()))
