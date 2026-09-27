# Tapo robot vacuum — local protocol notes

Protocol-level findings for the Tapo RV50 Pro Omni(EU), fw 1.2.8 Build
260811, and Tapo Android app 3.20.754.  Obtained 2026-09-23/24 by talking
to a real robot over the LAN and by decompiling the app.  Nothing here
comes from TP-Link documentation.  Status markers: **verified** means
sent to the real robot and the effect read back; **from app** means read
out of the decompiled code and not yet sent.

The site-specific facts (IP, map id, room ids) live in
`field-notes.md`.

## Transport

* **Discovery:** UDP broadcast on port 20002 (`kasa discover raw`).  The
  robot does not answer ping, so an nmap sweep misses it.  The reply carries
  `device_type: SMART.TAPOROBOVAC`, `tpap_preferred: true`, `tpap: {tls: 2,
  dac: 1, noc: 1, pake: [2], port: 4433}`.
* **TPAP over HTTPS on port 4433.**  TLS 1.2, `ECDHE-ECDSA-*` ciphers, no
  SNI.  The certificate chain device → `SMART.TAPOROBOVAC CA` → `TP-LINK
  SYSTEMS DEVICE ROOT CA` validates against the root CA embedded in
  python-kasa's `tpaptransport.py`.
* **Auth:** SPAKE2+ keyed by the Tapo account password (PBKDF2).  The
  password never goes on the wire, and the wire username is a hash of the
  literal string `"admin"`.  `dac: 1` means the robot also signs the client's
  nonce with its device-attestation key, which blocks a naive MITM
  impersonating the robot.
* **Session:** after the handshake, requests are AEAD-encrypted POSTs to
  `/stok=<session>/ds`.  An open review comment on the PR reports reused
  (key, nonce) pairs between request and response; not checked here.
* **Quirk:** the in-band `login`/`discover` over HTTPS answers **`tls: 0`**,
  although UDP discovery says `tls: 2`.  Taking the in-band value at face
  value downgrades to `http://…:4433` and the robot drops the connection.

### Client

Released python-kasa (0.10.2) has no TPAP.  Use the PR branch
<https://github.com/python-kasa/python-kasa/pull/1592>
(`ZeliardM/python-kasa@feature/tpap`) plus the one-line fix for the quirk
above: `<python-kasa checkout>`, branch `tpap-rv50-tls-fix`, with a regression
test.  Bug reported on the PR:
<https://github.com/python-kasa/python-kasa/pull/1592#issuecomment-5803644379>

```bash
cd <python-kasa checkout>
export KASA_USERNAME=$(yq -r .user ~/.config/tapo/credentials.yaml)
export KASA_PASSWORD=$(yq -r .pass ~/.config/tapo/credentials.yaml)
K=".venv/bin/kasa --json --host <ip> --port 4433 --https -e tpap \
   -df SMART.TAPOROBOVAC --timeout 30"
$K state                                  # read-only overview
$K command getMapInfo                     # raw method, no params
$K command getMapData '{"map_id": <id>, "type": 0}'
```

`--json` output is preceded by two WARNING lines (see "Failing reads"),
so strip up to the first `{` before parsing.  **Never use `state -v`**:
it prints `credentials_hash`, which is plain base64 of `{"un","pwd"}`,
i.e. the password.

## Method list

All robot methods are the enum
`3.20.754/jadx/sources/com/tplink/libtaporobotnetwork/datamodel/Method.java`
(~160 entries), extracted to `robot-methods.txt`.  Parameter names come from
the `@SerializedName` fields of the matching `*Params` class in the same
package; the call sites are in `com/tplink/robot/api/repository/Robot*Repository.java`.

`component_nego` on this robot lists, among others: `clean` v4, `map` v2,
`auto_change_map` v2, `map_lock` v2, `map_cloud_backup`, `goto_point`,
`direction_control`, `furniture`, `carpet_area`, `clean_angle`,
`continue_breakpoint_sweep`, `charge_pose_clean`, `wash_mop`, `dry_mop`,
`dust_bucket`, `do_not_disturb`.

### Failing reads

`getAutoDustCollection` and `get_matter_setup_info` return
`UNKNOWN_METHOD_ERROR` on this firmware, so python-kasa's Dustbin and
Matter modules are unavailable (harmless warnings).

## Map

**verified** — `getMapInfo` (no params): `current_map_id`, `map_list[]`
with `map_id`, base64 `map_name`, `map_locked`, `is_saved`.

**verified** — `getMapData {"map_id": <id>, "type": 0}`:

| Field | Meaning |
|---|---|
| `width`, `height`, `resolution` (50), `resolution_unit` (`mm`) | grid size; 5 cm/pixel |
| `map_data` | base64 of an **LZ4 block** (`lz4.block.decompress(…, uncompressed_size=pix_len)`), `pix_len` = width × height bytes, row-major |
| `bit_list` | pixel values: `barrier` 0 (wall), `none` 127 (unknown), `clean` 255 (floor not in a room), `auto_area` 1…100 = room id |
| `real_origin_coor` | mm coordinates of pixel (0, 0) |
| `real_charge_coor` | dock `[x, y, heading]` in mm |
| `real_vac_coor` | robot `[x, y, heading]`; reads ≈ (0, 0) while docked, so probably only live during a run (unconfirmed) |
| `goto_point` | last go-to target `[x, y]` |
| `map_hash` | changes whenever the pixel grid changes, not on renames |
| `area_list[]` | rooms and zones, see below |
| `furniture_list[]` | furniture polygons |

**The map's y axis points up**: pixel row r is at `y = origin_y + r ×
resolution`.  Drawn with image rows top-down, the map comes out mirrored.
`opentapovac render-map DUMP.json OUT.png [id=name …]` renders it
correctly, with rooms, no-go zones, virtual walls, dock and robot marked.

### `area_list` entries

* Room: `{"id", "type": "room", "name": <base64>, "floor_material", "color",
  "label"}`.  `name` and `label` may be absent.  `label` looks like a
  room-type/icon code (kitchen 4, hall and stairs 10).
* No-go zone: `{"type": "forbid", "id": 301…, "vertexs": [[x0,y1],[x1,y1],
  [x1,y0],[x0,y0]]}`, mm, clockwise from top-left.  **ids start at 301.**
* Virtual wall: `{"type": "virtual_wall", "id": 401…, "vertexs": [[x,y],[x,y]]}`.
  **ids start at 401.**
* Other types in the app: `forbid_mop` (mop-free zone), `area` (clean-zone),
  `carpet_rectangle`, `carpet_round`, `carpet_auto`, `hidden_rectangle`.

## Editing the map

### `setAreaInfo` — **verified** for room renames

```json
{"map_id": <id>, "type_list": ["room"], "area_list": [<every room>]}
```

It **replaces all areas of the types in `type_list`**, so always send the
full list, taken fresh from `getMapData`, with only the intended fields
changed.  Returns `null` on success.  Renames leave `map_hash` unchanged.

Caveat: **a field left out of an entry is not cleared.**  Omitting `label`
kept the old value; what value means "no label" is unknown.

No-go zones and virtual walls are edited the same way with `type_list:
["virtual_wall", "forbid", "forbid_mop"]`, so all three kinds must be sent
together (*from app*; the zone format itself is **verified** by reading back
a zone drawn in the app).

### `setAutoAreaData` — merge and split rooms, **verified**

```json
{"map_id": <id>, "operation": "merge",
 "extra": {"pixel1": <room a>, "pixel2": <room b>}, "auto_area_id": 1}

{"map_id": <id>, "operation": "split",
 "extra": {"pixel": <room>, "p1": [x, y], "p2": [x, y]}, "auto_area_id": 1}
```

`p1`/`p2` are map mm and give a straight dividing line; let it overshoot
the walls on both sides of the opening.  Returns `{}`.  `auto_area_id: 1` is
the app's default.

Observed behaviour, moving the border between rooms 5 and 2 by one pixel:

* merge 2+5 → one room with id 5, keeping room 5's name.  Pixel count =
  sum of both.
* split of 5 at the pixel edge x = 3601 mm → the border moved exactly one
  column, **but the ids came out swapped**: the larger side became 2 and the
  smaller side 5.  Names and `floor_material` did not follow the geometry.
* **So after a split, always read the map back, work out which id is where
  from the pixels, and re-apply names and settings with `setAreaInfo`.**
  Room ids feed schedules and `room_list`, so check those too.

Lay the split line on a pixel edge (`origin + k × 50`); a line through the
middle of a pixel column is ambiguous.

### Map backup/restore — *from app*

`mapBackup`, `mapRecovery` (`RESTORE_MAP`), `getMapBRProcess`,
`getOldMapData`.  `mapRecovery` takes `{"map_id", "map_md5", "url"}`, so a
restore pulls a **cloud** backup: there is no known way to upload a local
`getMapData` dump.  Local dumps are only good for comparing.

## Cleaning

**The RV50/RV50 Pro start runs with `runCleanTask`, not `setSwitchClean`**
(`RobotUtils.f0()` picks the method by model).  Sent and accepted
2026-09-24 (`payloads/runCleanTask-hall-vac-then-mop.json`), vacuum then
mop of one room:

```json
{"clean_on": true, "support_continue": true, "start_type": 1, "clean_mode": 3,
 "force_clean": false, "is_custom": true, "clean_order": true, "dust_collection": true,
 "area_list": [{"id": 5, "type": "room", "clean_number": 1, "suction": 2,
                "cistern": 2, "clean_type": 3, "density": 1}]}
```

Returns `null`; `getVacStatus` goes 16 (drying) → 1 (cleaning).  The app
stops a run with `runCleanTask` and the `RobotRunCleanTaskParams`
defaults, nulls left out by Gson (*from app*; OpenTapoVac sends exactly this):

```json
{"clean_on": false, "support_continue": true, "start_type": 1, "clean_mode": 0,
 "force_clean": false, "is_custom": false, "clean_order": true, "dust_collection": true}
```

* **`kasa command` parses params with `ast.literal_eval`**, so JSON
  `true`/`false`/`null` fail *silently* (no output, nothing sent).  Convert
  first: `python3 -c "import json;print(repr(json.load(open(F))))"`.
* `clean_type` (`RobotParamValue.CleanType`): 0 vacuum and mop, 1 mop only,
  2 vacuum only, 3 vacuum then mop, 4 AI.
* `getVacStatus.status` (`RobotStatusType`): 0 standby, 1 cleaning,
  2 mapping, 3 remote control, 4 going home, 5 charging, 6 charged, 7 paused,
  8 sleep, 9 emptying dust, 11 goto point, 15 washing mop, 16 drying mop,
  17 fitting mop, 18 removing mop, 19 cutting hair.
* `err_status` codes are `RobotCleanErrorType`; 26 = clean water tank empty (the tank is in the base, not the robot)
  or missing.  The hall run above went 1 → 4 → 17 (fit mop) → 15 (wash
  mop) → 1 with err 26 → 4 → 19 → 16.  It kept going with the error and
  the hall did get mopped, so the tank probably ran dry partway.  `getBaseStatus.clean_water`: 1 = empty (it was 1 after
  the run and 0 after the tank was refilled).
* Room run with `clean_type 2` (vacuum only) sent while drying: drying
  stops and the robot goes straight to 1.  Back home: 4 → 19 → 17 → 5.
* `getCleanAttr {"type": "global"}` → `clean_type 0, suction 2, cistern 2,
  clean_number 1, density 1` (the app's global defaults here).

* More `err_status` codes seen 2026-09-24: 3 = `ROBOT_ERR_MIDDLE_STUCK`
  (status 7, at the kitchen doorstep; went on after a kick), 21 =
  `ROBOT_ERR_CHARGE_STATION_IS_NOT_FOUND` (status 0 on the way home from
  room 6; went home again a minute later).
* After a run the robot passes through status 5 only briefly (4 → 5 →
  19 → 9 → 17 → 15 → 16 took ~5 min).  A `runCleanTask` sent at that 5
  got an empty reply and was dropped; sent at 16 it was accepted.  Wait
  for 16, or 5/6 seen twice, before sending the next run.
* **A new `runCleanTask` while a run is going fails with error -3002**
  (kasa prints only a warning).  Stop the run first, see above.  Several
  rooms in one `area_list`, each with its own `clean_type`, is the way to
  queue work on the robot itself.  Tried once with two rooms and the
  same `clean_type` 3 (`payloads/runCleanTask-bedroom1-kitchen-vac-then-mop.json`,
  2026-09-25): accepted, both rooms vacuumed, then both mopped.  List
  order and mixed `clean_type`s not checked yet.

From the app, not yet sent:

* `setSwitchClean`: `clean_mode` (0 whole house as python-kasa sends it,
  3 = rooms, 6 = task), `clean_on`, `clean_order`, `force_clean`,
  `start_type`, `map_id`, `room_list: [ids]`, `area_list: [items]`,
  `custom_rule_id`.
* Per-room items in `area_list`: `id`, `type`, `suction`, `cistern` (water
  level), `clean_number` (passes), `clean_type` (see above), `floor_texture` (clean angle),
  `carpet_strategy`, `density`, `is_custom`, `vertexs`.
* `runCleanTask`: as `setSwitchClean` plus `dust_collection`,
  `support_continue`, `is_custom`.
* Quick tasks: `getCleanTaskGroupList`, `getSpecificCleanTaskGroup`,
  `addCleanTaskGroup`, `startCleanTaskGroup {"group_id": n}`.
* `setSwitchCharge {"switch_charge": true}` sends the robot home
  (`RobotDetailRepository.ea`, `RobotGotoChargeStatus`); `setRobotPause`
  takes a `RobotPause`.
* Also present: `setCleanAttr`, `get/setCleanOrder`, `setRobotPause`,
  `setSwitchCharge`, `setGotoDustCollection`, `setSwitchDustCollection`,
  `setWashMopSwitch`, `setDryMopSwitch`, `getBaseStatus`.

### Pause, resume, lifted — sent 2026-09-26

* `setRobotPause {"pause": false}` (resume) and `{"pause": true}` (pause)
  are accepted (`null`), from the app's home-card start/pause button
  (`RobotPause`, field `pause`, no `@SerializedName`).  Sent while the
  robot sat in **standby (0) with err 4 (lifted) still set**, after being
  lifted out of a run on its way home: no effect, neither alone nor as
  true, 5 s, false.  Status stayed 0, err [4], `clean_status` 3, for a
  minute each.  Not yet tried on a paused run (status 7), which is where
  the app shows the button.
* Being lifted during a run gives status 7 (paused) + err 4.  On
  2026-09-25 it resumed by itself ~20 s after being put down; on
  2026-09-26 (carried into bedroom 2) it did not, and needed the button
  on the robot.  Lifted again later, it dropped from 7 to standby 0 with
  err 4 latched; the button then cleared the error and the robot
  **continued the old run** (it had been heading home: status 4).
* From standby off the dock, a vacuum-and-mop room run started with
  going home → fitting mop → washing mop → cleaning (4 → 17 → 15 → 1):
  the robot goes to the base to fit the mop first.

### Track and clean records — read 2026-09-26

* `getPathData {"start_pos": n}` returns only the entries from n on:
  `path_id`, `start_pos`, `point_counts` (in this reply), `total_points`,
  `pos_len`, `pos_array` (LZ4 block of big-endian int16 x, y pairs).
  Entry 0 of the list is a header, `(1, 0)` (seen before as `(377, -8),
  (1, 0)`); a reply from n > 0 has none.  The track is cleared now and
  then: after a 25-minute run with a carry to the dock, 14 points were
  left, all on the last stretch; `path_id` 188 before and after.
* `getCleanRecords` → `total_time`, `total_area`, `total_number`,
  `lastest_day_record` `[timestamp, minutes, m², runs]` (today),
  `record_list[]`: `timestamp` (start, Unix), `clean_time` (min),
  `clean_area` (m²), `error`, `clean_type`, `wash_times`, `task_type`,
  `start_type`, `is_custom`, `dust_collection`, `notify_event`,
  `highlight_list`, `record_index`.  The 2026-09-26 20:35 run (sent as
  `clean_type` 3) is recorded as `clean_type` 2.  The area seems to count
  the vacuum and mop passes separately.  `notify_event` meaning unknown
  (20: the run abandoned after err 21; 24, 28 otherwise).

### Progress, mop, go-to — 2026-09-26

* `getCleanInfo` → `clean_time` (min), `clean_area` (m²), `clean_percent`
  of the current run (read mid-run: 20, 9, 45; stood still at 45 while the
  robot fought the kitchen doorstep).
* `getMopState` → `mop_state`: true while the mop is on (mid-run, mopping).
* `getAreaInfo`, `getCleanRecordExtraInfo` without params: -1008
  (PARAMS_ERROR).
* `gotoPoint {"switch": true, "point": [x, y]}` (`GotoPointParams`), in
  the mm frame of `getMapData`'s `goto_point` / `real_vac_coor`; status
  11 while going.  Works, also from standby with err 21 (dock not found),
  from the app and from opentapovac, 2026-09-28.
* `setSwitchCharge {"switch_charge": true}`: the robot heads home;
  works, 2026-09-28.

### Map lock and relocation — verified 2026-09-25

* Lock: `setMapInfo {"map_list": [{"map_id": <id>, "map_locked": true}]}`
  (app `RobotMapRepository.H7`, `SetRobotMapInfoParams` /
  `SetSingleRobotMapInfoParams`; other fields `map_name`, `rotate_angle`,
  `is_base_exist`, and top-level `auto_change_map`, `is_multi_floor`,
  `current_map_id`).  Returns `null`; `getMapInfo` then shows
  `map_locked: 1`.  Sent while docked (drying).
* `getCleanStatus` → `clean_status`, `recharge_status` (1 = heading home),
  `is_working`, `is_mapping`, `is_relocating`, `is_back_wash`,
  `is_back_load_mop`, `is_back_unload_mop` (app `RobotCleanMode`).
  Seen: `is_relocating` true for ~20 s after the robot was lifted
  (`getVacStatus` err 4) and put down.
* `setMapInfo {"auto_change_map": false}` alone (no `map_list`) works;
  read back false.  Sent 2026-09-25.
* Stopping a run (`runCleanTask` with `clean_on: false`) leaves status 0,
  and ~20 s later it was heading home (status 4); by itself or a button
  press, unclear.
* With the base unpowered the robot sits at status 0 (not 5/6) and,
  sent home, searches the whole house for the dock.
* Two clients querying at the same moment: one query can fail (seen
  once with two monitor loops); serialise queries.

### Track — `getPathData {"start_pos": 0}`, read-only, verified

Returns `path_id`, `point_counts`, `pos_len`, and `pos_array` (base64, lz4
block).  Decoded (app `oe1/a.java`, `b()` and `h()`): big-endian int16 x, y
pairs in map mm.  The low 2 bits of x and y give the point type
`((x%4)<<2)+(y%4)`: 0/5 cleaning, 1 heading home, 2 remote control,
3/4 moving between areas, 6 error.  The first 8 bytes, (377,-8) and (1,0),
are not track points.  The path covers the current or last run only (it
grows until the robot is back on the dock, and the next run starts it
over).  Short magenta bits (unknown type) show up in some corners.
`opentapovac render-map … --path PATH.json` draws it.

## Movement — *from app*; the robot moves

* `gotoPoint {"switch": true, "point": [x, y]}` — mm, map frame; works
  (above).  `switch: false` presumably cancels (not sent).
* `directionControl {"direction": d, "control": bool}`: enter manual mode
  with `d=0, control=true`, leave with `d=4, control=false`; in manual mode
  send `{"direction": d}` per button, 4 = stop (button released).  The app
  maps its four button areas 0/1/2/3 to directions 3/1/2/0; which arrow is
  which is **not verified**.

## Getting the app code again

```bash
# phone: Developer options → Wireless debugging → Pair with code
adb pair <ip>:<pair-port> <code>; adb connect <ip>:<port>
adb shell pm path com.tplink.iot          # base.apk + splits
adb pull <path>/base.apk
nix shell nixpkgs#jadx --command jadx --no-res --show-bad-code -j 8 -d jadx base.apk
```

The phone uses a randomized MAC, so its IP can change after it sleeps; find
the adb port with `nmap -p 30000-50000 --open <ip>`.  Turn Wireless
debugging off afterwards.

## Files here

* `robot-methods.txt` — all method names from the app enum.
* `payloads/` — the exact write payloads that were sent.
* Not published: the decompiled app, `getMapData` dumps (a floor plan)
  and raw status logs.
