# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) - except, for pre-releases PEP440 takes precedence.

## Unreleased

### Fixed

* The map no longer draws the robot moving through walls and outside the
  house: after a sub-path marker the robot may record points counted from
  (0, 0) until it finds itself on the map, then jump to its real place.
  Those points are left out, and no line is drawn across a jump.

## 0.1.0 - 2026-09-28

Initial release.  See the [README](README.md) for what it does.
