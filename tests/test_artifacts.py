import hashlib
import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from scp_catalogue_data.artifacts import (
    build_baseline,
    descriptor,
    sha256,
    validate_baseline_package,
    write_latest,
    write_manifest,
)
from scp_catalogue_data.models import sorted_articles
from conftest import NOW

ROOT = Path(__file__).parents[1]


def validate_schema(name, value):
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    jsonschema.validate(value, schema, format_checker=jsonschema.FormatChecker())


def test_latest_and_manifest_match_public_schema(article, tmp_path):
    latest = tmp_path / "latest.json"
    write_latest([article], latest, NOW)
    baseline = tmp_path / "metadata.zip"
    build_baseline([article], baseline, feed_version="2026.11.11.12", generated_at=NOW)
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        latest,
        [{"id": "metadata", "version": "2026.11.11.12", **descriptor(baseline, "https://example.com/metadata.zip")}],
        feed_version="2026.11.11.12",
        generated_at=NOW,
    )

    latest_payload = json.loads(latest.read_text())
    manifest_payload = json.loads(manifest.read_text())
    validate_schema("latest.schema.json", latest_payload)
    validate_schema("manifest.schema.json", manifest_payload)
    assert latest_payload["articles"][0]["deltaVersion"] is None
    assert manifest_payload["latest"]["sha256"] == sha256(latest)
    assert manifest_payload["deltas"] == []


def test_baseline_contains_integrity_checked_sqlite_and_attribution(article, tmp_path):
    tale = replace(
        article,
        id="wikidot:42",
        wikidot_id=42,
        canonical_url="https://scp-wiki.wikidot.com/example-tale",
        kind="tale",
        wiki_title="A Tale",
        display_title="A Tale",
        tags=("tale",),
        created_at=datetime(2012, 1, 1, tzinfo=timezone.utc),
        object_class=None,
        series=None,
        tale_year=2012,
    )
    package = tmp_path / "metadata.zip"
    build_baseline([article, tale], package, feed_version="1", generated_at=NOW)
    validate_baseline_package(package)

    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {"manifest.json", "catalog.sqlite", "licenses.txt"}
        assert b"Moto42" in archive.read("licenses.txt")
        catalogue = tmp_path / "catalog.sqlite"
        catalogue.write_bytes(archive.read("catalog.sqlite"))
    connection = sqlite3.connect(catalogue)
    assert connection.execute("SELECT count(*) FROM articles").fetchone()[0] == 2
    assert connection.execute("SELECT name FROM attributions").fetchone()[0] == "Moto42"
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    connection.close()


def test_baseline_is_deterministic_for_identical_input(article, tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    build_baseline([article], first, feed_version="1", generated_at=NOW)
    build_baseline([article], second, feed_version="1", generated_at=NOW)
    assert sha256(first) == sha256(second)


def test_latest_order_uses_creation_then_stable_identity(article):
    newer = replace(
        article,
        id="wikidot:2",
        wikidot_id=2,
        canonical_url="https://scp-wiki.wikidot.com/newer",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert sorted_articles([article, newer])[0].id == "wikidot:2"
    same_time = replace(
        article,
        id="wikidot:1",
        wikidot_id=1,
        canonical_url="https://scp-wiki.wikidot.com/first",
    )
    assert sorted_articles([article, same_time])[0].id == "wikidot:1"
