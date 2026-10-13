from licita.profile import load_profile
from licita.spine.regions import coverage_score, normalise_city, region_for


def test_nuts_wins_when_spanish_and_long_enough():
    prof = load_profile()
    assert region_for("ES995", "madrid", "comunidad de ejemplo > x", prof) == "ES99"
    assert region_for("es30", None, None, prof) == "ES30"


def test_profile_chain_beats_city_and_short_nuts():
    prof = load_profile()        # test profile: nuts_prefix ES99, buyer_chain_re "comunidad de ejemplo"
    assert region_for("ES", "madrid", "Hospital X > Comunidad de Ejemplo", prof) == "ES99"
    assert region_for(None, "madrid", "Consejería > COMUNIDAD DE EJEMPLO", prof) == "ES99"


def test_city_fallback_without_accents_or_case():
    assert region_for(None, "MADRID", "", None) == "ES30"
    assert region_for("ESZZ", "  Alcalá de Henares ", "otra cadena", load_profile()) == "ES30"
    assert normalise_city("L'Hospitalet de Llobregat") == "l'hospitalet de llobregat"


def test_city_table_covers_every_region():
    """A fact table that listed every region but one would point at the deployment's region."""
    from licita.spine.regions import CITY_NUTS2
    nuts2_suffixes = (11, 12, 13, 21, 22, 23, 24, 30, 41, 42, 43, 51, 52, 53, 61, 62, 63, 64, 70)
    assert set(CITY_NUTS2.values()) == {f"ES{n}" for n in nuts2_suffixes}
    assert all(c == c.lower().strip() for c in CITY_NUTS2)


def test_city_table_depth_is_even_across_regions():
    """One population rule for every region: no region may be enumerated deeper than the others."""
    from collections import Counter

    from licita.spine.regions import CITY_NUTS2
    counts = Counter(CITY_NUTS2.values())
    assert max(counts.values()) <= 16 and min(counts.values()) >= 1
    big = sorted(counts.values())[-3:]            # the deepest regions are the three with most large cities
    assert all(counts[c] in big for c in ("ES51", "ES61", "ES52"))


def test_unknown_returns_none():
    assert region_for(None, "villaejemplo", "nadie", None) is None
    assert region_for("FR10", None, None, None) is None


def test_coverage_score_is_the_mean_and_bounded():
    full = dict(has_budget=1, has_real_award_date=1, has_notice_date=1, has_duration=1, not_feed_1044=1)
    assert coverage_score(full) == 1.0
    assert coverage_score({**full, "has_budget": 0.0, "not_feed_1044": None}) == 0.6
    assert coverage_score({}) == 0.0
