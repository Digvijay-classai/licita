import subprocess

import pytest

from licita import anon

KEY = b"test-key-not-a-secret"
HASHES = frozenset({anon.digest("santa ficticia", KEY), anon.digest("villaejemplo", KEY)})


def test_normalise_folds_accents_case_and_punctuation():
    assert anon.normalise("Sánta-FICTÍCIA, (Villaejemplo)") == "santa ficticia villaejemplo"
    assert anon.digest("Santa  Ficticia", KEY) == anon.digest("santa-ficticia", KEY)


def test_digest_is_keyed():
    assert anon.digest("villaejemplo", KEY) != anon.digest("villaejemplo", b"another key")


def test_planted_phrase_is_found_in_any_spelling():
    hits = anon.scan_text("ok line\nHospital SANTA FICTÍCIA de Villa\nvillaejemplo.", HASHES, KEY, "x.md")
    assert [(h.line, h.detail) for h in hits] == [(2, "denylisted phrase 'santa ficticia'"),
                                                  (3, "denylisted phrase 'villaejemplo'")]


def test_partial_words_do_not_match():
    assert anon.scan_text("santas ficticias villaejemplos", HASHES, KEY) == []


def _repo(tmp_path, files: dict[str, bytes]):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, data in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_check_scans_tracked_and_new_files_and_refuses_binaries(tmp_path):
    root = _repo(tmp_path, {"README.md": b"fine", "src/a.py": b"# Villaejemplo", "data/x.parquet": b"PAR1",
                            "demo/spine.duckdb": b"\0ok", "img.png": b"\x89PNG\0villaejemplo"})
    (root / "untracked.txt").write_text("santa ficticia")
    found = {(f.path, f.detail.split(" (")[0]) for f in anon.check(root, HASHES, KEY)}
    assert found == {("src/a.py", "denylisted phrase 'villaejemplo'"),
                     ("untracked.txt", "denylisted phrase 'santa ficticia'"),
                     ("data/x.parquet", "binary data file")}


def test_check_refuses_to_run_without_a_key(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"README.md": b"fine"})
    monkeypatch.delenv(anon.KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="no anonymity key"):
        anon.check(root, HASHES)


def test_allowlisted_file_skips_the_phrase_scan_but_not_the_binary_check(tmp_path):
    root = _repo(tmp_path, {"facts/cities.csv": b"city,code\nvillaejemplo,ES99\n", "src/b.py": b"villaejemplo",
                            "facts/blob.parquet": b"PAR1",
                            "anon/allowlist.txt": b"# reason\nfacts/cities.csv  # national fact table\nfacts/blob.parquet  # never\n"})
    found = {(f.path, f.detail.split(" (")[0]) for f in anon.check(root, HASHES, KEY)}
    assert found == {("src/b.py", "denylisted phrase 'villaejemplo'"), ("facts/blob.parquet", "binary data file")}
    assert anon.load_allowlist(root / "anon" / "allowlist.txt") == {"facts/cities.csv", "facts/blob.parquet"}


def test_hash_command_refuses_phrases_that_can_never_match(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(anon.KEY_ENV, KEY.decode())
    src = tmp_path / "deny.txt"
    src.write_text("# comment\nvillaejemplo\nuno dos tres cuatro\n")
    assert anon.main(["hash", str(src)]) == 2
    src.write_text("villaejemplo\nSanta Ficticia\n")
    assert anon.main(["hash", str(src)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert set(out[1:]) == HASHES


def test_committed_tree_is_clean():
    assert anon.load_hashes(), "anon/denylist.sha256 is missing"
    if anon.load_key() is None:
        pytest.skip("no anonymity key on this machine (private/anon.key or LICITA_ANON_KEY); CI provides one")
    assert anon.check() == []
