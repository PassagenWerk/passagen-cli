from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from passagen.config import LlmSettings, ProvidersSettings


class ProviderUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    statuses: dict[str, ProviderStatus]

    def require(self, name: str) -> None:
        status = self.statuses[name]
        if not status.available:
            raise ProviderUnavailableError(f"Provider {name} is unavailable: {status.detail}")


def check_provider_health(settings: ProvidersSettings) -> ProviderHealthSnapshot:
    timeout = settings.healthcheck_timeout_seconds
    checks = {
        "grobid": lambda: _grobid_status(settings.grobid.base_url, timeout),
        "llm": lambda: _llm_status(settings.llm, timeout),
    }
    statuses: dict[str, ProviderStatus] = {}
    if settings.crossref.enabled:
        checks["crossref"] = lambda: _http_status(
            settings.crossref.base_url.rstrip("/") + "/works",
            timeout,
            params={"rows": "0"},
        )
    else:
        statuses["crossref"] = ProviderStatus("crossref", False, "disabled by configuration")
    if settings.arxiv.enabled:
        checks["arxiv"] = lambda: _http_status(
            settings.arxiv.base_url.rstrip("/") + "/api/query",
            timeout,
            params={"search_query": "all:test", "max_results": "0"},
        )
    else:
        statuses["arxiv"] = ProviderStatus("arxiv", False, "disabled by configuration")
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = {name: executor.submit(check) for name, check in checks.items()}
        for name, future in futures.items():
            available, detail = future.result()
            statuses[name] = ProviderStatus(name, available, detail)
    return ProviderHealthSnapshot(statuses)


def _http_status(
    url: str,
    timeout: float,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, str(exc)
    return response.status_code < 500, f"HTTP {response.status_code}"


def _grobid_status(base_url: str, timeout: float) -> tuple[bool, str]:
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/isalive", timeout=timeout)
    except httpx.HTTPError as exc:
        return False, str(exc)
    available = response.is_success and response.text.strip().lower() == "true"
    return available, f"HTTP {response.status_code} body={response.text.strip()!r}"


def _llm_status(settings: LlmSettings, timeout: float) -> tuple[bool, str]:
    api_key_env = settings.api_key_env
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return False, f"environment variable {api_key_env} is not set"
    return _http_status(
        settings.base_url.rstrip("/") + "/models",
        timeout,
        headers={"Authorization": f"Bearer {api_key}"},
    )
