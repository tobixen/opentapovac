# Tapo RV50 Pro Omni — research notes

Answers to the four questions in `original-questions.md`.  Rev. 2 — revised after your
inline comments, which corrected several things.  Researched 2026-09-22.

## TL;DR

* **Valetudo: no.**  TP-Link is not a supported vendor at all, and never has been.
* **Matter: no.**  It is a remote control, not a navigation stack.  Touches none
  of your three problems.
* **Yes — and it is better than you hoped.**  It is not the phone→cloud API.  The
  robot speaks a **local LAN protocol** (the app uses it at home, and the cloud
  when away), and *that* protocol is the one that has been reverse-engineered.
  It works on your exact model.  Whether the robot works fully offline is
  unverified.
* **#1 is workaroundable** — your own point-and-go observation is the key, and it
  is automatable.  **#2 is largely fixable.**  **#3 is not**, beyond
  restore-and-retry.
* **The doorstep ramp is still the best fix you have.**  Do that first.

## Read this before you start

Honest arguments against the whole project, revised:

1. **The core behaviours are firmware.**  Route-home pathfinding, the dock's
   maintenance cycle and the relocalize-or-start-blank policy live inside the
   robot.  No API replaces them.  But — as you point out — the app already
   exposes primitives (point-and-go, manual drive) that let you *route around*
   the bad pathfinding rather than fix it.  That changes #1 from "hopeless" to
   "a waypoint script".
2. **The protocol is reverse-engineered.**  TPAP/SPAKE2+ was extracted from the
   app.  A firmware update may break it, silently, at an arbitrary time, and the
   fix depends on a handful of hobbyists.  Maintenance liability.
3. **The commands you most want are the ones nobody has captured yet.**  See
   §4.1 — point-and-go, manual drive and map-restore are all things the app can
   do and no public integration implements.  Budget for doing protocol
   archaeology yourself, or for waiting.
4. **Cost/benefit.**  A ramp is an afternoon and permanent.  Home Assistant plus
   custom automations is weeks of fiddling for partial mitigations.

---

## 1) Are the problems known?

Partly.  There is no public bug tracker, so the evidence is vendor forum threads.

**#3 (relocalization failure → blank map) is a known, recurring complaint**,
mostly reported on the RV30 family:

* "RV30 constantly gets error *Failed to locate* and returns to charge base.
  Have to remap to clear it" —
  <https://community.tp-link.com/en/smart-home/forum/topic/843270>
* Map shifted/rotated after every full clean —
  <https://community.tp-link.com/en/smart-home/forum/topic/715370>
* TP-Link's own FAQ on distorted/overlapping maps names your exact triggers:
  furniture moved mid-clean, robot picked up and carried —
  <https://www.tp-link.com/us/support/faq/4387/>

**On the vendor-sanctioned workarounds — you tested them, I was wrong:**

* **Lock Map does not help.**  Noted.  That fits the failure mode you describe:
  the lock protects the *stored* map from being overwritten, but the robot's
  decision is "I cannot localize, so I will build a new map from scratch", and a
  lock has no say in that.  It is the wrong lever entirely.
  <https://www.tapo.com/us/faq/352/>
* **Map Backup & Restore is redundant** — the old map survives anyway, so
  restoring it is a one-tap recovery you already have.  Keep it as the recovery
  step, not as a precaution.
* **Dock placement:** with the dock in a corner of a cluttered house and no
  sunlight problem, the only clearance figure that still matters is TP-Link's
  ≥1.5 m free *in front* — that is the final approach corridor for redocking, and
  it is the plausible link to #1.  The ≥0.5 m sides are about the dock's own
  mechanics, not localization.  Ignore the rest.
  <https://www.tp-link.com/us/support/faq/4484/>

**#1 (wrong room on the way home) and #2 (forced maintenance cycle):** no public
reports specific enough to be useful.  #2 looks like deliberate design — the
dock runs its cycle on arrival regardless of whether anything was cleaned.

**Doorstep:** spec'd climb is ~20 mm.  If your threshold is near that, one-way
traversal is the expected failure mode — it manages it from the side where the
approach is effectively shallower and stalls from the other.  Confirms the ramp.
<https://www.tp-link.com/us/smart-home/robot-vacuum/tapo-rv50-pro-omni/>

## 2) Valetudo

**Not possible.**  Valetudo's supported list covers Xiaomi, Dreame, MOVA,
Roborock, Viomi, Eureka, Cecotec, Proscenic, Commodore and IKOHS.  TP-Link /
Tapo appears nowhere, in any model.

Valetudo requires root; root requires a vendor- and hardware-specific exploit;
the project states plainly that finding one is time-consuming and largely
chance.  Nobody has published one for Tapo.  The RV50 Pro Omni being a recent
model makes it a *worse* candidate, not a better one — published exploits get
burned and patched.

Request form at <https://requests.valetudo.cloud>; registering interest is all
you can do.  <https://valetudo.cloud/pages/general/supported-robots/>

## 3) Matter

The RV50 Pro Omni **does** support Matter (Settings → Device Information →
Matter Setup Code).  Supported models: RV20 Max (Plus), RV30 Max (Plus), RV50
Pro Omni.  Works with Alexa, SmartThings and Apple Home; **not** Google Home.

What Matter exposes, per TP-Link: start mapping, start cleaning, pause/standby,
recharge/go home, vacuum mode (Quiet/Standard/Turbo/Max), vacuum+mop mode and
water level — i.e. the RVC Run Mode / RVC Clean Mode / RVC Operational State
cluster trio.  Matter 1.4 added a Service Area cluster for cleaning named zones,
but TP-Link does not document supporting it.
<https://www.tp-link.com/us/support/faq/4395/>

**Verdict: solves none of your three problems**, and notably has *no* concept of
"go to this coordinate" — so it cannot even express your #1 workaround.  It is a
strict subset of the LAN API below.

## 4) The app↔robot API — the real answer

### It is local, not cloud

Your comment asked whether this is the phone→cloud API.  It is not.  The Tapo
app speaks **TPAP, authenticated with SPAKE2+, straight to the robot's IP on the
LAN**, and that is what was reverse-engineered (originally by ZeliardM in
python-kasa PR <https://github.com/python-kasa/python-kasa/pull/1592>, still
open).
The integration states it outright: *"No cloud dependency — communicates
directly with the vacuum over your LAN."*  You configure it with the **vacuum's
IP address**, plus your Tapo account email and password — the credentials are
the shared secret for the SPAKE2+ handshake **with the robot**, not a cloud
login.  So the integration should keep working without internet — but that is
**unverified**: the app itself goes through the cloud when away from home, and
restoring a map pulls a cloud backup (see below).

A handful of things genuinely *are* cloud-only, confirmed by the project probing
a real device and getting `UNKNOWN_METHOD_ERROR`:

* **LOCATE** ("find me" / make a sound)
* resolving a schedule's `custom_rule_id` back to room names
* the robot's voice/announcement language (`getVolume`/`setVolume` *are* local)
* usage statistics beyond `getCleanRecords`

There is also a true cloud API (`tplink-cloud-api` on PyPI).  Ignore it — the
LAN route is lower latency, works offline, and exposes far more state.

### What exists today

* **<https://github.com/jan-tdy/TapoVac-ADV>** — explicitly verified on
  **RV50 Pro Omni (EU)**, your exact unit.  Start/pause/stop/dock, spot clean,
  per-room selective cleaning, fan speed, water level, clean passes, map with
  room geometry and furniture polygons, position, current-room inference,
  progress %, battery, consumable wear, error state, Omni dock actions (empty /
  wash mop / dry mop / remove hair), saved schedules, and — the important part —
  **`vacuum.send_command`, a raw passthrough to any device method.**
* **<https://github.com/cavefire/tapo-vacuum-ha>** and
  **<https://github.com/andrew-schofield/tapo-vacuum-ha>** — upstream and fork.
* **<https://github.com/epg-pers/tapo-rv30-ha>** — the first Home Assistant
  vacuum integration over TPAP, built on ZeliardM's reverse engineering in
  python-kasa PR <https://github.com/python-kasa/python-kasa/pull/1592>.
* **python-kasa** itself now carries a vacuum `Clean` module with `setSwitchClean`,
  `setRobotPause`, `setSwitchCharge`, `setCleanAttr`, `getMapInfo`, `getMapData`.
  Usable as a plain Python library, no Home Assistant required.
* HA thread (needs HA 2026.3+):
  <https://community.home-assistant.io/t/ultimate-tapo-rv50-rv30-rv20-max-integration-native-room-cleaning-via-has-own-vacuum-dialog-and-more/1022423>

### 4.1 The gap: what the app can do and the integrations cannot

This is where your comments matter most.  **Point-and-go and the
forward/left/right drive buttons are not implemented in any public
integration.**  Nobody has captured them.  Same for map selection/restore.

Two ways forward:

**(a) Fake point-and-go with a zone clean — available now.**  python-kasa
documents `CleanMode.Zone = 4`, "clean user-defined rectangular areas", sent
through the same `setSwitchClean` call that room cleaning uses:

```json
{"clean_mode": 4, "clean_on": true, "clean_order": true,
 "force_clean": false, "map_id": <int>, "start_type": 1, ...rect...}
```

A 30×30 cm zone at a waypoint is, behaviourally, "drive there".  The exact
rectangle field name still has to be read off the wire, but the mode is known to
exist and `send_command` can post the payload.  This is the shortest path to
your waypoint idea.

**(b) Capture the real calls.**  Watch the app do point-and-go / a drive-button
press / a map restore, and read the method names off the traffic.  The project
is explicitly asking for exactly this kind of capture — Discussion #8,
<https://github.com/jan-tdy/TapoVac-ADV/discussions/8>.  If you do it, the drive
buttons are also what would let an automation *nudge* the robot over the
threshold instead of asking a human.

### What that buys you, per problem

| Problem | Verdict | Approach |
|---|---|---|
| **#1 wrong room on return** | **Workaroundable** — your call, not mine | Waypoint chain: go-to hall → go-to kitchen → dock, scripted. Needs the point-and-go method (4.1b) or a zone-clean stand-in (4.1a). Sequencing it is trivial once one waypoint call works. |
| **#2 forced maintenance cycle** | **Largely fixable** | Two halves — see below. |
| **#3 relocalization → blank map** | **Not fixable** | Restore the saved map and retry localization, as you do manually. Automating even that needs an undiscovered map-select/restore call: `getMapInfo` and `current_map_id` are readable, but no *setter* is public, and multi-map handling is untested against real hardware. |
| **Doorstep "wait for help"** | **Buildable now** | Position + state are already exposed. See below. |

### #2 in detail

You raised a second half I had missed: a **wrong run configuration** (mop when
you wanted vacuum only, rooms 2+3 when you wanted 1+3) also cannot be corrected
without the full dock round-trip.  The API handles both halves better than the
app does:

* **Wrong configuration:** don't correct it — compose it correctly up front.
  `setCleanAttr` sets suction / water level / clean passes, `setSwitchClean` with
  `clean_mode: 3` + `room_list` picks the rooms, then start.  You never touch the
  app's start flow, so there is nothing to "restart".
* **Mid-run correction:** `setRobotPause` pauses without docking.  Whether
  `setCleanAttr` applied while paused takes effect on resume is **unverified** —
  test it; if it works, that is your #2 solved outright.
* **Suppressing the dock cycle itself:** the Omni dock actions are individually
  exposed as commands, which implies a settable switch behind them
  (`setSwitchDustCollection` and siblings are named in the protocol notes).
  Probe via `send_command` for a per-run or global disable.  Unverified.

### The doorstep automation, concretely

Everything needed is already exposed: position, current room, cleaning state.

1. Trigger on position inside a small zone around the threshold, held for N
   seconds without the room changing — i.e. it is stuck trying to cross.
2. `setRobotPause` — keeps it off the dock, so no maintenance cycle.
3. Notify: phone push, and/or TTS on a nearby speaker — *"please lift me over
   the doorstep"*.  The robot's own speaker only plays vendor prompts and the
   language setting is cloud-side, so use a Home Assistant media player.
4. Resume on a button press / NFC tag / voice command after lifting.

Caveat: being lifted is the classic trigger for #3.  Lift a short distance, set
down in the same orientation and the same spot on the far side.  Given Lock Map
does not protect you, the fallback is the manual restore-and-retry you already
use.  A ramp avoids the whole question.

## Suggested order of work

1. **Build the doorstep ramp.**  Highest value per hour, and it removes the
   lifting that triggers #3.
2. Compose runs correctly instead of correcting them — mitigates #2 with no code.
3. Home Assistant + TapoVac-ADV (or python-kasa directly, if you would rather
   script than dashboard).  Confirms the LAN path works before you invest in it.
4. Test whether `setCleanAttr` while paused takes effect on resume.  Cheap, and
   it decides #2.
5. Try a zone clean (`clean_mode: 4`) as a waypoint primitive.  If it works,
   script the waypoint chain for #1.
6. Capture the app's point-and-go / drive-button / map-restore traffic and post
   it to TapoVac-ADV Discussion #8.  This is the one contribution that unlocks
   #1 and #3 properly, for you and everyone else.
7. File at requests.valetudo.cloud and forget about it.

### Your credentials file

You put them in `~/.config/tapo/credentials.yaml` with keys `user` (email) and
`pass`.  The Home Assistant integration asks for IP + email + password in its
config flow, so that file is only useful for scripting.  Its standalone test
script reads `TAPO_HOST` / `TAPO_USER` / `TAPO_PASS` from the environment, so:

```bash
export TAPO_HOST=192.168.x.y
export TAPO_USER=$(yq -r .user ~/.config/tapo/credentials.yaml)
export TAPO_PASS=$(yq -r .pass ~/.config/tapo/credentials.yaml)
```

Same three values work directly with python-kasa.  Check the file is `0600`.

## 5) Outcome — 2026-09-23/24

The LAN route works.  The details have moved out of this document:

* **Protocol** (transport, the `tls:0` bug and fix, map format, every method
  and parameter found, what is verified and what is not):
  `protocol.md`.
* **This house's setup** (IP, map and room ids, zones, tools, what has
  been done to the robot): `field-notes.md`.

Corrections to §4 above, found on the way:

* python-kasa (even the TPAP PR branch) has **no** map, room or zone
  support — `start()` is a whole-house clean only.  `getMapInfo`,
  `getMapData`, room cleaning and `CleanMode.Zone` in §4 came from other
  projects, not from python-kasa.
* Point-and-go (`gotoPoint`) and manual drive (`directionControl`) exist on
  this robot and their parameters are known from the app; §4.1's zone-clean
  stand-in is probably unnecessary.
* The phone app pins certificates and the robot signs its handshake with a
  device key, so capturing traffic (§4.1b) is impractical.  Decompiling the
  app gave the same answers.
* Map restore (`mapRecovery`) pulls a cloud backup, so scripted
  restore-and-retry for #3 depends on the cloud.

## Typos in `original-questions.md`

* "annyoing" → annoying
* "If doing a mistake" → if making a mistake
* "even if the base haven't been moved" → hasn't
* "not finding it's position" → its position
