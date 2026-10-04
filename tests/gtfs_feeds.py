"""Ported from Live Track Commuter Rail tests/feeds.py (df972df), backlog 274.

Synthetic GTFS-RT feeds for the tests. Shapes follow what each feed was
measured to carry (SSOT section 4 for Metra; the NICTD adapter's docstring).
No real feed bytes are committed: the data belongs to the operators."""

from google.transit import gtfs_realtime_pb2

T0 = 1790925150


def feed():
    fm = gtfs_realtime_pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = T0 + 30
    return fm


def vehicle(fm, ent_id, vid=None, lat=41.88, lon=-87.95, ts=T0, label=None,
            route=None, trip=None, bearing=None, position=True):
    e = fm.entity.add()
    e.id = ent_id
    v = e.vehicle
    if vid is not None:
        v.vehicle.id = vid
    if label is not None:
        v.vehicle.label = label
    if route is not None:
        v.trip.route_id = route
    if trip is not None:
        v.trip.trip_id = trip
    if position:
        v.position.latitude = lat
        v.position.longitude = lon
        if bearing is not None:
            v.position.bearing = bearing
    if ts is not None:
        v.timestamp = ts
    return e


def trip_update(fm, trip, stops):
    """stops: [(stop_sequence, arrival_delay_s)]"""
    e = fm.entity.add()
    e.id = f"tu_{trip}"
    e.trip_update.trip.trip_id = trip
    for seq, delay in stops:
        s = e.trip_update.stop_time_update.add()
        s.stop_sequence = seq
        s.arrival.delay = delay
    return e
