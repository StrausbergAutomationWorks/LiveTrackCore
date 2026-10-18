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

  saw_livetrack.map_card  Which Live Track Map Card bundle an instance loads
                          and how its one dashboard resource row changes
                          (0.2.8). Pure; the Home Assistant calls are in
                          reference/shared_map_card.py. NOT imported here.

`amtraker` and `gtfs` import from `track`. Never the reverse.

Not affiliated with Amtrak, VIA Rail Canada, Brightline, VR or Fintraffic.
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

__version__ = "0.2.8"
