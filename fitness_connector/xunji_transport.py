"""Shared JSON-over-HTTP transport for registered Xunji Open APIs."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
import re
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_PREFIXED_SECRET = re.compile(
    r"(?i)\b(?:xjllm|xjfood|xjbody)_[A-Za-z0-9]{12,}\b"
)
_AUTHORIZATION_SECRET = re.compile(
    r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9_.-]{16,}"
)


class XunjiOpenApiError(RuntimeError):
    """A fail-closed Open API transport or response error."""

    def __init__(self, message: str, *, retry_after_ms: int | None = None):
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


@dataclass(frozen=True)
class HttpResponse:
    payload: dict[str, Any]
    status: int


def redact_secrets(value: object, secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in sorted(
        {item for item in secrets if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, "<redacted>")
    text = _PREFIXED_SECRET.sub("<redacted>", text)
    return _AUTHORIZATION_SECRET.sub(r"\1<redacted>", text)


def decode_json_bytes(raw: bytes, encoding: str | None) -> dict[str, Any]:
    if encoding and "gzip" in encoding.lower():
        raw = gzip.decompress(raw)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XunjiOpenApiError("Xunji API returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise XunjiOpenApiError("Xunji API response root must be an object")
    return payload


def _retry_after_ms(payload: object, headers: object | None = None) -> int | None:
    if isinstance(payload, dict):
        value = payload.get("retry_after_ms")
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    if headers is not None:
        try:
            value = headers.get("Retry-After")
        except AttributeError:
            value = None
        if isinstance(value, str):
            try:
                return max(0, int(float(value) * 1000))
            except ValueError:
                pass
    return None


def _message_from_payload(payload: object) -> str:
    if isinstance(payload, dict):
        for field in ("message", "error", "msg"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


def post_json(
    *,
    url: str,
    body: dict[str, Any],
    credential: str,
    timeout: float = 30.0,
    user_agent: str = "fitness-agent-xunji-open-api/1.0",
    success_required: bool = False,
) -> HttpResponse:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = decode_json_bytes(
                response.read(), response.headers.get("Content-Encoding")
            )
            status = int(getattr(response, "status", 200))
    except HTTPError as exc:
        try:
            payload = decode_json_bytes(
                exc.read(), exc.headers.get("Content-Encoding")
            )
            message = _message_from_payload(payload)
        except XunjiOpenApiError:
            payload = {}
            message = f"HTTP {exc.code}"
        retry_after = _retry_after_ms(payload, exc.headers)
        clean = redact_secrets(message, (credential,))
        raise XunjiOpenApiError(
            f"Xunji API HTTP {exc.code}: {clean}",
            retry_after_ms=retry_after,
        ) from exc
    except URLError as exc:
        reason = redact_secrets(exc.reason, (credential,))
        raise XunjiOpenApiError(f"Xunji API request failed: {reason}") from exc

    if payload.get("success") is False:
        retry_after = _retry_after_ms(payload)
        message = redact_secrets(_message_from_payload(payload), (credential,))
        raise XunjiOpenApiError(
            f"Xunji API returned an error: {message}",
            retry_after_ms=retry_after,
        )
    if success_required and payload.get("success") is not True:
        raise XunjiOpenApiError("Xunji API response must contain success=true")
    if "res" not in payload:
        raise XunjiOpenApiError("Xunji API response does not contain 'res'")
    return HttpResponse(payload=payload, status=status)
