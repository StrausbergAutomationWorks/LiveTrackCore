# saw-livetrack

Shared code for the **Live Track** Home Assistant integrations.

Three submodules, deliberately separate.

## `saw_livetrack.track` — source-agnostic

Holds the last two **distinct** fixes per object and emits the `previous_*`
segment fields, so a consumer can animate between two **observed** positions
rather than extrapolating toward a projection.

The bug it prevents is not specific to any feed. **Any** integration whose poll
interval sits near its source's publish interval receives duplicate fixes, and
storing a duplicate as the previous fix collapses the segment to zero length —
which renders as a stutter on the map. Every individual update looks correct in
isolation; only the sequence is wrong.

The timestamp extractor is injected, because only the source knows what its own
timestamp means.

```python
from saw_livetrack.track import FixTracker

t = FixTracker()                      # or FixTracker(observed_at=my_extractor)
t.update("vehicle-1", record)
attrs = t.segment_fields("vehicle-1") # previous_latitude, course_deg, ...
```

Keys that cannot be supplied honestly are **omitted**, never set to `None` or a
sentinel. A stationary object has no course, and that absence is correct.

## `saw_livetrack.amtraker` — the Amtraker API client

For Live Track Amtrak, Live Track VIA Rail and Live Track Brightline. One
cached fetch serves all three providers, since the endpoint takes no parameters
and returns every train of every provider.

What it refuses to do, and why — each refusal came from a measurement:

| Behaviour | Reason |
|---|---|
| `course_deg()` always returns `None` | the feed's direction is an eight-value octant string; converting it invents precision the feed never carried. A real course comes from two observed positions instead |
| `speed_kmh()` returns `None` for Brightline even when non-zero | measured 0.0 on every instance while trains covered 76–133 km at 44–77 mph. The field is unpopulated, not stationary |
| `observed_at()` returns `None` for Brightline | its timestamp advances every 30 s while the position changes every 60 s — a feed job clock, not an observation time |
| `observed_at()` returns `None` for `Predeparture` trains | that field then carries a scheduled *future* departure |
| a finished train is handled by type | the API returns a bare `[]`, not the documented keyed object |
| HTTP 429 raises `RateLimited` | a non-200 must never be recorded as "no data" |
| an empty `User-Agent` raises at construction | the server blocks such requests |

`amtraker` imports from `track`. Never the reverse.

## `saw_livetrack.gtfs` — GTFS and GTFS-Realtime

Moved here from Live Track Commuter Rail when Live Track Intercity Rail became
its second consumer. No Home Assistant code: storage, coordinators, entities and
config flows stay in each integration.

| Module | Holds |
|---|---|
| `realtime` | `Obs`, one vehicle in one poll; `vehicle_time` and `position_of`, the readers every adapter shares; `decode` |
| `schedule` | a GTFS static zip distilled to stations, their coordinates, line names, scheduled times for the trips running yesterday, today and tomorrow, and (where the realtime feed names stops only by sequence) stop times; the next station, its estimated arrival, and the delay there (`stop_delays`: the feed's own delay, else predicted minus scheduled) from TripUpdates; the dataset's `feed_version` from `feed_info.txt` when present; and `trip_coverage`, how many of a TripUpdates feed's trips the cached timetable knows (a low share means it is out of date: Metra's 2026 GTFS static change renames every trip id) |
| `fleet` | one feed's vehicles across polls: motion, the hold for equipment not in service, held course at rest, grace for a vehicle missing from the feed, and each published record, with `delay_min` (whole minutes toward zero) and `stalled` (in service and standing: for `REST_S` when farther than `STALL_M` from every stop, for `PLATFORM_HOLD_S` when at a stop); with an `on_rail` check, a train in service whose fix is not on rail is held at its last fix on rail for at most `REFUSED_HOLD_S`, then hidden |

What a given feed actually carries is **not** here. Each integration keeps one
small adapter per railroad that turns that feed into `Obs` records and states
only what the feed really populates.

```
pip install "saw-livetrack[gtfs]"
```

The `gtfs` extra brings the protobuf bindings, which only `decode` needs, and
`tzdata`, without which `schedule` cannot read a timetable's times on a system
with no timezone database and publishes no next station.
`import saw_livetrack` does not import this module, so the Amtraker consumers
neither load it nor need the extra.

## `saw_livetrack.rail` — is this position on a railway?

`RailIndex(lines)` indexes rail lines, each a sequence of (lat, lon), on a 0.01 degree
grid; `distance_m(lat, lon, within_m)` and `near(lat, lon, within_m)` answer from it.
No dependencies. The lines are the caller's: an integration ships them as data
(Live Track Commuter Rail builds one file per railroad from the FRA North American
Rail Network, public domain). `Fleet(on_rail=...)` takes such a check.

## Data attribution

Train data is provided by **[Amtraker](https://amtraker.com)** and is licensed
under the [Open Data Commons Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/).

## Disclaimer

Independent project. Not affiliated with, endorsed by or connected to Amtrak,
VIA Rail Canada or Brightline.

## Tests

```
python tests/test_track.py
python tests/test_amtraker.py
python tests/test_shared_coordinator.py
python -m unittest discover -s tests -p "test_gtfs_*.py"
```

The first three are scripts that exit non-zero on a failed check; run them
directly, not through `unittest discover`.

## Licence

MIT.
