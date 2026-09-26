import pytest

from opentapovac import __version__
from opentapovac.cli import build_parser, main, request_from_args


def parse(*argv):
    return build_parser().parse_args(list(argv))


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flag", "mode"),
    [
        ("--vac", "vac"),
        ("--mop", "mop"),
        ("--vac-and-mop", "vac_and_mop"),
        ("--vac-then-mop", "vac_then_mop"),
        (None, None),
    ],
)
def test_mode_flags(flag, mode):
    args = parse("clean", *([flag] if flag else []), "kitchen")
    assert request_from_args(args).mode == mode


def test_clean_request():
    req = request_from_args(parse("clean", "--suction", "3", "--sequential", "--force", "kitchen", "outer hall"))
    assert req.rooms == ["kitchen", "outer hall"]
    assert req.suction == 3
    assert req.sequential
    assert req.force


def test_modes_exclusive():
    with pytest.raises(SystemExit):
        parse("clean", "--vac", "--mop", "kitchen")


def test_render_map(tmp_path, capsys):
    import json

    from tests.conftest import make_map

    dump = tmp_path / "map.json"
    dump.write_text("WARNING: x\n" + json.dumps({"getMapData": make_map()}))
    out = tmp_path / "map.png"
    assert main(["render-map", str(dump), str(out)]) == 0
    assert out.read_bytes().startswith(b"\x89PNG")


def test_log_standalone(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\ncache_dir: {tmp_path}\ntimezone: UTC\ndaemon_url: http://127.0.0.1:9\n")
    (tmp_path / "events.jsonl").write_text('{"t": "2026-09-24T19:09:03+00:00", "level": "alert", "msg": "stuck"}\n')
    assert main(["--config", str(cfg), "log"]) == 0
    assert "19:09:03 alert  stuck" in capsys.readouterr().out


def test_room_completer(tmp_path, monkeypatch):
    from opentapovac.cli import room_completer

    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"cache_dir: {tmp_path}\n")
    (tmp_path / "rooms.json").write_text('[{"id": 1, "name": "kjøkken"}, {"id": 6, "name": null}]')
    monkeypatch.setenv("OPENTAPOVAC_CONFIG", str(cfg))
    assert sorted(room_completer("", parse("clean"))) == ["6", "kjøkken"]


def test_completion_hook():
    from hatch_build import completion_snippet

    assert "opentapovac" in completion_snippet()
