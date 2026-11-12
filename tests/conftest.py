from datetime import datetime, timezone

import pytest

from scp_catalogue_data.models import Article, Attribution

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


@pytest.fixture
def crom_node():
    return {
        "url": "http://scp-wiki.wikidot.com/scp-173",
        "alternateTitles": [{"title": "The Sculpture - The Original"}],
        "attributions": [
            {
                "type": "AUTHOR",
                "date": "2008-07-25T20:49:21Z",
                "order": 0,
                "isCurrent": True,
                "user": {"name": "Moto42"},
            }
        ],
        "wikidotInfo": {
            "title": "SCP-173",
            "wikidotId": 1956234,
            "rating": 11167,
            "tags": ["scp", "euclid", "horror"],
            "createdAt": "2008-07-25T20:49:21Z",
            "revisionCount": 57,
        },
    }


@pytest.fixture
def article():
    return Article(
        id="wikidot:1956234",
        wikidot_id=1956234,
        canonical_url="https://scp-wiki.wikidot.com/scp-173",
        kind="scp",
        wiki_title="SCP-173",
        display_title="The Sculpture - The Original",
        attributions=(Attribution(type="AUTHOR", name="Moto42", order=0, is_current=True),),
        tags=("001-proposal", "euclid", "scp"),
        rating=11167,
        rating_fetched_at=NOW,
        created_at=datetime(2008, 7, 25, 20, 49, 21, tzinfo=timezone.utc),
        revision_count=57,
        object_class="Euclid",
        series=1,
    )
