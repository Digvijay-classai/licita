"""Buyer identity resolution: the rules in licita.spine.buyers, each on a hand-made case."""
from licita.spine.buyers import KeyRow, resolve

NIF = "Q0000001A"


def _row(key, nif=None, dir3=None, name="hospital santa ficticia", city="villaejemplo", parent="comunidad de ejemplo"):
    return KeyRow(key, nif, dir3, name, city, parent)


def test_dir3_only_key_joins_its_single_nif():
    rows = [_row(NIF, nif=NIF, dir3="A0001"), _row("DIR3:A0001", dir3="A0001")]
    mapping, stats = resolve(rows, [(NIF, "A0001")])
    assert mapping["DIR3:A0001"] == NIF and stats["dir3_merged"] == 1


def test_dir3_shared_by_two_nifs_stays_apart():
    rows = [_row(NIF, nif=NIF, dir3="A0001"), _row("Q0000002B", nif="Q0000002B", dir3="A0001"), _row("DIR3:A0001", dir3="A0001")]
    mapping, stats = resolve(rows, [(NIF, "A0001"), ("Q0000002B", "A0001")])
    assert mapping["DIR3:A0001"] == "DIR3:A0001" and stats["dir3_ambiguous"] == 1


def test_two_nifs_are_never_merged():
    rows = [_row(NIF, nif=NIF), _row("Q0000002B", nif="Q0000002B")]        # identical name, city and parent
    mapping, _ = resolve(rows, [])
    assert mapping[NIF] == NIF and mapping["Q0000002B"] == "Q0000002B"


def test_name_key_joins_identified_key_on_name_city_parent():
    rows = [_row(NIF, nif=NIF), _row("NAME:hospital santa ficticia | comunidad de ejemplo >")]
    mapping, stats = resolve(rows, [])
    assert mapping["NAME:hospital santa ficticia | comunidad de ejemplo >"] == NIF and stats["name_merged"] == 1


def test_long_name_joins_on_name_and_city_when_parent_wording_differs():
    rows = [_row(NIF, nif=NIF, parent="departamento de salud"),
            _row("NAME:x", parent="departamento de salud de la comunidad")]
    mapping, _ = resolve(rows, [])
    assert mapping["NAME:x"] == NIF


def test_short_generic_name_does_not_join_on_name_and_city():
    rows = [_row(NIF, nif=NIF, name="director gerente", parent="hospital a"),
            _row("NAME:dg-b", name="director gerente", parent="hospital b")]
    mapping, _ = resolve(rows, [])
    assert mapping["NAME:dg-b"] == "NAME:dg-b"


def test_name_key_with_two_candidates_is_left_alone():
    rows = [_row(NIF, nif=NIF), _row("Q0000002B", nif="Q0000002B"), _row("NAME:amb")]
    mapping, stats = resolve(rows, [])
    assert mapping["NAME:amb"] == "NAME:amb" and stats["name_ambiguous"] == 1


def test_name_key_follows_a_dir3_that_was_itself_merged():
    rows = [_row(NIF, nif=NIF, dir3="A0001", city="a"), _row("DIR3:A0001", dir3="A0001", city="b"), _row("NAME:n", city="b")]
    mapping, _ = resolve(rows, [(NIF, "A0001")])
    assert mapping["DIR3:A0001"] == NIF and mapping["NAME:n"] == NIF
