"""SAW LiveTrack - shared code for the Live Track Home Assistant integrations.

Two submodules, deliberately separate:

  saw_livetrack.track     source-agnostic. Holds the last two DISTINCT fixes
                          per object and emits the `previous_*` segment fields
                          so a consumer can animate between two OBSERVED
                          positions. The aliasing bug it prevents is not
                          specific to any feed: ANY integration whose poll
                          interval is near its source's publish interval sees
                          duplicate fixes, and storing one as the previous fix
                          collapses the segment to zero length.

  saw_livetrack.amtraker  the Amtraker API client, for Amtrak, VIA Rail and
                          Brightline. Data from Amtraker (amtraker.com),
                          ODC-By v1.0.

  saw_livetrack.gtfs      GTFS static and GTFS-Realtime: the vehicle record,
                          next station and ETA, and the per-feed fleet
                          (backlog 274). NOT imported here: `import
                          saw_livetrack.gtfs` explicitly, and install the
                          `gtfs` extra for its decode().

  saw_livetrack.rail      is this position on a railway? A grid index over
                          rail lines the caller ships (0.2.4). NOT imported
                          here; no dependencies.

  saw_livetrack.zones     the IANA time zone at any coordinate, and the
                          local clock there (0.2.5). NOT imported here:
                          install the `zones` extra (tzfpy). Data: Timezone
                          Boundary Builder, ODbL v1.0.

  saw_livetrack.digitraffic_rail
                          Finland's trains from Fintraffic Digitraffic,
                          routed by category to the intercity, commuter and
                          freight consumers (MV empty stock by type, to
                          freight). NOT imported here; no
                          dependencies. Data: Fintraffic / digitraffic.fi,
                          CC BY 4.0.

  saw_livetrack.renfe     Spain's Renfe Cercanias and Rodalies feed (0.2.9):
                          positions and trip updates routed to the commuter,
                          intercity (Catalonia's regional lines) and bus
                          (route_type 3) consumers, cleaned once (one row per
                          trip, 0,0 dropped, A-B-A returns held). NOT imported
                          here; needs the `gtfs` extra. Data: Renfe, CC BY 4.0.

  saw_livetrack.map_card  Which Live Track Map Card bundle an instance loads
                          and how its one dashboard resource row changes
                          (0.2.8). Pure; the Home Assistant calls are in
                          reference/shared_map_card.py. NOT imported here.

  saw_livetrack.aircraft_icons
                          The Live Track aircraft marker icons: the type
                          table, the artwork, render(), tones, lights and
                          ART_REV (0.2.11), shared so every integration
                          draws an aircraft the same way. NOT imported
                          here; no dependencies. Icons by ADS-B Radar,
                          https://adsb-radar.com, under their author's
                          terms: aircraft_icons/ATTRIBUTION.md. Lights
                          modes (glow, wig-wag) since 0.2.12; the
                          steady charter glow since 0.2.16.
  saw_livetrack.enforcement_list
                          The public EnforcementAircraft list (ODbL), read
                          once per instance, cached, checksum checked
                          (0.2.12); the ICE Air charter status (0.2.16).
                          NOT imported here; no dependencies.
  airlines              the operator and the hover's short name by callsign
                          (0.2.17; moved from Live Track Aircraft). NOT
                          imported here; no dependencies.
  saw_livetrack.adsb_poll
                          One adsb.fi poll per instance for the aircraft
                          integrations: a union hex round a minute, shared
                          circles, one 429 backoff, claims (0.2.13); a
                          circle inside another's costs no request
                          (0.2.14). NOT imported here; no dependencies.
  saw_livetrack.ais
                          The AIS seam: Observation, the rate-of-turn
                          decode, what a transmitter is, the MMSI statics
                          cache (0.2.19; moved from Live Track Ships).
                          NOT imported here; no dependencies.
  saw_livetrack.aisstream
                          One aisstream.io client per instance: at most two
                          connections per API key, an AREA and a VESSELS
                          subscription, dispatch by box and MMSI, Ships'
                          parser (0.2.19). NOT imported here; aiohttp
                          imported only when it connects.
  saw_livetrack.faa_registry
                          The FAA registry for the aircraft integrations,
                          once per instance: government registrants with
                          owner class and agency short names, and every
                          registration's owner; one download, cached
                          (0.2.15). NOT imported here; no dependencies.

`amtraker` and `gtfs` import from `track`. Never the reverse.

Not affiliated with Amtrak, VIA Rail Canada, Brightline, VR, Fintraffic or Renfe.
"""

from .track import (  # noqa: F401
    COURSE_MIN_M,
    JITTER_M,
    MAX_PLAUSIBLE_KMH,
    SNAP_KM,
    FixTracker,
    bearing_deg,
    haversine_km,
)
from .amtraker import (  # noqa: F401
    BATCH_STAMPED,
    CONTRACT_VERSION,
    FEED_TICK_S,
    POSITION_INTERVAL_S,
    PROVIDERS,
    SHARED_KEY,
    VELOCITY_UNPOPULATED,
    AmtrakerClient,
    AmtrakerError,
    FeedUnavailable,
    RateLimited,
    classify_movement,
    course_deg,
    derived_speed_kmh,
    heading_octant,
    observed_at,
    parse_ts,
    speed_kmh,
)

__version__ = "0.2.19"
