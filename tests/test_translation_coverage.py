"""Catch catalog gaps and user-facing literals before they reach Telegram."""

import ast
from pathlib import Path

from smartlead.i18n import SUPPORTED_LANGUAGES, get_catalog

SOURCE = Path(__file__).resolve().parents[1] / "src" / "smartlead"


def test_every_literal_translation_and_business_error_key_exists_in_every_catalog():
    required = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or not call.args
            ):
                continue
            first = call.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            if call.func.id == "tr" or call.func.id in {
                "ValidationError",
                "AccessDenied",
                "NotFound",
                "InvalidTransition",
                "SlotUnavailable",
            }:
                required.add(first.value)
    required.update(
        f"command_{name}"
        for name in (
            "start",
            "menu",
            "language",
            "cancel",
            "help",
            "id",
            "admin",
        )
    )
    assert required
    for language in SUPPORTED_LANGUAGES:
        assert required <= get_catalog(language).keys(), (
            language,
            required - get_catalog(language).keys(),
        )


def test_handlers_do_not_embed_user_facing_english_sentences():
    # Callback IDs, logging and docstrings may remain English. Human-readable
    # literals passed directly to Telegram, a keyboard, or an error must not.
    for filename in ("client.py", "admin.py", "keyboards.py", "middleware.py"):
        path = SOURCE / "bot" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Attribute) and call.func.attr in {
                "answer",
                "edit_text",
                "send_message",
            }:
                if call.args:
                    assert not isinstance(call.args[0], (ast.Constant, ast.JoinedStr)), (
                        filename,
                        call.lineno,
                    )
            if isinstance(call.func, ast.Name) and call.func.id == "InlineKeyboardButton":
                for keyword in call.keywords:
                    if keyword.arg == "text":
                        assert not isinstance(keyword.value, (ast.Constant, ast.JoinedStr)), (
                            filename,
                            call.lineno,
                        )
