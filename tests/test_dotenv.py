import os

from montage import cli


def test_load_dotenv_parses_and_never_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nMONTAGE_T_A=plain\nexport MONTAGE_T_B=\"quoted value\"\n"
                   "MONTAGE_T_C='single'\nMONTAGE_T_SET=from-file\nnot a pair\n", encoding="utf-8")
    for k in ("MONTAGE_T_A", "MONTAGE_T_B", "MONTAGE_T_C"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONTAGE_T_SET", "from-shell")
    loaded = cli.load_dotenv(env)
    assert sorted(loaded) == ["MONTAGE_T_A", "MONTAGE_T_B", "MONTAGE_T_C"]
    assert os.environ["MONTAGE_T_A"] == "plain"
    assert os.environ["MONTAGE_T_B"] == "quoted value"
    assert os.environ["MONTAGE_T_C"] == "single"
    assert os.environ["MONTAGE_T_SET"] == "from-shell"
    for k in loaded:
        monkeypatch.delenv(k)


def test_load_dotenv_missing_file(tmp_path):
    assert cli.load_dotenv(tmp_path / "nope.env") == []


def test_main_with_argv_skips_dotenv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda p: calls.append(p) or [])
    try:
        cli.main(["validate", "does-not-exist.json"])
    except SystemExit:
        pass
    assert calls == []
