# Notes and documentation

Copied on 2026-09-26 from the author's private notes, and scrubbed of
private details.  Room names are the robot's own, except the two
bedrooms, which are "bedroom 1" and "bedroom 2" here whatever the robot
calls them.  `tapo-run-queue.sh`, mentioned in places, is a private shell
prototype and not published.  Payloads are the ones sent to the robot;
`runCleanTask-bedroom1-kitchen-outerhall.json` was sent while contact
was lost, and its outcome is unknown.
Dates are when things were found out.

| File | What |
|---|---|
| [design.md](design.md) | OpenTapoVac design: daemon, CLI, web UI, home rules |
| [protocol.md](protocol.md) | The robot's local API as far as known: transport, map, cleaning, status and error codes, movement |
| [robot-methods.txt](robot-methods.txt) | All method names in the Tapo app's robot enum |
| [payloads/](payloads/) | Exact write payloads that were sent to a real robot |
| [scripts/](scripts/) | One-off robot scripts for this house (a room-border move) |
| [tapo-clean-spec.md](tapo-clean-spec.md) | The earlier CLI-only spec; still the reference for the monitor loop, doorstep behaviour and the robot tests to do |
| [field-notes.md](field-notes.md) | The robot, the map and rooms, and a log of what was done to it |
| [network.md](network.md) | Putting the robot on an isolated IoT network, WireGuard for remote access |
| [research.md](research.md) | Initial research: known problems, Valetudo, Matter, existing integrations |
| [original-questions.md](original-questions.md) | The questions that started it |
| [vacuum-review.md](vacuum-review.md) | The author's user review of the robot (Norwegian, 2/5 stars) |

The notes are written in the first person in places; "the user" and
"you" are the author.
