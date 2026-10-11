"""Small catalog-backed translations for SmartLead user-facing text."""

from __future__ import annotations

import json
import string
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType

SUPPORTED_LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "ru": "Русский",
    "uk": "Українська",
    "de": "Deutsch",
    "ja": "日本語",
}

LOCALE_BY_LANGUAGE = {
    "en": "en_US",
    "fr": "fr_FR",
    "ru": "ru_RU",
    "uk": "uk_UA",
    "de": "de_DE",
    "ja": "ja_JP",
}

_current_language: ContextVar[str] = ContextVar("smartlead_language", default="en")


def normalize_language(language: str | None) -> str:
    """Normalize a language tag to one of the supported base language codes."""
    if not isinstance(language, str):
        return "en"
    code = language.strip().replace("_", "-").split("-", 1)[0].lower()
    return code if code in SUPPORTED_LANGUAGES else "en"


def current_language() -> str:
    """Return the language active in this async or synchronous context."""
    return _current_language.get()


@contextmanager
def use_language(language: str | None) -> Iterator[None]:
    """Set the current language for a block and restore its prior value afterward."""
    token: Token[str] = _current_language.set(normalize_language(language))
    try:
        yield
    finally:
        _current_language.reset(token)


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _load_catalog(language: str) -> Mapping[str, str]:
    resource = files("smartlead").joinpath("locales", language, "messages.json")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load the {language!r} translation catalog") from exc
    if not isinstance(raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError(f"The {language!r} translation catalog must map strings to strings")
    return MappingProxyType(raw)


def get_catalog(language: str | None = None) -> Mapping[str, str]:
    """Load one catalog, using the current context language when omitted."""
    code = current_language() if language is None else normalize_language(language)
    return _load_catalog(code)


def tr(key: str, *, language: str | None = None, **variables: object) -> str:
    """Translate a key and format its named variables without hiding missing values."""
    code = current_language() if language is None else normalize_language(language)
    template = get_catalog(code).get(key)
    if template is None and code != "en":
        template = get_catalog("en").get(key)
    if template is None:
        return key

    rendered = template.format_map(variables)
    expected = {field for _, field, _, _ in string.Formatter().parse(template) if field}
    unused = variables.keys() - expected
    if unused:
        names = ", ".join(sorted(unused))
        raise TypeError(f"Unused translation variables for {key!r}: {names}")
    return rendered


def _template_variables(template: str, *, key: str, language: str) -> set[str]:
    try:
        return {field for _, field, _, _ in string.Formatter().parse(template) if field}
    except ValueError as exc:
        raise ValueError(f"Invalid template for {key!r} in {language!r}") from exc


def validate_catalogs() -> None:
    """Raise ValueError if catalogs differ in keys or named template variables."""
    english = get_catalog("en")
    english_keys = set(english)
    errors: list[str] = []
    english_variables = {
        key: _template_variables(value, key=key, language="en") for key, value in english.items()
    }

    for language in SUPPORTED_LANGUAGES:
        catalog = get_catalog(language)
        keys = set(catalog)
        missing = sorted(english_keys - keys)
        extra = sorted(keys - english_keys)
        if missing:
            errors.append(f"{language}: missing keys: {', '.join(missing)}")
        if extra:
            errors.append(f"{language}: extra keys: {', '.join(extra)}")
        for key in sorted(english_keys & keys):
            variables = _template_variables(catalog[key], key=key, language=language)
            if variables != english_variables[key]:
                errors.append(
                    f"{language}: placeholders for {key!r} are {sorted(variables)}, "
                    f"expected {sorted(english_variables[key])}"
                )

    if errors:
        raise ValueError("Invalid translation catalogs:\n" + "\n".join(errors))
