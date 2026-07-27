from __future__ import annotations

import json
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


class HttpClientError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def get_text(self, url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> str:
        return self._request(url=url, timeout=timeout, headers=headers)

    def get_json(self, url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> Any:
        payload = self._request(url=url, timeout=timeout, headers=headers)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HttpClientError(f"Invalid JSON from {url}") from exc

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: int = 60,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self._request(
            url=url,
            timeout=timeout,
            headers={"Content-Type": "application/json", **(headers or {})},
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            raise HttpClientError(f"Invalid JSON from {url}") from exc

    def _request(
        self,
        url: str,
        timeout: int,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        method: str | None = None,
    ) -> str:
        request_headers = {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self.user_agent,
        }
        request_headers.update(headers or {})
        request = Request(
            url,
            headers=request_headers,
            data=data,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise HttpClientError(f"HTTP {exc.code} while fetching {url}") from exc
        except URLError as exc:
            raise HttpClientError(f"Network error while fetching {url}: {exc.reason}") from exc
