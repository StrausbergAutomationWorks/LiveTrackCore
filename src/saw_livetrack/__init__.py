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

  saw_livetrack.tfnsw     Transport for NSW's nswtrains feed (0.2.23): each
                          vehicle to the intercity, commuter or bus consumer, one
                          update per position's run, the misleading fields
                          cleared, the timetable on disk. Each user's own key.
                          NOT imported here; needs the `gtfs` extra. Data:
                          Transport for NSW, CC BY 4.0.

  saw_livetrack.transport_vic  Transport Victoria (0.2.23): a consumer's folder
                          of the GTFS Schedule by HTTP Range, the URL read from
                          the portal each time, and V/Line's realtime with each
                          user's own key. NOT imported here; realtime needs the
                          `gtfs` extra. Data: Transport Victoria, CC BY 4.0.

  saw_livetrack.irish_rail    Ireland's trains from Irish Rail's realtime API and the
                              NTA's timetable (0.2.32): one keyless poll for every
                              consumer, routed by train code (A/B intercity, P/D
                              commuter, E transit); every run drawn from the
                              timetable, positions as updates (0.2.34)
  saw_livetrack.timetable     where a train is by its calls' times alone (0.2.34):
                              call_times, place, service_base. No dependencies
  saw_livetrack.ns            the Netherlands (0.2.35): NS's Virtual Train positions
                              with each user's own key, one poll for every consumer,
                              routed by type (IC and ARR intercity, SPR commuter);
                              SpoorKaart's track; OVapi's timetable and train updates.
                              NOT imported here; updates need the `gtfs` extra.
  saw_livetrack.trafikverket  Sweden's trains from Trafikverket (0.2.31): one
                          poll for every consumer with each user's own key,
                          positions joined to their runs' announcements and
                          routed by traffic type and brand to the intercity,
                          commuter and transit consumers. NOT imported here;
                          no dependencies. Data: Trafikverket, CC0.

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
                          parser (0.2.19); claims, and no empty VESSELS
                          list (0.2.20). NOT imported here; aiohttp
                          imported only when it connects.
  saw_livetrack.enforcement_vessels
                          The public EnforcementVessels list (ODbL) and its
                          theatres, read once per instance, cached,
                          checksum checked (0.2.20; moved from Live Track
                          SALT); the look of a listed vessel
                          (0.2.21; moved from Live Track Ships). NOT
                          imported here; no dependencies.
  saw_livetrack.faa_registry
                          The FAA registry for the aircraft integrations,
                          once per instance: government registrants with
                          owner class and agency short names, and every
                          registration's owner; one download, cached
                          (0.2.15). NOT imported here; no dependencies.

`amtraker` and `gtfs` import from `track`. Never the reverse.

Not affiliated with Amtrak, VIA Rail Canada, Brightline, VR, Fintraffic, Renfe,
Transport for NSW or Transport Victoria.
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

__version__ = "0.2.35"
