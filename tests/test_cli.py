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


@pytest.mark.parametrize(
    ("line", "choice"), [("done", "done"), ("D\n", "done"), (" can ", "cancel"), ("x", None), ("", None)]
)
def test_parse_choice(line, choice):
    from opentapovac.cli import parse_choice

    assert parse_choice(line, ["done", "cancel"]) == choice


def test_answer_parser():
    assert parse("answer", "done").choice == "done"
    assert parse("answer").choice is None


async def test_terminal_asker(monkeypatch, capsys):
    import asyncio
    import os

    from opentapovac.cli import TerminalAsker

    r, w = os.pipe()
    monkeypatch.setattr("sys.stdin", stdin := os.fdopen(r))
    os.write(w, b"x\ncan\n")
    sent = []

    async def fetch(job_id):
        return {"text": "carry it in", "choices": ["done", "cancel"]}

    async def send(job_id, choice):
        sent.append((job_id, choice))

    asker = TerminalAsker(fetch, send)
    asker.feed({"code": "ask", "job": "j1"})
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.01)
    assert sent == [("j1", "cancel")]
    assert capsys.readouterr().out.count("carry it in [done/cancel]") == 2
    os.close(w)
    stdin.close()


async def test_terminal_asker_cancelled_by_answer_elsewhere(monkeypatch):
    import asyncio
    import os

    from opentapovac.cli import TerminalAsker

    r, w = os.pipe()
    monkeypatch.setattr("sys.stdin", stdin := os.fdopen(r))

    async def fetch(job_id):
        return {"text": "q", "choices": ["done"]}

    async def send(job_id, choice):
        raise AssertionError("must not send")

    asker = TerminalAsker(fetch, send)
    asker.feed({"code": "ask", "job": "j1"})
    await asyncio.sleep(0.01)
    task = asker._task
    asker.feed({"code": "answered", "job": "j1"})
    await asyncio.sleep(0.01)
    assert task.cancelled()
    os.close(w)
    stdin.close()


def test_goto_target():
    from opentapovac.cli import goto_target

    assert goto_target(["4100", "-300"]) == ((4100, -300), None)
    assert goto_target(["outer", "hall"]) == (None, "outer hall")
    assert goto_target(["kitchen"]) == (None, "kitchen")


def test_goto_target_odd_numbers():
    from opentapovac.cli import goto_target

    assert goto_target(["--5", "3"]) == (None, "--5 3")
