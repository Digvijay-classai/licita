import pytest

from licita import profile
from licita.profile import ProfileError, load_profile, parse


def test_test_profile_is_active_and_parsed():
    p = load_profile()
    assert p.name == "test" and list(p.buyers) == ["HOSP_A", "HOSP_B"]
    assert p.buyer().key == "HOSP_A" and p.buyer("HOSP_B").short == "General"
    assert p.region.nuts_prefix == "ES99" and p.region.health_terms == ("SALUDEJ",)
    assert p.tribunal.short == "TRE"


def test_example_profile_ships_and_is_fictitious():
    p = load_profile("example")
    assert {b.nif[1:3] for b in p.buyers.values()} == {"00"}     # province code 00 does not exist
    assert p.region.nuts_prefix == "ES99" and p.tribunal.base_url == ""


def test_missing_profile_explains_the_fix(monkeypatch):
    monkeypatch.setenv("LICITA_PROFILE", "does-not-exist")
    with pytest.raises(ProfileError) as e:
        load_profile()
    assert "LICITA_PROFILE" in e.value.fix and "example.toml" in e.value.fix


def test_unknown_buyer_lists_known_keys():
    with pytest.raises(ProfileError) as e:
        load_profile().buyer("NOPE")
    assert "HOSP_A" in e.value.cause


@pytest.mark.parametrize("body, message", [
    ("name = 'x'\n", "has no buyers"),
    ("[buyers.A]\nname = 'A'\n", "incomplete"),
    ("[region]\nbuyer_chain_re = '('\n[buyers.A]\nname='A'\nnif='Q0000001A'\n", "invalid regular expression"),
    ("[region]\nhealth_terms = 'x'\n[buyers.A]\nname='A'\nnif='Q0000001A'\n", "list of strings"),
    ("not toml = = =\n", "cannot parse"),
])
def test_invalid_profiles_fail_loudly(tmp_path, body, message):
    path = tmp_path / "bad.toml"
    path.write_text(body)
    with pytest.raises(ProfileError, match=message):
        parse(path)


def test_profile_cli_lists_buyers(capsys):
    assert profile.main(["buyers"]) == 0
    assert capsys.readouterr().out.split() == ["HOSP_A", "HOSP_B"]


def test_profile_is_not_read_at_import(monkeypatch):
    import importlib

    import licita.memo.facts as facts
    monkeypatch.setenv("LICITA_PROFILE", "does-not-exist")
    profile._load.cache_clear()
    importlib.reload(facts)            # must not raise: the profile is read lazily
