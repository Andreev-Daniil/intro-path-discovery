"""Подмена HTTP для тестов источников: URL (+ ключевые параметры) -> заготовленный ответ."""

from __future__ import annotations

import json


class FakeHttp:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def _find(self, url: str, params: dict | None):
        self.calls.append((url, params))
        for key, value in self.routes.items():
            if url == key or url.startswith(key):
                return value(params) if callable(value) else value
        raise AssertionError(f"неожиданный запрос: {url} {params}")

    def get_json(self, url, params=None):
        value = self._find(url, params)
        return json.loads(value) if isinstance(value, str) else value

    def get_text(self, url, params=None):
        value = self._find(url, params)
        return value if isinstance(value, str) else json.dumps(value)


FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>1</rptOwnerCik><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>{director}</isDirector><isOfficer>{officer}</isOfficer>
      <officerTitle>{title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>"""


def form4(name, director=0, officer=0, title=""):
    return FORM4_XML.format(name=name, director=director, officer=officer, title=title)
