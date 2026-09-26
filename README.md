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

Design only; nothing works yet.  See [docs/design.md](docs/design.md) and the notes in [docs/](docs/).

## Are you using this?  Please say so

The author will not own this robot forever.  If you use OpenTapoVac, or
would like to, please open an issue or a discussion and say which robot
you have.  Knowing there are other users matters, and at some point
someone else will need to take over as maintainer.

## License

AGPL-3.0-or-later.  See [LICENSE](LICENSE).
