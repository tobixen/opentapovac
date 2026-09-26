# Tapo robot vacuum — local setup

State as of 2026-09-25.  Protocol details are in `protocol.md`;
background and the original questions are in `original-questions.md` and
`research.md`.

## Device

* Tapo RV50 Pro Omni(EU), hw 1.0, firmware 1.2.8 Build 260811 Rel.030349.
* LAN: **10.47.128.10** (static lease), wifi
  "iot.example.net" on the OpenWrt router, since
  2026-09-24 (joined with `kasa wifi join … --keytype wpa2_psk`).
  Reachable from the home.example.net LAN only, tcp/4433.
* Talks TPAP on port 4433 (HTTPS).  Doesn't answer ping.
* Account credentials: `~/.config/tapo/credentials.yaml` (`user`, `pass`,
  mode 0600).

## Tools

* **Client:** `<python-kasa checkout>`, branch `tpap-rv50-tls-fix` (unmerged upstream
  TPAP PR + our fix), venv in `.venv`.  Command line:

  ```bash
  cd <python-kasa checkout>
  export KASA_USERNAME=$(yq -r .user ~/.config/tapo/credentials.yaml)
  export KASA_PASSWORD=$(yq -r .pass ~/.config/tapo/credentials.yaml)
  .venv/bin/kasa --host 10.47.128.10 --port 4433 --https -e tpap \
      -df SMART.TAPOROBOVAC --timeout 30 state
  ```

  Don't add `-v` to `state`: it prints the password in reversible form.
* **Map renderer:** `opentapovac render-map DUMP.json OUT.png [--path PATH.json]`
  renders saved dumps; `--path` draws a `getPathData` dump, i.e. the track.
  `opentapovac map OUT.png` fetches both from the robot.
* **Payloads:** `payloads/`.  The decompiled app and the map dumps are
  not in this repository.
* **Phone:** Android, Tapo app 3.20.754.  Paired with the
  laptop for wireless adb (Wireless debugging is normally off).

## Map "andre etg" — map_id 1789832484

The only saved map; auto-change-map is off (since 2026-09-25).  A backup was taken in the app on
2026-09-24 before the room border was moved.

| Room id | Name on robot | Notes |
|---|---|---|
| 1 | kjøkken | dock is here, in a corner |
| 2 | living room | was id 5 until the border move on 2026-09-24 |
| 3 | stairs | covered by no-go zone 301 |
| 4 | bedroom 1 | |
| 5 | hall | was id 2 (named "stua" in the app by mistake, fixed) |
| 6 | — | unnamed, small room right of the hall, beside the stairs |
| 7 | bedroom 2 | |

* **Locked**, and auto-change-map **off**, since 2026-09-25
  (`getMapInfo`: `map_locked` 1, `auto_change_map` false).
* No-go zone **301**: rectangle over the stairs, drawn in the app.
* Virtual wall **401**: between the stairs and room 6.
* Living room/hall border moved 5 cm towards the hall on 2026-09-24
  (merge + split from the laptop).  The living room still carries the
  hall's `label` 10 — cosmetic, fix by setting the room type in the app.
* Map y points up, so the app's view and a naive image are mirror images.
  Leaving the kitchen into the hall, the living room is to the right.

## Known problems (see `original-questions.md`)

1. Takes the wrong way home sometimes.  Plan: waypoint chain with
   `gotoPoint` → dock.
2. Forced dock cycle on every restart.  Plan: compose runs correctly from
   the laptop; test pause + `setCleanAttr` + resume.
3. Loses its position → starts a blank map.  Old map survives; restore
   works from the app (cloud backup).
4. Doorsteps.  Two can only be crossed one way (into the living room,
   out of bedroom 2), and homing fails at doorsteps in general (err 21,
   see the runs below).  Ramps maybe; software fallback: detect stuck,
   pause, ask for help, resume.

## What has been done to the robot from the laptop

Everything was read back and compared afterwards.

* 2026-09-23: read-only state.
* 2026-09-24: renamed rooms (2 hall, 4 bedroom 1, 5 living room, 7 bedroom 2); merged 2+5 and split them again 5 cm further right; re-applied
  names and floor types to the swapped ids.
* 2026-09-24: first run from the laptop — vacuum then mop of the hall
  (room 5) with `runCleanTask`.  Payload and method notes in
  `protocol.md`, section "Cleaning".  It did the whole cycle;
  err 26 (clean water tank in the base empty) came up during the mop pass, but the
  hall did get mopped.
* 2026-09-24: vacuum only, bedroom 1 (room 4).
* 2026-09-24: mop only, bedroom 1, sent with `tapo-run-queue.sh` (kitchen
  vac-then-mop queued after it on the laptop).  The laptop then lost contact
  mid-run; a stop and a 3-room run were attempted, outcome unknown.
* 2026-09-24 evening: kitchen, then room 6 (outer hall), each vacuum then
  mop, as two queued runs.  Stuck once at the kitchen doorstep (err 3),
  lost the dock once coming back from room 6 (err 21); both runs
  finished.  The second send went out too early and was dropped (see
  protocol.md); `tapo-run-queue.sh` still has that bug.
* 2026-09-25 morning: bedroom 2 (room 7), vacuum and mop in one pass
  (`clean_type` 0, `payloads/runCleanTask-bedroom2-vac-and-mop.json`).
  The base had no power: the robot sat in standby (status 0, not 5/6)
  and, sent home, searched the whole house for the dock.  Stopped it,
  put it on the powered dock by hand, re-sent.  Lifted over the high
  doorstep into room 7 (err 4 while lifted; it resumed by itself ~20 s
  after being put down).  ~9 min cleaning.  On the way home err 21 at
  the doorstep, then after ~90 s it went home by itself and made it.
* 2026-09-25 09:00: living room (room 2), vacuum and mop in one pass
  (`payloads/runCleanTask-livingroom-vac-and-mop.json`).  It went in over
  the one-way doorstep by itself.  Got tangled in cords; untangled by
  hand, then relocated.  At 09:16 heading home with err 4 (lifted),
  `getCleanStatus.is_relocating` true for ~20 s, no new map; back on the
  dock 09:17.
* 2026-09-25 09:21: **map locked** from the laptop, while docked:
  `setMapInfo {'map_list': [{'map_id': 1789832484, 'map_locked': True}]}`,
  read back `map_locked: 1`.  `auto_change_map` turned off right after
  (`setMapInfo {'auto_change_map': False}`, read back false).
* 2026-09-25 15:25: bedroom 1 (room 4) then kitchen (room 1), vacuum
  then mop, as one run (`payloads/runCleanTask-bedroom1-kitchen-vac-then-mop.json`).
  One vacuum pass for both rooms (15:25–15:36), then mop pass with three mop
  washes at the base (15:38, 16:01, 16:07); err 26 (base clean water tank
  empty) until the tank was refilled.  Hair cut, dust emptied, drying from
  16:21.  No stuck or lost-dock errors this time.

## Next steps

* The tool is designed in `design.md`; the robot tests to do first are
  in `tapo-clean-spec.md`.
* First movement test, with someone watching: `gotoPoint` to a spot in the
  kitchen, then back to the dock.  Check `real_vac_coor` while it runs.
