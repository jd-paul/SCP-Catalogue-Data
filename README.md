# SCP Catalogue Data

Python generator for the static feeds consumed by [SCP Catalogue](https://github.com/jd-paul/SCP-Catalogue). Crom queries run only here in generation jobs; user devices download public artifacts from GitHub Releases and fetch individual canonical SCP Wiki articles when opened.

## Current scope

- The 50 newest SCPs and tales, ordered by creation time.
- A complete metadata-only SQLite baseline built from `scp-api` indexes and Crom metadata.
- Versioned manifests, SHA-256 integrity metadata, and per-article attribution.
- Manual generation and publication.

Daily body deltas, article-body packages, offline packs, and scheduled production generation are not part of this milestone. Scheduled Crom use remains disabled until the maintainers have been contacted about this application.

## Setup and verification

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Generate a Latest feed without changing a previously downloaded baseline reference:

```sh
scp-catalogue-data latest --output dist
```

Build a complete Latest feed and enriched metadata baseline:

```sh
scp-catalogue-data baseline --output dist --minimum-articles 1000
```

For a local source-data check without Crom catalogue enrichment:

```sh
scp-catalogue-data baseline \
  --items-index /path/to/items-index.json \
  --tales-index /path/to/tales-index.json \
  --skip-crom-enrichment \
  --minimum-articles 1 \
  --output dist
```

That fallback stores page creators as `SUBMITTER`, never as verified authors. Production baselines use Crom enrichment.

## Artifact contract

JSON Schema version 1 is documented in `schemas/manifest.schema.json` and `schemas/latest.schema.json`. The rolling `feed` release contains the current `manifest.json` and `latest.json`:

```text
https://github.com/jd-paul/SCP-Catalogue-Data/releases/download/feed/manifest.json
https://github.com/jd-paul/SCP-Catalogue-Data/releases/download/feed/latest.json
```

Each metadata baseline is uploaded to an immutable `baseline-<version>` release. Its ZIP contains:

```text
manifest.json
catalog.sqlite
licenses.txt
```

The app must verify schema versions, byte counts, and hashes before importing. Unsupported major versions or invalid artifacts leave the existing database untouched.

## Publication and retention

GitHub Actions is manual-only initially. A validation job runs tests and builds artifacts with read-only repository permissions. A separate publish job alone receives `contents: write`; it uploads immutable baselines first, current Latest second, and the current manifest last.

Keep the active baseline and at least four preceding metadata baselines. A failed run never removes or replaces the active feed. Prune only superseded baseline releases after confirming that the active manifest does not reference them. Generated workflow artifacts are diagnostic and expire after seven days.

## Sources and licensing

- Crom: https://crom.avn.sh/docs/a-primer-on-graphql
- Bulk metadata: https://github.com/scp-data/scp-api
- Canonical pages and licensing: https://scp-wiki.wikidot.com/

Generator code is MIT-licensed. SCP content and adaptations retain their own CC BY-SA 3.0 and per-item notices; see `CONTENT-LICENSE.md`. This project is unofficial and does not imply endorsement by the SCP Wiki, Crom, `scp-api`, or their contributors.
