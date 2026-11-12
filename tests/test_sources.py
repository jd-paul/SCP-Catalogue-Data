from dataclasses import replace

import pytest

from scp_catalogue_data.models import ValidationError, validate_articles
from scp_catalogue_data.sources import article_from_crom, article_from_scp_api, merge_catalogue
from conftest import NOW


def test_scp_api_creator_is_an_honest_submitter():
    article = article_from_scp_api(
        {
            "link": "scp-173",
            "page_id": "1956234",
            "url": "https://scp-wiki.wikidot.com/scp-173",
            "title": "SCP-173",
            "creator": "Lt Masipag",
            "created_at": "2008-07-25T20:49:21",
            "rating": 11167,
            "series": 1,
            "tags": ["scp", "euclid"],
            "history": [{"author": "Lt Masipag"}],
        },
        "scp",
        NOW,
    )

    assert article.id == "wikidot:1956234"
    assert article.attributions[0].type == "SUBMITTER"
    assert article.attributions[0].name == "Lt Masipag"
    assert not article.attributions[0].is_current
    assert article.series == 1


def test_crom_repairs_attribution_title_and_preserves_series(crom_node):
    base = article_from_scp_api(
        {
            "link": "scp-173",
            "page_id": "1956234",
            "url": "https://scp-wiki.wikidot.com/scp-173",
            "title": "SCP-173",
            "creator": "Lt Masipag",
            "created_at": "2008-07-25T20:49:21",
            "rating": 1,
            "series": 1,
            "tags": ["scp"],
        },
        "scp",
        NOW,
    )

    article = article_from_crom(crom_node, NOW, base)

    assert article.display_title == "The Sculpture - The Original"
    assert article.attributions[0].name == "Moto42"
    assert article.attributions[0].type == "AUTHOR"
    assert article.rating == 11167
    assert article.revision_count == 57
    assert article.series == 1
    assert article.canonical_url == "https://scp-wiki.wikidot.com/scp-173"


def test_merge_adds_crom_coverage_gaps(crom_node):
    crom_node["wikidotInfo"]["tags"].append("crom:series-1")
    merged = merge_catalogue([], [crom_node], NOW)
    assert [item.id for item in merged] == ["wikidot:1956234"]
    assert merged[0].series == 1


def test_tale_year_comes_from_creation_date(crom_node):
    crom_node["url"] = "http://scp-wiki.wikidot.com/example-tale"
    crom_node["alternateTitles"] = []
    crom_node["wikidotInfo"].update(
        wikidotId=42,
        title="A Tale",
        tags=["tale"],
        createdAt="2012-03-01T00:00:00Z",
    )
    article = article_from_crom(crom_node, NOW)
    assert article.kind == "tale"
    assert article.tale_year == 2012


def test_duplicate_identities_and_missing_coverage_fail(article):
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_articles([article, article])
    with pytest.raises(ValidationError, match="missing"):
        validate_articles([], expected_identities={article.id})


def test_dangerous_or_non_scp_urls_fail(crom_node):
    crom_node["url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError, match="Unsupported"):
        article_from_crom(crom_node, NOW)


def test_url_fallback_identity_does_not_use_scp_label(crom_node):
    crom_node["wikidotInfo"]["wikidotId"] = None
    article = article_from_crom(crom_node, NOW)
    assert article.id == article.canonical_url
    assert article.id != "SCP-173"


def test_changing_content_does_not_change_latest_order_key(article):
    edited = replace(article, revision_count=article.revision_count + 1)
    assert edited.created_at == article.created_at
