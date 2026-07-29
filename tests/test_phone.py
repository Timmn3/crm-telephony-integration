"""Тесты нормализации и маскирования телефона."""
import pytest

from app.utils.phone import mask_phone, normalize_phone, strip_phones


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


# --- strip_phones: защита от утечки телефона в текст карточки ---

@pytest.mark.parametrize("raw", [
    "+79114053330",                         # тот самый номер из бага
    "89114053330",
    "79114053330",
    "8 (911) 405-33-30",
    "8911-405-33-30",
    "Иванов, тел 89114053330",              # номер внутри фразы
    "звоните +7 911 405 33 30 в любое время",
])
def test_strip_phones_masks(raw):
    out = strip_phones(raw)
    assert "[номер скрыт]" in out
    # ни одной длинной цепочки цифр номера не осталось
    assert "9114053330" not in out
    assert "4053330" not in out


@pytest.mark.parametrize("raw", [
    "Замер окон",
    "ул. Ленина, д. 12, кв. 5",
    "подъезд 3, этаж 7",
    "",
])
def test_strip_phones_keeps_plain_text(raw):
    assert strip_phones(raw) == raw


def test_strip_phones_none():
    assert strip_phones(None) is None


# --- mask_phone: частичное скрытие номера для логов ---

@pytest.mark.parametrize("raw,expected", [
    ("79991234567", "7***4567"),
    ("+7 (999) 123-45-67", "7***4567"),
    ("74950000001", "7***0001"),
])
def test_mask_phone_keeps_only_tail(raw, expected):
    out = mask_phone(raw)
    assert out == expected
    # середина номера в лог не попадает
    assert "999123" not in out


@pytest.mark.parametrize("raw,expected", [(None, "—"), ("", "—"), ("123", "***")])
def test_mask_phone_edge_cases(raw, expected):
    assert mask_phone(raw) == expected
