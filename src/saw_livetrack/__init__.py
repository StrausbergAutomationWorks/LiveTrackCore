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

`amtraker` imports from `track`. Never the reverse.

Not affiliated with Amtrak, VIA Rail Canada or Brightline.
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

__version__ = "0.1.0"
