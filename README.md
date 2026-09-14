# saw-livetrack

Shared code for the **Live Track** Home Assistant integrations.

Two submodules, deliberately separate.

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

## Data attribution

Train data is provided by **[Amtraker](https://amtraker.com)** and is licensed
under the [Open Data Commons Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/).

## Disclaimer

Independent project. Not affiliated with, endorsed by or connected to Amtrak,
VIA Rail Canada or Brightline.

## Licence

MIT.
