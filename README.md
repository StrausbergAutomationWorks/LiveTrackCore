# saw-amtraker-client

Shared Amtraker API client for the **Live Track** passenger-rail integrations
for Home Assistant: Live Track Amtrak, Live Track VIA Rail and Live Track
Brightline.

Pure Python, no Home Assistant imports. Each integration declares it in
`manifest.json` `requirements`.

## Why this is a separate package

HACS permits only **one integration per repository**, so three integrations
cannot share a folder. Vendoring the same client into three repositories is how
three subtly different clients get written, so it ships as a dependency instead.

## What it refuses to do, and why

Each refusal comes from a measurement against the live feed, not from caution.

| Behaviour | Reason |
|---|---|
| `course_deg()` always returns `None` | the feed's direction is an eight-value octant string; converting `"SW"` to `225.0` invents precision the feed never carried |
| `speed_kmh()` returns `None` for Brightline, even when the field is non-zero | measured 0.0 on every instance while trains covered 76-133 km at 44-77 mph. The field is unpopulated, not stationary |
| `observed_at()` returns `None` for Brightline | its `lastValTS` advances every 30 s while the position changes every 60 s. It is a feed refresh clock, not an observation time |
| `observed_at()` returns `None` for `Predeparture` trains | that field then carries a scheduled *future* departure |
| a finished train is handled by type | the API returns a bare `[]` list, not the documented keyed object |
| HTTP 429 raises `RateLimited` | a non-200 must never be recorded as "no data" |
| an empty `User-Agent` raises at construction | the server blocks such requests, so failing here is clearer than an empty result later |

A terminal snap - the feed resetting a finished train's position to its
terminus - is detected by **implied speed**, not by a fixed distance. A flat
distance threshold discards genuine movement: at 125 mph over the observed
180 s maximum interval a train legitimately covers 10 km.

## Caching

`/v3/trains` takes no parameters and returns every provider, so N consumers
would otherwise make N identical system-wide requests. One client instance
serves all three providers from a single cached fetch.

## Data attribution

Train data is provided by **[Amtraker](https://amtraker.com)** and is licensed
under the [Open Data Commons Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/).

## Disclaimer

Independent project. Not affiliated with, endorsed by or connected to Amtrak,
VIA Rail Canada or Brightline.

## Licence

MIT.
