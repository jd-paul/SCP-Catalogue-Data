import json

import httpx
import pytest

from scp_catalogue_data.crom import CROM_ENDPOINT, CromClient, CromError


def payload(nodes, *, has_next=False, cursor=None, remaining=100):
    return {
        "data": {
            "pages": {
                "edges": [{"cursor": str(index), "node": node} for index, node in enumerate(nodes)],
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            },
            "rateLimit": {"cost": 200, "limit": 10000, "remaining": remaining, "resetAt": "2026-11-11T13:00:00Z"},
        }
    }


def response(value, status=200, headers=None):
    return httpx.Response(status, json=value, headers=headers)


def test_latest_uses_documented_query(crom_node):
    requests = []

    def handler(request):
        requests.append(request)
        return response(payload([crom_node]))

    client = CromClient(httpx.MockTransport(handler))
    nodes = client.fetch_latest()
    body = json.loads(requests[0].content)

    assert requests[0].url == CROM_ENDPOINT
    assert body["variables"] == {"after": None}
    assert "CREATED_AT" in body["query"]
    assert nodes == [crom_node]


def test_pagination_returns_all_records_and_reuses_server_cursor(crom_node):
    cursors = []

    def handler(request):
        body = json.loads(request.content)
        cursors.append(body["variables"]["after"])
        if body["variables"]["after"] is None:
            return response(payload([crom_node], has_next=True, cursor="server-cursor"))
        return response(payload([crom_node]))

    client = CromClient(httpx.MockTransport(handler))
    assert len(list(client.iter_catalogue())) == 2
    assert cursors == [None, "server-cursor"]


def test_graphql_errors_fail_even_with_http_200():
    client = CromClient(httpx.MockTransport(lambda _: response({"data": {"pages": {}}, "errors": [{"message": "partial"}]})))
    with pytest.raises(CromError, match="GraphQL"):
        client.fetch_latest()


def test_rate_limited_transport_honors_retry_after(crom_node):
    calls = 0
    sleeps = []

    def handler(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response({}, status=429, headers={"Retry-After": "7"})
        return response(payload([crom_node]))

    client = CromClient(httpx.MockTransport(handler), sleeper=sleeps.append, jitter=lambda: 0)
    assert client.fetch_latest() == [crom_node]
    assert sleeps == [7]


def test_repeated_cursor_fails_instead_of_looping(crom_node):
    client = CromClient(
        httpx.MockTransport(lambda _: response(payload([crom_node], has_next=True, cursor="same")))
    )
    with pytest.raises(CromError, match="repeated"):
        list(client.iter_catalogue())


def test_response_size_limit_is_enforced(crom_node):
    client = CromClient(httpx.MockTransport(lambda _: response(payload([crom_node]))), max_response_bytes=20)
    with pytest.raises(CromError, match="size limit"):
        client.fetch_latest()
