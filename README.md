# saw-livetrack

Shared code for the **Live Track** Home Assistant integrations.

Separate submodules, deliberately.

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

## `saw_livetrack.zones` — the time zone at any coordinate

`zone_at(lat, lon)` gives the IANA zone name (`"America/Chicago"`), anywhere on
Earth; open sea gives a nautical zone (`"Etc/GMT+5"`). `local_time(when, lat, lon)`
and `utc_offset_min(when, lat, lon)` give the local clock there for an aware
datetime. Install the `zones` extra (`pip install saw-livetrack[zones]`): it brings
[tzfpy](https://github.com/ringsaturn/tzfpy) and tzdata. Without tzfpy, `zone_at`
raises `ImportError`. A feed's own stated zone is not trusted: Amtrak's GTFS puts
Arizona on Denver time and the Mississippi Gulf Coast on New York time; the
coordinate puts them right.

## `saw_livetrack.digitraffic_rail` — Finland's trains (Fintraffic Digitraffic)

One poller for every Live Track integration that draws Finnish trains. It joins
`train-locations/latest` (every train with a fix, passenger or not) to the train
list (`trains/<date>`, then `trains?version=` for only what changed), and routes
each train to ONE consumer by `trainCategory`: `Long-distance` to `intercity`,
`Commuter` to `commuter`, every other category (cargo, locomotives, shunting,
track machines, test runs) to `freight`. One train TYPE overrides its category:
`MV`, empty passenger stock, which Digitraffic files under `Long-distance`,
goes to `freight` (it carries no passengers), and so does `V`, a depot or yard
train (varikkojuna). `next_stop(train)` gives the next
station, its ETA and the delay in whole minutes. Sends the `Accept-Encoding: gzip`
Digitraffic requires and a `Digitraffic-User` header. No extra dependencies.
Share one client per Home Assistant with `reference/shared_digitraffic_rail.py`:
each integration registers the consumer it draws, and the client keeps
timetables only for registered consumers, so an integration installed alone
costs only its own trains (0.2.7). Every fetch and table change is under one
lock; `register()` and `unregister()` never wait on it. Each joined record
carries its `train`.

## `saw_livetrack.renfe` - Spain's commuter trains (Renfe)

One poller for every Live Track integration that draws Renfe's vehicles. Renfe
publishes ONE GTFS-Realtime feed of positions and one of trip updates for all its
Cercanías networks and Rodalies de Catalunya, keyless, about every 20 s, and the
feed carries more than commuter trains: rail-replacement and feeder buses
(`route_type` 3 in Renfe's own timetable) and Catalonia's regional lines (R11-R17).
Each vehicle goes to ONE consumer: `bus` for a bus, `intercity` for a regional
line, `commuter` for every other train. `for_consumer(name)` returns that
consumer's positions and trip updates as two GTFS-Realtime feeds, cleaned once for
everyone: the trip id is the identity (Renfe's entity ids repeat across networks),
a trip appearing twice is kept once, `0,0` is dropped, and a position that jumps
straight back to where the train was two fixes earlier is held. The timetable
(16 MB, for telling the buses from the trains) loads on its own thread and then
daily; until the first load ends every feed is empty, so a bus is never drawn as
a train, and `static_zip()` hands the same bytes to a consumer that distills them.
`parse_trip_id()` reads the network, train number and line out of a trip id. Needs
the `gtfs` extra. Share one client per Home Assistant with
`reference/shared_renfe.py`; each integration registers the consumer it draws
(0.2.9). Renfe leaves a whole network out of about one batch in four; such a
network is served from the batch it was last in for up to a minute, positions
and trip updates both (0.2.10).

## `saw_livetrack.map_card` - one map card, many integrations

Every Live Track integration ships the released
[Live Track Map Card](https://github.com/StrausbergAutomationWorks/LiveTrackMapCard),
serves it and offers it; the instance loads the highest version, from exactly
one dashboard resource row. This module decides: it reads the version stamped
on the bundle's first line, orders versions by SemVer precedence (build
metadata ignored, ties keep what is registered), builds and parses the
resource URL (`?v=` percent-encoded), and plans the change to the row. It never
touches a row under `/hacsfiles/`, and it updates rows in place rather than
adding them. No dependencies, no Home Assistant import. Each integration
copies `reference/shared_map_card.py` verbatim for the Home Assistant calls
(0.2.8).

## `saw_livetrack.aircraft_icons` - aircraft marker icons

The marker icons of the Live Track aircraft integrations, so each draws an
aircraft the same way: the ICAO type designator to icon table, the SVG
artwork, `render()` (fill by owner class, a dark edge, gentle shading,
police lights, a size ramp by ADS-B emitter category), and `ART_REV`, the
picture revision a consumer puts in its URLs. Each integration serves the
pictures from its own view; `picture_query()` gives the query string. No
dependencies, no Home Assistant import (0.2.11).

Most of the icons are by **[ADS-B Radar](https://adsb-radar.com)**, from its
[SVG aircraft icon set](https://adsb-radar.com/help/icons.html), used under
the author's terms, which ask for a backlink to ADS-B Radar. They are not
covered by this package's MIT licence, which covers the code; the icons drawn
for this project were donated to ADS-B Radar and are under the same terms.
Which is which, and what was changed:
[`src/saw_livetrack/aircraft_icons/ATTRIBUTION.md`](src/saw_livetrack/aircraft_icons/ATTRIBUTION.md).

## Data attribution

Train data is provided by **[Amtraker](https://amtraker.com)** and is licensed
under the [Open Data Commons Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/).

Time-zone boundaries (`saw_livetrack.zones`) are from
**[Timezone Boundary Builder](https://github.com/evansiroky/timezone-boundary-builder)**,
embedded in tzfpy, and are licensed under the
[Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/).
`zones.DATA_VERSION()` names the release.

Finnish rail data (`saw_livetrack.digitraffic_rail`) is from **Fintraffic /
[digitraffic.fi](https://www.digitraffic.fi/en/railway-traffic/)**, licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

Spanish rail data (`saw_livetrack.renfe`) is from **[Renfe](https://data.renfe.com/)**,
licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

## Disclaimer

Independent project. Not affiliated with, endorsed by or connected to Amtrak,
VIA Rail Canada, Brightline, VR, Fintraffic or Renfe.

## Tests

```
python tests/test_track.py
python tests/test_amtraker.py
python tests/test_shared_coordinator.py
python tests/test_digitraffic_rail.py
python tests/test_map_card.py
python tests/test_shared_map_card.py
python tests/test_renfe.py
python tests/test_aircraft_icons.py
python -m unittest discover -s tests -p "test_gtfs_*.py"
python -m unittest tests.test_zones
```

The first four, and `test_aircraft_icons.py`, are scripts that exit non-zero on a failed check; run them
directly, not through `unittest discover`.

## Licence

MIT, except the ADS-B Radar aircraft icons, which are under their author's
terms: see `src/saw_livetrack/aircraft_icons/ATTRIBUTION.md`.
