from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .artifacts import (
    build_baseline,
    descriptor,
    validate_baseline_package,
    write_latest,
    write_manifest,
)
from .crom import CromClient, USER_AGENT
from .models import Article, ValidationError, sorted_articles, utc_datetime, validate_articles
from .sources import (
    article_from_crom,
    article_from_scp_api,
    download_json,
    load_scp_api_index,
    merge_catalogue,
)

ITEMS_INDEX = "https://raw.githubusercontent.com/scp-data/scp-api/main/docs/data/scp/items/index.json"
TALES_INDEX = "https://raw.githubusercontent.com/scp-data/scp-api/main/docs/data/scp/tales/index.json"
RELEASES = "https://github.com/jd-paul/SCP-Catalogue-Data/releases/download"


def _version(generated_at: datetime) -> str:
    return f"{generated_at.year}.{generated_at.month:02}.{generated_at.day:02}.{generated_at.hour:02}"


def _load_index(client: httpx.Client, source: str, kind: str, fetched_at: datetime) -> list[Any]:
    if source.startswith("https://"):
        payload = download_json(client, source)
        if not isinstance(payload, dict):
            raise ValidationError(f"scp-api index must be an object: {source}")
        return [article_from_scp_api(record, kind, fetched_at) for record in payload.values()]
    return load_scp_api_index(source, kind, fetched_at)


def _latest(output: Path, generated_at: datetime, feed_version: str) -> None:
    client = CromClient()
    articles = sorted_articles([article_from_crom(node, generated_at) for node in client.fetch_latest()])
    validate_articles(articles)
    latest_path = output / "latest.json"
    write_latest(articles, latest_path, generated_at)
    baselines = []
    current_manifest = output / "manifest.json"
    if current_manifest.exists():
        current = json.loads(current_manifest.read_text(encoding="utf-8"))
        if current.get("schemaVersion") == 1 and isinstance(current.get("baselines"), list):
            baselines = current["baselines"]
    write_manifest(output / "manifest.json", latest_path, baselines, feed_version=feed_version, generated_at=generated_at)


def _baseline(args: argparse.Namespace, output: Path, generated_at: datetime, feed_version: str) -> None:
    crom = CromClient()
    with httpx.Client(
        timeout=httpx.Timeout(60, connect=15),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as http:
        base_articles = _load_index(http, args.items_index, "scp", generated_at)
        base_articles += _load_index(http, args.tales_index, "tale", generated_at)
    expected = {article.id for article in base_articles}
    if args.skip_crom_enrichment:
        articles = sorted_articles(base_articles)
    else:
        articles = merge_catalogue(base_articles, crom.iter_catalogue(), generated_at)
    validate_articles(articles, expected_identities=expected)
    if len(articles) < args.minimum_articles:
        raise ValidationError(f"Baseline contains only {len(articles)} articles")
    proposals = [article for article in articles if "001-proposal" in article.tags]
    if args.minimum_articles > 1 and len(proposals) < 2:
        raise ValidationError("Baseline has incomplete SCP-001 proposal coverage")
    baseline_path = output / "baselines" / f"metadata-{feed_version}.zip"
    build_baseline(articles, baseline_path, feed_version=feed_version, generated_at=generated_at)
    validate_baseline_package(baseline_path)
    latest_path = output / "latest.json"
    write_latest(articles[:50], latest_path, generated_at)
    baseline_url = f"{RELEASES}/baseline-{feed_version}/{baseline_path.name}"
    write_manifest(
        output / "manifest.json",
        latest_path,
        [{"id": "metadata", "version": feed_version, **descriptor(baseline_path, baseline_url)}],
        feed_version=feed_version,
        generated_at=generated_at,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scp-catalogue-data")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("latest", "baseline"):
        command = subcommands.add_parser(name)
        command.add_argument("--output", type=Path, default=Path("dist"))
        command.add_argument("--feed-version")
        command.add_argument("--generated-at", help="ISO timestamp; intended for reproducible checks")
        if name == "baseline":
            command.add_argument("--items-index", default=ITEMS_INDEX)
            command.add_argument("--tales-index", default=TALES_INDEX)
            command.add_argument("--skip-crom-enrichment", action="store_true")
            command.add_argument("--minimum-articles", type=int, default=1000)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    generated_at = utc_datetime(args.generated_at) if args.generated_at else datetime.now(timezone.utc)
    feed_version = args.feed_version or _version(generated_at)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "latest":
        _latest(args.output, generated_at, feed_version)
    else:
        _baseline(args, args.output, generated_at, feed_version)
    print(f"Generated {args.command} artifacts in {args.output} (feed {feed_version})")


if __name__ == "__main__":
    main()
