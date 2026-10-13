import re
from datetime import date

import pytest

from licita.i18n import DEFAULT_LANG, LANGS, STRINGS, fmt_date, fmt_eur, fmt_num, fmt_pct, t


def test_default_is_spanish():
    assert DEFAULT_LANG == "es"
    assert t("nav.home") == "Portada"


@pytest.mark.parametrize("key", sorted(STRINGS))
def test_every_key_has_every_language(key):
    for lang in LANGS:
        assert lang in STRINGS[key], f"{key} missing {lang}"


@pytest.mark.parametrize("key", sorted(STRINGS))
def test_placeholders_match_across_languages(key):
    ph = {lang: set(re.findall(r"{(\w+)}", STRINGS[key][lang])) for lang in LANGS}
    # Spanish-only gloss is allowed to be empty; otherwise placeholders must agree.
    if all(STRINGS[key][lang] for lang in LANGS):
        assert ph["es"] == ph["en"], key


def test_status_labels_carry_a_glyph():
    for key in [k for k in STRINGS if k.startswith("status.") and k != "status.pending"]:
        for lang in LANGS:
            assert STRINGS[key][lang][0] in "▲◆●", (key, lang)


def test_unknown_key_and_language_fail_loudly():
    with pytest.raises(KeyError):
        t("does.not.exist")
    with pytest.raises(ValueError):
        t("nav.home", "fr")


def test_number_formats():
    assert fmt_num(1234567.8, "es") == "1.234.568"
    assert fmt_num(1234567.8, "en") == "1,234,568"
    assert fmt_num(4.75, "es", 1) == "4,8"
    assert fmt_eur(72000, "es") == "72.000 €"
    assert fmt_eur(72000, "en") == "€72,000"
    assert fmt_pct(0.047, "es") == "4,7 %"
    assert fmt_pct(0.166, "en") == "16.6%"
    assert fmt_eur(-1500, "es") == "-1.500 €"


def test_dates():
    d = date(2026, 10, 8)
    assert fmt_date(d, "es") == "08/10/2026"
    assert fmt_date(d, "en") == "8 Oct 2026"


def test_formatting_interpolates():
    assert t("memo.plus_one", "es", eur=fmt_eur(5800, "es")) == "+1 licitador ≈ 5.800 €/año"
    assert t("memo.plus_one", "en", eur=fmt_eur(5800, "en")) == "+1 bidder ≈ €5,800/year"
