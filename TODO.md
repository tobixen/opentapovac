# TODO

## Map renderer (`docs/tapo-render-map.py`, becomes `mapimg.py` in milestone 3)

* A docked robot is drawn at the map origin: `real_vac_coor` reads
  `[0, 0, 0]` while docked, and `if not d.get(key)` doesn't skip a
  non-empty list.  Skip when `not any(d[key][:2])`.
* Raw `kasa --json` output starts with WARNING lines (see
  `docs/protocol.md`), so `json.load` fails on it.  Strip up to the
  first `{`, or document that the dump must be clean JSON.
* Missing arguments raise `IndexError` instead of printing the usage.
