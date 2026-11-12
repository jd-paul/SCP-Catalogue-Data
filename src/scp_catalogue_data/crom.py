from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator

import httpx

from .models import ValidationError, utc_datetime

CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql"
USER_AGENT = "SCP-Catalogue-Feed-Generator/0.1 (https://github.com/jd-paul/SCP-Catalogue-Data)"

PAGE_QUERY = """
query Catalogue($after: ID) {
  pages(
    first: 50
    after: $after
    sort: { key: CREATED_AT, order: DESC }
    filter: {
      url: { startsWith: "http://scp-wiki.wikidot.com/" }
      wikidotInfo: {
        _and: [
          { isPrivate: false }
          {
            _or: [
              { tags: { eq: "scp" } }
              { tags: { eq: "tale" } }
            ]
          }
        ]
      }
    }
  ) {
    edges {
      cursor
      node {
        url
        alternateTitles { title }
        attributions {
          type
          date
          order
          isCurrent
          user { name }
        }
        wikidotInfo {
          title
          wikidotId
          rating
          voteCount
          commentCount
          tags
          createdAt
          revisionCount
          thumbnailUrl
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
  rateLimit {
    cost
    limit
    remaining
    resetAt
  }
}
"""


class CromError(RuntimeError):
    pass


class CromClient:
    def __init__(
        self,
        client: httpx.Client | httpx.BaseTransport | None = None,
        *,
        max_retries: int = 3,
        max_response_bytes: int = 5_000_000,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if isinstance(client, httpx.BaseTransport):
            client = httpx.Client(transport=client)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes
        self._sleep = sleeper
        self._jitter = jitter

    def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                status, headers, body = self._post(query, variables)
            except httpx.TransportError as error:
                if attempt == self._max_retries:
                    raise CromError(f"Crom transport failed: {error}") from error
                self._sleep((2**attempt) + self._jitter())
                continue
            if status == 429 or status >= 500:
                if attempt == self._max_retries:
                    raise CromError(f"Crom returned HTTP {status} after retries")
                self._sleep(self._retry_delay(headers.get("Retry-After"), attempt))
                continue
            if status != 200:
                raise CromError(f"Crom returned HTTP {status}")
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CromError("Crom returned invalid JSON") from error
            if payload.get("errors"):
                serialized_errors = json.dumps(payload["errors"])
                wait = re.search(r"Please wait for (\d+(?:\.\d+)?) seconds", serialized_errors)
                if "requests too often" in serialized_errors and wait and attempt < self._max_retries:
                    self._sleep(float(wait.group(1)) + self._jitter())
                    continue
                raise CromError(f"Crom GraphQL errors: {serialized_errors[:2000]}")
            data = payload.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("pages"), dict):
                raise CromError("Crom response is missing complete page data")
            return data
        raise AssertionError("unreachable")

    def fetch_latest(self) -> list[dict[str, Any]]:
        data = self.query(PAGE_QUERY, {"after": None})
        nodes, page_info = self._page_data(data)
        if len(nodes) > 50:
            raise CromError("Crom returned more than 50 Latest records")
        if not nodes and page_info.get("hasNextPage"):
            raise CromError("Crom returned an empty page with a continuation")
        return nodes

    def iter_catalogue(self) -> Iterator[dict[str, Any]]:
        after = None
        seen_cursors = set()
        while True:
            data = self.query(PAGE_QUERY, {"after": after})
            nodes, page_info = self._page_data(data)
            yield from nodes
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise CromError("Crom returned an invalid or repeated continuation cursor")
            seen_cursors.add(cursor)
            after = cursor
            self._respect_rate_limit(data.get("rateLimit"))

    def _post(self, query: str, variables: dict[str, Any] | None) -> tuple[int, httpx.Headers, bytes]:
        with self._client.stream(
            "POST",
            CROM_ENDPOINT,
            json={"query": query, "variables": variables or {}},
        ) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self._max_response_bytes:
                    raise CromError("Crom response exceeds the configured size limit")
            return response.status_code, response.headers, bytes(body)

    @staticmethod
    def _page_data(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        pages = data["pages"]
        edges = pages.get("edges")
        page_info = pages.get("pageInfo")
        if not isinstance(edges, list) or not isinstance(page_info, dict):
            raise CromError("Crom page response has an invalid shape")
        nodes = [edge.get("node") for edge in edges if isinstance(edge, dict)]
        if len(nodes) != len(edges) or any(not isinstance(node, dict) for node in nodes):
            raise CromError("Crom page response contains an invalid edge")
        return nodes, page_info

    def _retry_delay(self, retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    target = parsedate_to_datetime(retry_after)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError):
                    pass
        return (2**attempt) + self._jitter()

    def _respect_rate_limit(self, rate_limit: Any) -> None:
        if not isinstance(rate_limit, dict) or not isinstance(rate_limit.get("resetAt"), str):
            return
        remaining = rate_limit.get("remaining")
        cost = rate_limit.get("cost")
        if not isinstance(remaining, (int, float)) or not isinstance(cost, (int, float)) or remaining >= cost:
            return
        reset_at = utc_datetime(rate_limit["resetAt"])
        delay = max(0.0, (reset_at - datetime.now(timezone.utc)).total_seconds())
        self._sleep(delay + self._jitter())
