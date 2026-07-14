"""Live REST client for the Ataccama ONE public API.

Handles OAuth2 client-credentials auth (tokens are short-lived, ~5 min, so we mint
and refresh them automatically) and cursor-based pagination (``after``/``size``).
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from ataccama_osi.models import CatalogAttribute, CatalogItem, CatalogItemBundle, Term

DEFAULT_PAGE_SIZE = 200
# Refresh the token this many seconds before it actually expires.
TOKEN_EXPIRY_SKEW_S = 30


class AtaccamaClient:
    """Minimal client for the endpoints the OSI importer needs."""

    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        # base_url is the API root, e.g. https://<host>/api
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._session = session or requests.Session()
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # --- auth ---

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - TOKEN_EXPIRY_SKEW_S:
            return self._token
        resp = self._session.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + float(payload.get("expires_in", 300))
        return self._token

    # --- low-level HTTP ---

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._session.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Iterate every record across pages, following the ``meta.next`` cursor."""
        params = dict(params or {})
        params.setdefault("size", DEFAULT_PAGE_SIZE)
        after: str | None = None
        while True:
            if after is not None:
                params["after"] = after
            body = self._get(path, params)
            yield from body.get("data", [])
            after = (body.get("meta") or {}).get("next")
            if not after:
                break

    # --- catalog endpoints ---

    def get_catalog_item(self, urn: str) -> CatalogItem:
        return CatalogItem.from_dict(self._get(f"/catalog/v1/catalog-items/{urn}"))

    def get_attributes(self, catalog_item_urn: str) -> list[CatalogAttribute]:
        return [
            CatalogAttribute.from_dict(a)
            for a in self._paged("/catalog/v1/attributes", {"catalogItemUrn": catalog_item_urn})
        ]

    def get_term(self, urn: str) -> Term:
        return Term.from_dict(self._get(f"/catalog/v1/terms/{urn}"))

    # --- data-quality endpoint ---

    def get_dq_results(
        self, catalog_item_urn: str, monitor: str = "primary", processing: str = "latest"
    ) -> dict | None:
        """Latest DQ results for a catalog item's primary monitor.

        Returns the raw ``DqResults`` payload (overall + per-dimension + per-attribute
        quality), or ``None`` if the item has no monitor / published results.
        """
        path = (
            f"/data-quality/v1/catalog-items/{catalog_item_urn}"
            f"/dq-monitors/{monitor}/processings/{processing}/dq-results"
        )
        try:
            return self._get(path)
        except requests.HTTPError:
            return None

    def get_dq_overall_threshold(self, catalog_item_urn: str, monitor: str = "primary") -> float | None:
        """The monitor's configured overall DQ threshold (0-100), or None if unset.

        This is the pass/fail bar shown in Ataccama, and is only available from the
        monitor-config endpoint (not the DQ results payload).
        """
        try:
            mon = self._get(f"/data-quality/v1/catalog-items/{catalog_item_urn}/dq-monitors/{monitor}")
        except requests.HTTPError:
            return None
        thresholds = mon.get("overallDqThresholds") or []
        return thresholds[0].get("value") if thresholds else None

    # --- composite ---

    def fetch_bundle(self, catalog_item_urn: str, *, with_dq: bool = True) -> CatalogItemBundle:
        """Fetch a catalog item, its attributes, referenced terms, and (by default) DQ results."""
        item = self.get_catalog_item(catalog_item_urn)
        attributes = self.get_attributes(catalog_item_urn)

        term_urns: set[str] = {ta.term_urn for ta in item.term_assignments}
        for attr in attributes:
            term_urns.update(ta.term_urn for ta in attr.term_assignments)

        terms: dict[str, Term] = {}
        for urn in sorted(term_urns):
            if not urn:
                continue
            try:
                terms[urn] = self.get_term(urn)
            except requests.HTTPError:
                # A referenced term may be inaccessible; skip it rather than fail the whole run.
                continue

        dq_results = None
        dq_threshold_pct = None
        if with_dq:
            dq_results = self.get_dq_results(catalog_item_urn)
            if dq_results is not None:
                dq_threshold_pct = self.get_dq_overall_threshold(catalog_item_urn)

        return CatalogItemBundle(
            item=item,
            attributes=attributes,
            terms=terms,
            dq_results=dq_results,
            dq_threshold_pct=dq_threshold_pct,
        )
