"""saw_livetrack.gtfs - shared GTFS and GTFS-Realtime code (backlog 274).

  realtime   Obs (one vehicle in one poll), vehicle_time, position_of, decode
  schedule   static GTFS distilled; next station, estimated arrival, delay
  fleet      one feed's vehicles across polls: what to publish, each record

No Home Assistant imports. Only realtime.decode() needs the protobuf
bindings, which come with the extra: pip install "saw-livetrack[gtfs]".
Nothing here is imported by `import saw_livetrack`.
"""

from .realtime import Obs, decode, position_of, vehicle_time  # noqa: F401
from .schedule import (  # noqa: F401
    AHEAD_S,
    PAST_S,
    STATIC_VERSION,
    distill,
    iso_utc,
    line_name,
    next_stops,
    stop_delays,
)
from .fleet import (  # noqa: F401
    GRACE_S,
    HOLD_S,
    JITTER_M,
    MAX_SEGMENT_S,
    PLATFORM_HOLD_S,
    REST_S,
    STALL_M,
    Fleet,
    metres,
)
