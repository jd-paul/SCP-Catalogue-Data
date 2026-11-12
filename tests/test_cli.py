import json
import sqlite3
import zipfile

from scp_catalogue_data.cli import main
from conftest import NOW


def record(link, page_id, kind):
    return {
        "link": link,
        "page_id": str(page_id),
        "url": f"https://scp-wiki.wikidot.com/{link}",
        "title": link.upper() if kind == "scp" else "A Tale",
        "creator": "Submitter",
        "created_at": "2012-03-01T00:00:00",
        "rating": 10,
        "series": 1 if kind == "scp" else None,
        "tags": [kind, "001-proposal"] if kind == "scp" else [kind],
        "history": [],
    }


def test_local_baseline_command_builds_complete_artifacts(tmp_path):
    items = tmp_path / "items.json"
    tales = tmp_path / "tales.json"
    items.write_text(json.dumps({"scp-001-a": record("scp-001-a", 1, "scp"), "scp-001-b": record("scp-001-b", 2, "scp")}))
    tales.write_text(json.dumps({"a-tale": record("a-tale", 3, "tale")}))
    output = tmp_path / "dist"

    main(
        [
            "baseline",
            "--items-index",
            str(items),
            "--tales-index",
            str(tales),
            "--skip-crom-enrichment",
            "--minimum-articles",
            "1",
            "--generated-at",
            "2026-11-11T12:00:00Z",
            "--feed-version",
            "test-1",
            "--output",
            str(output),
        ]
    )

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["feedVersion"] == "test-1"
    assert manifest["baselines"][0]["version"] == "test-1"
    latest = json.loads((output / "latest.json").read_text())
    assert len(latest["articles"]) == 3
    package = output / "baselines" / "metadata-test-1.zip"
    assert package.exists()
    with zipfile.ZipFile(package) as archive:
        assert json.loads(archive.read("manifest.json"))["articleCount"] == 3
