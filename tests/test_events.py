import json

from opentapovac.events import EventLog, format_record


def test_emit_writes_jsonl_and_keeps_recent(config):
    log = EventLog(config.events_file, "Europe/Oslo")
    log.emit("info", "hello", job="j1")
    log.emit("warn", "careful")
    lines = config.events_file.read_text().splitlines()
    assert [json.loads(line)["msg"] for line in lines] == ["hello", "careful"]
    assert [r["msg"] for r in log.recent(1)] == ["careful"]


def test_reload_recent_from_file(config):
    EventLog(config.events_file, None).emit("info", "old")
    assert [r["msg"] for r in EventLog(config.events_file, None).recent()] == ["old"]


async def test_subscribe(config):
    log = EventLog(None, None)
    q = log.subscribe()
    log.emit("alert", "stuck")
    assert (await q.get())["msg"] == "stuck"
    log.unsubscribe(q)


def test_format_in_timezone():
    rec = {"t": "2026-09-24T19:09:03+00:00", "level": "alert", "msg": "stuck"}
    assert format_record(rec, "Europe/Oslo") == "2026-09-24 21:09:03 alert  stuck"
