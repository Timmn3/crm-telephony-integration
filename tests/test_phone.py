"""Тесты нормализации телефона."""
import pytest

from app.utils.phone import normalize_phone


@pytest.mark.parametrize("raw,expected", [
    ("+7 (999) 123-45-67", "79991234567"),
    ("8 999 123 45 67", "79991234567"),
    ("9991234567", "79991234567"),
    ("7999 123 45 67", "79991234567"),
    ("89991234567", "79991234567"),
    ("+79991234567", "79991234567"),
])
def test_valid(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123", "", None, "abcdef", "12345678901234"])
def test_invalid(raw):
    assert normalize_phone(raw) is None
