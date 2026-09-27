# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) - except, for pre-releases PEP440 takes precedence.

## [Unreleased]

### Added
- Python package `opentapovac` with the `opentapovac` command: `clean`,
  `status`, `stop`, `home`, `rooms`, `log`, `map`, `render-map` and
  `serve`.
- A monitor that follows a run until the robot is back on the dock and
  reports stuck, dock-not-found, relocation and empty-water-tank events.
- A daemon (`opentapovac serve`) with an HTTP API; the other commands use
  it when it answers and run standalone when it doesn't.
- A web page for day-to-day runs: room and preset buttons, mode, stop,
  status, event log, map and the version next to the title.
- Home rules from the config: room order within a run (`order.first`,
  `order.last`), and carry rooms (`carry_in`, `carry_out`) that get their
  own run and ask a human to carry the robot, with a position check
  after carrying it in.  Answers come from the web page, the terminal, or
  `opentapovac answer`.
- The daemon keeps the robot's track and its clean records (time, area,
  mop washes) before the robot forgets them, also for runs started from
  the app.  The map shows the tracks of the last 12 hours (adjustable),
  with mopping in its own colour, and checkboxes on the web page for
  vacuuming, mopping and movement.
- A Nix package and a NixOS module (`services.opentapovac`).
- Releases on PyPI: `pip install opentapovac`.
- A progress line every minute of a run (percent done, vacuuming or
  mopping, room, battery), and warnings when a vacuum pass is skipped or
  the robot makes no progress for five minutes.
- When the robot finishes a run without its mop pass, the mopping is
  sent once more; when it leaves a run unfinished, the web page asks
  whether to send it again.  Not with the battery low or the water tank
  empty.  `redo_missed: false` in the config turns this off.
- Send the robot to a room or a spot: tap the map on the web page, or
  `opentapovac goto ROOM` / `goto X Y`.  Meant for guiding it home when
  it can't find the dock.  Only floor points outside no-go zones are
  sent; not yet tried on the robot from here.
- A "Return to dock" button on the web page, next to Stop; not yet
  tried on the robot.  Like going to a spot, it ends the current job.
