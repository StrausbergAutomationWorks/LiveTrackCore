"""SAW Amtraker client - shared by the Live Track passenger-rail family.

Data: Amtraker (https://amtraker.com), licensed ODC-By v1.0.
This package is NOT affiliated with Amtrak, VIA Rail or Brightline.
"""

from .client import (  # noqa: F401
    AmtrakerClient,
    AmtrakerError,
    RateLimited,
    FeedUnavailable,
    CONTRACT_VERSION,
    SHARED_KEY,
    PROVIDERS,
    JITTER_M,
    SNAP_KM,
    MAX_PLAUSIBLE_KMH,
    FEED_TICK_S,
    POSITION_INTERVAL_S,
    VELOCITY_UNPOPULATED,
    BATCH_STAMPED,
    parse_ts,
    observed_at,
    haversine_km,
    classify_movement,
    speed_kmh,
    derived_speed_kmh,
    course_deg,
    heading_octant,
)

from .tracker import (  # noqa: F401
    FixTracker,
    bearing_deg,
    COURSE_MIN_M,
)

__version__ = "0.2.0"
