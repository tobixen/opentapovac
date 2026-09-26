# OpenTapoVac

Local control of a TP-Link Tapo robot vacuum (developed against the
RV50 Pro Omni): a daemon, a command-line tool and a small web page for
day-to-day cleaning, with house-specific rules kept in a config file.

*This README was written by an AI assistant (Claude) and reviewed by
the author.*

## What this is, and what it is not

**Not open firmware.**  The "Open" is about the software on your side.
There is no Valetudo port for Tapo robots and no known way to root
them, so we are stuck with the vendor firmware.  What we can use is the
local API the Tapo app itself talks to the robot over the LAN (TPAP,
reverse-engineered, see
[python-kasa PR #1592](https://github.com/python-kasa/python-kasa/pull/1592)).
Everything here goes through that API, and a firmware update may break
it.

The goal is not to replace the Tapo app.  It is to make the everyday
runs easy for the whole household and to add what the app lacks:
room ordering rules, rooms the robot must be carried into or out of,
watching relocation so a bad guess doesn't ruin the map, and sensible
handling of "dock not found".

## Status

Early.  The command-line tool, the daemon, the web page and the home
rules (room order, carrying the robot, a position check after carrying)
exist (milestones 1–4 in [docs/design.md](docs/design.md)), but have not
yet cleaned a room on their own; recovery when the dock is not found is
still to come.  Background and protocol notes are
in [docs/](docs/).

## Installation

TPAP support is not in a python-kasa release yet, so the package pulls
python-kasa from a git branch (see docs/design.md, "Decisions") and is not
on PyPI.

```
make install
```

(This auto-detects `uv`, `pipx`, or `pip` and does the right thing.)
Tab completion for bash and zsh comes with it; bash needs the
`bash-completion` package.

## Configuration

`~/.config/opentapovac/config.yaml`; the account password stays in
`~/.config/tapo/credentials.yaml` (`user:` and `pass:`, mode 0600), or in
`$KASA_USERNAME`/`$KASA_PASSWORD`.  A minimal config:

```yaml
robot: {host: 192.0.2.10}
timezone: Europe/Oslo
defaults: {mode: vac_then_mop, suction: 2, water: 2, passes: 1}
rooms:                       # keyed by the robot's room name or id
  "kjøkken": {aliases: [kitchen]}
  6: {aliases: [outer hall]}
  stairs: {forbidden: true}  # refused without --force
  "bedroom 2": {carry_in: true}    # can't get in by itself
  "living room": {carry_out: true} # can't get out by itself
order: {first: ["bedroom 1", 6], last: ["kjøkken"]}  # within a run
presets:
  - {label: "Halls + kitchen", rooms: [5, 6, "kjøkken"]}
listen: 127.0.0.1:8765       # the daemon
human_wait_timeout: 15m      # then an alert; it keeps waiting
```

Room names come from the robot; `opentapovac rooms --refresh` reads them.
The poll interval and the monitor's timeouts can be set under `monitor:`
(see `src/opentapovac/config.py`).

## Usage

Run `opentapovac --help`, or `opentapovac COMMAND --help`, for the full
list of options.

```
opentapovac clean kitchen "outer hall"      # one run, rooms in this order
opentapovac clean --mop --sequential hall 6 # one run per room
opentapovac status | stop | rooms | log
opentapovac answer done                     # "carry the robot into ..."
opentapovac map map.png                     # map with the last track
opentapovac serve                           # the daemon and the web page
```

Without a daemon, `clean` blocks until the robot is back on the dock and
reports what happens on the way (stuck, dock not found, relocation, empty
water tank).  With a daemon running, every command goes through it and
`clean` returns at once; `--wait` follows the job.

The daemon binds to localhost.  To reach the web page from phones, put a
reverse proxy with authentication in front of it; the daemon has no
accounts of its own.

A carry room gets a run of its own, before the others.  For a
`carry_in` room the run starts from the dock as usual (so the mops go on
first), and the job asks someone to carry the robot in when it leaves
the base; the robot waits at the doorstep.  A minute after it is put
down the job checks that the robot knows where it is, and stops it if
it thinks it is in another room.  "skip" drops the room and goes on with
the rest.  For a `carry_out` room it asks each time the robot heads for
the dock.  Lifting the robot and putting it down counts as "done".  The question shows on the web page, on the
terminal of a standalone or `--wait` clean, and in `opentapovac status`;
the job waits until someone answers or stops it.

Before a run the robot's map must be locked and "auto change map" off,
since a bad relocation can otherwise overwrite the map; `--force` skips
that check.

## Are you using this?  Please say so

The author will not own this robot forever.  If you use OpenTapoVac, or
would like to, please open an issue or a discussion and say which robot
you have.  Knowing there are other users matters, and at some point
someone else will need to take over as maintainer.

## License

AGPL-3.0-or-later.  See [LICENSE](LICENSE).
