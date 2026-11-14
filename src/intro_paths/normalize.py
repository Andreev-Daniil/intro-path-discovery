"""Нормализация названий для сравнения (минимальный entity resolution).

Это грубая версия: убирает регистр, ё/е, кавычки, организационно-правовые формы.
Синонимы вида «Сбер» и «Сбербанк» здесь не склеиваются — для этого нужен
словарь алиасов или LLM-матчинг с порогом уверенности и ручной проверкой.
"""

from __future__ import annotations

import re
import unicodedata

_LEGAL_FORMS = {
    "ооо", "ао", "пао", "зао", "оао", "ип",
    "llc", "inc", "ltd", "gmbh", "jsc", "ooo", "pao",
}

_PUNCT = re.compile(r"[\"'«»“”„()\[\],.]")


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_org(name: str | None) -> str:
    tokens = normalize_text(name).replace("-", " ").split()
    return " ".join(t for t in tokens if t not in _LEGAL_FORMS)
