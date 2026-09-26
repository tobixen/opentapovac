from pathlib import Path

from opentapovac.config import Config, load_config, load_credentials


def test_defaults():
    c = Config.from_dict({})
    assert c.port == 4433
    assert c.defaults.mode == "vac_then_mop"
    assert c.daemon_url == "http://127.0.0.1:8765"


def test_load(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "robot: {host: 10.0.0.2}\n"
        "defaults: {mode: vac, suction: 3}\n"
        "rooms:\n  6: {aliases: [outer hall]}\n"
        "listen: 0.0.0.0:9000\n"
    )
    c = load_config(f)
    assert c.host == "10.0.0.2"
    assert c.defaults.mode == "vac"
    assert c.defaults.suction == 3
    assert c.rooms[6]["aliases"] == ["outer hall"]
    assert c.daemon_url == "http://127.0.0.1:9000"


def test_missing_file(tmp_path):
    assert load_config(tmp_path / "none.yaml").host is None


def test_credentials_file(tmp_path, monkeypatch):
    monkeypatch.delenv("KASA_USERNAME", raising=False)
    monkeypatch.delenv("KASA_PASSWORD", raising=False)
    f = tmp_path / "credentials.yaml"
    f.write_text("user: a@b\npass: secret\n")
    assert load_credentials(Config.from_dict({"robot": {"credentials": str(f)}})) == ("a@b", "secret")


def test_credentials_env(monkeypatch):
    monkeypatch.setenv("KASA_USERNAME", "u")
    monkeypatch.setenv("KASA_PASSWORD", "p")
    assert load_credentials(Config.from_dict({"robot": {"credentials": "/nonexistent"}})) == ("u", "p")


def test_paths_expanded():
    c = Config.from_dict({"cache_dir": "~/x"})
    assert c.cache_dir == Path.home() / "x"
