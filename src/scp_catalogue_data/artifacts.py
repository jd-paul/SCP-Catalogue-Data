from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import Article, SCHEMA_VERSION, iso_datetime, validate_articles


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def descriptor(path: Path, url: str) -> dict[str, Any]:
    return {"url": url, "bytes": path.stat().st_size, "sha256": sha256(path)}


def build_catalogue_sqlite(articles: Iterable[Article], path: Path, generated_at: datetime) -> None:
    records = list(articles)
    validate_articles(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.executescript(
            """
            CREATE TABLE schemaMetadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE articles (
                id TEXT PRIMARY KEY,
                wikidotId INTEGER UNIQUE,
                canonicalURL TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK (type IN ('scp', 'tale')),
                wikiTitle TEXT NOT NULL,
                displayTitle TEXT NOT NULL,
                tags TEXT NOT NULL,
                rating INTEGER NOT NULL,
                ratingFetchedAt TEXT NOT NULL,
                createdAt TEXT NOT NULL,
                revisionCount INTEGER NOT NULL,
                objectClass TEXT,
                series INTEGER,
                taleYear INTEGER
            );
            CREATE TABLE attributions (
                articleId TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                date TEXT,
                position INTEGER NOT NULL,
                isCurrent INTEGER NOT NULL,
                PRIMARY KEY (articleId, type, name, position)
            );
            CREATE INDEX articlesCreatedAt ON articles(createdAt DESC, id);
            CREATE INDEX articlesType ON articles(type);
            CREATE INDEX articlesSeries ON articles(series);
            CREATE INDEX articlesTaleYear ON articles(taleYear);
            CREATE INDEX attributionsName ON attributions(name);
            """
        )
        with connection:
            connection.executemany(
                "INSERT INTO schemaMetadata(key, value) VALUES (?, ?)",
                [("schemaVersion", str(SCHEMA_VERSION)), ("generatedAt", iso_datetime(generated_at))],
            )
            connection.executemany(
                """
                INSERT INTO articles(
                    id, wikidotId, canonicalURL, type, wikiTitle, displayTitle,
                    tags, rating, ratingFetchedAt, createdAt, revisionCount,
                    objectClass, series, taleYear
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        article.id,
                        article.wikidot_id,
                        article.canonical_url,
                        article.kind,
                        article.wiki_title,
                        article.display_title,
                        json.dumps(article.tags, separators=(",", ":")),
                        article.rating,
                        iso_datetime(article.rating_fetched_at),
                        iso_datetime(article.created_at),
                        article.revision_count,
                        article.object_class,
                        article.series,
                        article.tale_year,
                    )
                    for article in records
                ],
            )
            connection.executemany(
                """
                INSERT INTO attributions(articleId, type, name, date, position, isCurrent)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        article.id,
                        attribution.type,
                        attribution.name,
                        iso_datetime(attribution.date) if attribution.date else None,
                        attribution.order,
                        int(attribution.is_current),
                    )
                    for article in records
                    for attribution in article.attributions
                ],
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",) or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Generated SQLite catalogue failed integrity validation")
    finally:
        connection.close()


def _licenses(articles: Iterable[Article]) -> str:
    lines = [
        "SCP Catalogue metadata and content notices",
        "",
        "SCP Wiki content and adaptations are available under CC BY-SA 3.0 where applicable:",
        "https://creativecommons.org/licenses/by-sa/3.0/",
        "https://scp-wiki.wikidot.com/licensing-guide",
        "",
        "Metadata formatting and normalization are adapted for SCP Catalogue.",
        "SCP Catalogue is unofficial and does not imply endorsement by the SCP Wiki or its authors.",
        "Individual article and media notices remain part of their canonical pages.",
        "SCP-173's former Izumi Kato Untitled 2004 image is not bundled or commercially reused.",
        "",
        "Per-article attribution supplements, but does not replace, the structured records:",
        "",
    ]
    for article in sorted(articles, key=lambda item: item.id):
        title = f"{article.wiki_title}: {article.display_title}" if article.display_title != article.wiki_title else article.wiki_title
        lines.append(f"{title}\n{article.canonical_url}")
        if article.attributions:
            for attribution in article.attributions:
                lines.append(f"  {attribution.type}: {attribution.name}")
        else:
            lines.append("  Attribution unavailable; see the canonical SCP Wiki page.")
        lines.append("")
    return "\n".join(lines)


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_baseline(
    articles: list[Article],
    output_path: Path,
    *,
    feed_version: str,
    generated_at: datetime,
) -> None:
    validate_articles(articles)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_path.parent) as temporary:
        catalogue = Path(temporary) / "catalog.sqlite"
        build_catalogue_sqlite(articles, catalogue, generated_at)
        package_manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "packageType": "metadata-baseline",
            "feedVersion": feed_version,
            "generatedAt": iso_datetime(generated_at),
            "articleCount": len(articles),
            "catalog": descriptor(catalogue, "catalog.sqlite"),
        }
        with zipfile.ZipFile(output_path, "w") as archive:
            _zip_write(archive, "manifest.json", (json.dumps(package_manifest, indent=2, sort_keys=True) + "\n").encode())
            _zip_write(archive, "catalog.sqlite", catalogue.read_bytes())
            _zip_write(archive, "licenses.txt", _licenses(articles).encode())


def write_latest(articles: list[Article], output_path: Path, generated_at: datetime) -> None:
    validate_articles(articles)
    write_json(
        output_path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": iso_datetime(generated_at),
            "articles": [article.to_json(latest=True) for article in articles],
        },
    )


def write_manifest(
    output_path: Path,
    latest_path: Path,
    baselines: list[dict[str, Any]],
    *,
    feed_version: str,
    generated_at: datetime,
) -> None:
    write_json(
        output_path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "feedVersion": feed_version,
            "generatedAt": iso_datetime(generated_at),
            "minimumAppVersion": "1.0.0",
            "latest": descriptor(latest_path, "latest.json"),
            "deltas": [],
            "baselines": baselines,
        },
    )


def validate_baseline_package(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {"manifest.json", "catalog.sqlite", "licenses.txt"}:
            raise RuntimeError("Baseline package has an unexpected file set")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("packageType") != "metadata-baseline":
            raise RuntimeError("Baseline package has an unsupported schema")
        catalogue_bytes = archive.read("catalog.sqlite")
        if hashlib.sha256(catalogue_bytes).hexdigest() != manifest["catalog"]["sha256"]:
            raise RuntimeError("Baseline catalogue checksum does not match")
        with tempfile.TemporaryDirectory() as temporary:
            catalogue = Path(temporary) / "catalog.sqlite"
            catalogue.write_bytes(catalogue_bytes)
            connection = sqlite3.connect(catalogue)
            try:
                count = connection.execute("SELECT count(*) FROM articles").fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
        if count != manifest["articleCount"] or integrity != ("ok",):
            raise RuntimeError("Baseline catalogue count or integrity is invalid")
