"""Translation loading with locale-file fallback."""
from __future__ import annotations

import json
import locale
from pathlib import Path
from typing import Any

__all__ = ['LOCALE_DIR', 'T', 'detect_lang_code', 'load_translations']

LOCALE_DIR = Path(__file__).resolve().parent.parent / 'locale'


def _locale_exists(code: str) -> bool:
    return (LOCALE_DIR / f'{code}.json').is_file()


def detect_lang_code(lang: str) -> str:
    """Return the best locale file name stem for a system locale string."""
    normalized = locale.normalize(lang or '').split('.', 1)[0]
    language, sep, region = normalized.partition('_')
    base = language.lower()
    if len(base) > 3:
        base = locale.normalize(language).split('.', 1)[0].partition('_')[0].lower()
    aliases = {'ukrainian': 'uk'}
    base = aliases.get(base, base)
    if sep and len(base) <= 3:
        regional = f'{base}-{region}'
        if _locale_exists(regional):
            return regional
    if _locale_exists(base):
        return base
    return 'en'


def _read_locale(code: str) -> dict[str, Any]:
    return json.loads((LOCALE_DIR / f'{code}.json').read_text(encoding='utf-8'))


def load_translations() -> dict[str, Any]:
    """Load the configured language, otherwise use the detected locale."""
    from .settings import LANGUAGE

    if LANGUAGE and _locale_exists(LANGUAGE):
        return _read_locale(LANGUAGE)
    return _read_locale(detect_lang_code(locale.getlocale()[0] or ''))


T: dict[str, Any] = load_translations()
