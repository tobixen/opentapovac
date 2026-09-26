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
  status, event log and map.
- Home rules from the config: room order within a run (`order.first`,
  `order.last`), and carry rooms (`carry_in`, `carry_out`) that get their
  own run and ask a human to carry the robot, with a position check
  after carrying it in.  Answers come from the web page, the terminal, or
  `opentapovac answer`.
