"""Is this position on a railway? A grid index over rail lines.

Lee, 2026-10-08: a train's position that is not on any railway is refused
(Live Track Commuter Rail SSOT 7.10). Measured first: the line's own GTFS shape
refuses real trains (a layover 1.5 km past a terminal; a train on another
line's approach), while ALL rail separates - every real Metra and NICTD fix
in 9,109 lay within 210 m of FRA NARN track, and the bad unit's 40 fixes ran
374 m to 1,060 m off.

No Home Assistant imports and no dependencies. The lines are the caller's: an
integration ships them as data (Live Track Commuter Rail builds one file per
railroad from NARN). This module only answers distances.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

_R = 6371008.8          # mean Earth radius, metres
_DEG_M = _R * math.pi / 180.0


class RailIndex:
    """Rail lines, each a sequence of (lat, lon), indexed on a 0.01 degree grid.

    Distances are on a local flat projection around the query point, which
    over a few kilometres differs from the great circle by far less than the
    tolerance any caller uses.
    """

    CELL_DEG = 0.01

    def __init__(self, lines: Iterable[Sequence[Sequence[float]]]) -> None:
        self._segs: list[tuple[float, float, float, float]] = []
        self._grid: dict[tuple[int, int], list[int]] = {}
        c = self.CELL_DEG
        for line in lines:
            pts = [(float(p[0]), float(p[1])) for p in line]
            for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:]):
                i = len(self._segs)
                self._segs.append((a_lat, a_lon, b_lat, b_lon))
                for la in range(math.floor(min(a_lat, b_lat) / c), math.floor(max(a_lat, b_lat) / c) + 1):
                    for lo in range(math.floor(min(a_lon, b_lon) / c), math.floor(max(a_lon, b_lon) / c) + 1):
                        self._grid.setdefault((la, lo), []).append(i)

    def __len__(self) -> int:
        return len(self._segs)

    def distance_m(self, lat: float, lon: float, within_m: float) -> float | None:
        """Metres to the nearest rail if some lies within `within_m`, else None."""
        c = self.CELL_DEG
        kx = math.cos(math.radians(lat)) * _DEG_M      # metres per degree of longitude here
        ky = _DEG_M
        rl = int(math.ceil(within_m / (c * ky)))
        ro = int(math.ceil(within_m / (c * max(kx, 1.0))))
        cl, co = math.floor(lat / c), math.floor(lon / c)
        cand: set[int] = set()
        for la in range(cl - rl, cl + rl + 1):
            for lo in range(co - ro, co + ro + 1):
                cand.update(self._grid.get((la, lo), ()))
        best = None
        for i in cand:
            a_lat, a_lon, b_lat, b_lon = self._segs[i]
            ax, ay = (a_lon - lon) * kx, (a_lat - lat) * ky
            bx, by = (b_lon - lon) * kx, (b_lat - lat) * ky
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            t = 0.0 if den == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / den))
            d = math.hypot(ax + t * dx, ay + t * dy)
            if best is None or d < best:
                best = d
        return best if best is not None and best <= within_m else None

    def near(self, lat: float, lon: float, within_m: float) -> bool:
        """True when some rail lies within `within_m` metres."""
        return self.distance_m(lat, lon, within_m) is not None
