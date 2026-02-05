"""
Адаптер SberJazz/SaluteJazz.

Назначение:
- подключение к внешней платформе встреч через HTTP API
- базовый join/leave/fetch_recording контракт
"""

from __future__ import annotations

import time
from typing import Any

import requests

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, ProviderError
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.connectors.base import MeetingConnector, MeetingContext

log = get_project_logger()


class SaluteJazzConnector(MeetingConnector):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.sberjazz_api_base or "").rstrip("/")
        self.api_token = (api_token or s.sberjazz_api_token or "").strip()
        self.timeout_sec = int(timeout_sec if timeout_sec is not None else s.sberjazz_timeout_sec)
        self.http_retries = max(0, int(getattr(s, "sberjazz_http_retries", 2)))
        self.http_retry_backoff_sec = (
            max(0, int(getattr(s, "sberjazz_http_retry_backoff_ms", 300))) / 1000.0
        )
        self.http_retry_statuses = self._parse_retry_statuses(
            str(getattr(s, "sberjazz_http_retry_statuses", "408,409,425,429,500,502,503,504"))
        )

    @staticmethod
    def _parse_retry_statuses(raw: str) -> set[int]:
        out: set[int] = set()
        for item in (raw or "").split(","):
            value = item.strip()
            if not value:
                continue
            try:
                out.add(int(value))
            except ValueError:
                continue
        return out

    @staticmethod
    def _safe_response_text(resp: requests.Response, max_len: int = 300) -> str:
        try:
            text = resp.text or ""
            return text[:max_len]
        except Exception:
            return ""

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in self.http_retry_statuses

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        if not self.base_url:
            raise ProviderError(
                ErrCode.CONNECTOR_BAD_REQUEST,
                "SBERJAZZ_API_BASE не настроен",
            )

        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        attempts = self.http_retries + 1
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(1, attempts + 1):
            try:
                resp = requests.request(
                    method=method.upper(),
                    url=url,
                    json=payload,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_sec,
                )
            except requests.Timeout as e:
                last_error = str(e)
                if attempt < attempts:
                    time.sleep(self.http_retry_backoff_sec * attempt)
                    continue
                raise ProviderError(
                    ErrCode.CONNECTOR_TIMEOUT,
                    "Таймаут обращения к SberJazz API",
                    details={"err": last_error, "attempts": attempts, "url": url},
                ) from e
            except requests.ConnectionError as e:
                last_error = str(e)
                if attempt < attempts:
                    time.sleep(self.http_retry_backoff_sec * attempt)
                    continue
                raise ProviderError(
                    ErrCode.CONNECTOR_UNAVAILABLE,
                    "SberJazz API недоступен",
                    details={"err": last_error, "attempts": attempts, "url": url},
                ) from e
            except requests.RequestException as e:
                raise ProviderError(
                    ErrCode.CONNECTOR_PROVIDER_ERROR,
                    "Ошибка обращения к SberJazz API",
                    details={"err": str(e), "url": url},
                ) from e

            last_status = resp.status_code
            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    data = resp.json()
                    return data if isinstance(data, dict) else {}
                except ValueError:
                    raise ProviderError(
                        ErrCode.CONNECTOR_INVALID_RESPONSE,
                        "SberJazz API вернул невалидный JSON",
                        details={"status_code": resp.status_code, "url": url},
                    ) from None

            body = self._safe_response_text(resp)
            if resp.status_code in {401, 403}:
                raise ProviderError(
                    ErrCode.CONNECTOR_AUTH_ERROR,
                    "Ошибка авторизации SberJazz API",
                    details={"status_code": resp.status_code, "body": body, "url": url},
                )

            if self._should_retry_status(resp.status_code) and attempt < attempts:
                time.sleep(self.http_retry_backoff_sec * attempt)
                continue

            if resp.status_code == 429:
                raise ProviderError(
                    ErrCode.CONNECTOR_RATE_LIMIT,
                    "Превышен лимит запросов SberJazz API",
                    details={"status_code": resp.status_code, "body": body, "url": url},
                )

            if resp.status_code in {400, 404, 422}:
                raise ProviderError(
                    ErrCode.CONNECTOR_BAD_REQUEST,
                    "Неверный запрос к SberJazz API",
                    details={"status_code": resp.status_code, "body": body, "url": url},
                )

            if resp.status_code >= 500:
                raise ProviderError(
                    ErrCode.CONNECTOR_UNAVAILABLE,
                    "SberJazz API временно недоступен",
                    details={"status_code": resp.status_code, "body": body, "url": url},
                )

            raise ProviderError(
                ErrCode.CONNECTOR_PROVIDER_ERROR,
                "SberJazz API вернул ошибку",
                details={"status_code": resp.status_code, "body": body, "url": url},
            )

        raise ProviderError(
            ErrCode.CONNECTOR_UNAVAILABLE,
            "Ошибка обращения к SberJazz API",
            details={"status_code": last_status, "err": last_error, "url": url},
        )

    def health(self) -> dict:
        return self._request("GET", "/api/v1/health")

    def join(self, meeting_id: str) -> MeetingContext:
        data = self._request("POST", f"/api/v1/meetings/{meeting_id}/join")
        participants = data.get("participants")
        if not isinstance(participants, list):
            participants = None
        language = str(data.get("language") or "ru")

        log.info("sberjazz_join_ok", extra={"payload": {"meeting_id": meeting_id}})
        return MeetingContext(meeting_id=meeting_id, language=language, participants=participants)

    def leave(self, meeting_id: str) -> None:
        self._request("POST", f"/api/v1/meetings/{meeting_id}/leave")
        log.info("sberjazz_leave_ok", extra={"payload": {"meeting_id": meeting_id}})

    def fetch_recording(self, meeting_id: str):
        data = self._request("GET", f"/api/v1/meetings/{meeting_id}/recording")
        log.info("sberjazz_fetch_recording_ok", extra={"payload": {"meeting_id": meeting_id}})
        return data or None

    def fetch_live_chunks(
        self, meeting_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> dict | None:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", f"/api/v1/meetings/{meeting_id}/live-chunks", params=params)
        return data or {}
