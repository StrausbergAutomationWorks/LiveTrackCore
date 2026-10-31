"""Tests for saw_livetrack.aisstream, the shared client (backlog 315). Offline: a fake session, websocket and clock.

Run: python -m unittest tests.test_aisstream
"""
import asyncio
import enum
import json
import logging
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from saw_livetrack import aisstream as A  # noqa: E402

KEY = "k" * 40
OTHER_KEY = "o" * 40
CHICAGO = [[[44.0, -89.0], [41.0, -86.0]]]
FLORIDA = [[[27.5, -87.0], [21.5, -76.5]]]


class WSMsgType(enum.Enum):
    TEXT = 1
    BINARY = 2
    CLOSING = 256
    CLOSED = 257
    ERROR = 258


FAKE_AIOHTTP = types.SimpleNamespace(WSMsgType=WSMsgType)


class Msg:
    def __init__(self, type_, data=None):
        self.type, self.data = type_, data


def frame(obj):
    return Msg(WSMsgType.BINARY, json.dumps(obj).encode())


def position(mmsi, lat, lon, name="TEST BOAT"):
    return frame({"MessageType": "PositionReport",
                  "MetaData": {"MMSI": int(mmsi), "ShipName": name + "   ", "latitude": lat, "longitude": lon,
                               "time_utc": "2026-10-30 08:00:00.123456789 +0000 UTC"},
                  "Message": {"PositionReport": {"Sog": 5.0, "Cog": 90.0, "TrueHeading": 511, "RateOfTurn": -128,
                                                 "NavigationalStatus": 0}}})


def static5(mmsi, typ=55, name="CG TEST"):
    return frame({"MessageType": "ShipStaticData", "MetaData": {"MMSI": int(mmsi), "ShipName": name},
                  "Message": {"ShipStaticData": {"Name": name, "Type": typ, "CallSign": "NABC",
                                                 "Dimension": {"A": 30, "B": 17, "C": 5, "D": 6}}}})


class FakeWS:
    def __init__(self):
        self.sent = []
        self.q = asyncio.Queue()
        self.close_code = None
        self.closed = False

    async def send_str(self, s):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(json.loads(s))

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return m

    def exception(self):
        return None


class _Ctx:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        self.ws.closed = True
        return False


class FakeSession:
    def __init__(self):
        self.sockets = []
        self.calls = []

    def ws_connect(self, url, **kw):
        self.calls.append((url, kw))
        ws = FakeWS()
        self.sockets.append(ws)
        return _Ctx(ws)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s
        await asyncio.sleep(0)


async def settle(n=30):
    for _ in range(n):
        await asyncio.sleep(0)


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.c = A.Client(clock=self.clock, sleep=self.clock.sleep)
        self.c._aiohttp = FAKE_AIOHTTP
        self.s = FakeSession()
        self.got = {}

    async def asyncTearDown(self):
        for name in list(self.c._consumers):
            await self.c.remove(name)

    def sink(self, name):
        self.got[name] = []
        return self.got[name].append

    def area(self, name, boxes, key=KEY, domain="live_track_ships"):
        self.c.add_area(name, self.s, key, boxes, self.sink(name), domain)

    def vessels(self, name, mmsis, key=KEY, domain="live_track_salt", boxes=None):
        self.c.add_vessels(name, self.s, key, mmsis, self.sink(name), domain, boxes=boxes)


class TestConnections(Base):
    async def test_at_most_two_per_key(self):
        self.area("ships_a", CHICAGO)
        self.area("ships_b", FLORIDA)
        self.vessels("salt", ["367297000"])
        await settle()
        self.assertEqual(len(self.s.sockets), 2)
        for url, kw in self.s.calls:
            self.assertEqual(url, A.URL)
            self.assertEqual(kw["compress"], 15)
            self.assertEqual(kw["heartbeat"], 30.0)
            self.assertEqual(kw["max_msg_size"], 0)
        subs = [ws.sent[-1] for ws in self.s.sockets]
        area = [s for s in subs if "FiltersShipMMSI" not in s]
        vess = [s for s in subs if "FiltersShipMMSI" in s]
        self.assertEqual(len(area), 1)
        self.assertEqual(len(vess), 1)
        self.assertEqual(vess[0]["FiltersShipMMSI"], ["367297000"])
        self.assertEqual(vess[0]["BoundingBoxes"], [A.WORLD])
        # the second area consumer either joined the first subscription or replaced it on the same socket
        self.assertIn(CHICAGO[0], area[0]["BoundingBoxes"])
        self.assertIn(FLORIDA[0], area[0]["BoundingBoxes"])

    async def test_another_key_gets_its_own(self):
        self.area("a", CHICAGO)
        self.area("b", FLORIDA, key=OTHER_KEY)
        await settle()
        self.assertEqual(len(self.s.sockets), 2)
        self.assertEqual({ws.sent[0]["APIKey"] for ws in self.s.sockets}, {KEY, OTHER_KEY})

    async def test_subscription_first_and_replaced_on_same_socket_paced(self):
        self.area("a", CHICAGO)
        await settle()
        ws = self.s.sockets[0]
        self.assertEqual(ws.sent, [{"APIKey": KEY, "BoundingBoxes": CHICAGO}])
        t0 = self.clock.t
        self.area("b", FLORIDA)
        await settle()
        self.vessels("v", ["367297000"])      # other kind: no effect on the area socket
        await settle()
        self.assertEqual(len(ws.sent), 2)
        self.assertEqual(ws.sent[1]["BoundingBoxes"], CHICAGO + FLORIDA)
        self.assertGreaterEqual(self.clock.t - t0, 1.0)  # the service: at most one update a second

    async def test_two_changes_are_paced(self):
        self.area("a", CHICAGO)
        await settle()
        ws = self.s.sockets[0]
        self.c.update("a", boxes=FLORIDA)
        await settle()
        t1 = self.clock.t
        self.c.update("a", boxes=CHICAGO)
        await settle()
        self.assertEqual([s["BoundingBoxes"] for s in ws.sent], [CHICAGO, FLORIDA, CHICAGO])
        self.assertGreaterEqual(self.clock.t - t1, 1.0)  # the service: at most one update a second

    async def test_remove_replaces_then_closes(self):
        self.area("a", CHICAGO)
        self.area("b", FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        await self.c.remove("b")
        await settle()
        self.assertEqual(ws.sent[-1]["BoundingBoxes"], CHICAGO)
        await self.c.remove("a")
        await settle()
        self.assertTrue(ws.closed)
        self.assertEqual(self.c.status()["connections"], [])
        self.assertEqual(self.c.status()["consumers"], [])

    async def test_mmsi_cap(self):
        self.vessels("v1", [f"{366000000 + i}" for i in range(150)])
        with self.assertRaises(ValueError):
            self.vessels("v2", [f"{367000000 + i}" for i in range(51)])
        self.assertNotIn("v2", self.c._consumers)
        with self.assertRaises(ValueError):
            self.c.update("v1", mmsis=[f"{366000000 + i}" for i in range(201)])
        self.assertEqual(len(self.c._consumers["v1"].mmsis), 150)
        self.vessels("v3", [f"{368000000 + i}" for i in range(50)])   # exactly 200: allowed
        await settle()
        self.assertEqual(len(self.s.sockets[0].sent[-1]["FiltersShipMMSI"]), 200)

    async def test_update_own_list_counts_once(self):
        self.vessels("v1", [f"{366000000 + i}" for i in range(150)])
        self.c.update("v1", mmsis=[f"{367000000 + i}" for i in range(150)])   # 150, not 300
        self.assertEqual(min(self.c._consumers["v1"].mmsis), "367000000")

    async def test_same_box_sent_once(self):
        self.area("a", CHICAGO)
        self.area("b", CHICAGO)
        await settle()
        self.assertEqual(self.s.sockets[0].sent[-1]["BoundingBoxes"], CHICAGO)

    async def test_duplicate_consumer_refused(self):
        self.area("a", CHICAGO)
        with self.assertRaises(ValueError):
            self.area("a", FLORIDA)

    async def test_bad_mmsis_dropped(self):
        self.vessels("v", ["367297000", "12345", "36729700x", 367262000])
        self.assertEqual(self.c._consumers["v"].mmsis, frozenset({"367297000", "367262000"}))

    def test_shared_is_one_per_store(self):
        store = {}
        self.assertIs(A.shared(store), A.shared(store))
        self.assertIn(A.STORE_KEY, store)


class TestDispatch(Base):
    async def test_area_by_box_and_relabelled(self):
        self.area("a", CHICAGO, domain="live_track_ships")
        self.area("b", FLORIDA, domain="other")
        await settle()
        ws = self.s.sockets[0]
        await ws.q.put(position("367000001", 42.0, -87.5))      # Chicago
        await ws.q.put(position("367000002", 25.0, -80.0))      # Florida
        await ws.q.put(position("367000003", 10.0, 10.0))       # neither
        await settle()
        self.assertEqual([o.identity for o in self.got["a"]], ["367000001"])
        self.assertEqual([o.identity for o in self.got["b"]], ["367000002"])
        self.assertEqual(self.got["a"][0].source, "live_track_ships")
        self.assertEqual(self.got["b"][0].source, "other")
        o = self.got["a"][0]
        self.assertEqual(o.name, "TEST BOAT")
        self.assertIsNone(o.heading_deg)                       # 511 is unavailable
        self.assertIsNone(o.rate_of_turn_dps)                  # -128 is unavailable
        self.assertAlmostEqual(o.speed_kmh, 5.0 * 1.852)
        self.assertEqual(o.observed_at.microsecond, 123456)

    async def test_vessels_by_list_and_box(self):
        self.vessels("salt", ["367297000"], boxes=FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        await ws.q.put(position("367297000", 25.0, -80.0))      # listed, inside
        await ws.q.put(position("367297000", 30.3, -87.3))      # listed, outside the box
        await ws.q.put(position("367000009", 25.0, -80.0))      # inside, not listed
        await settle()
        self.assertEqual(len(self.got["salt"]), 1)
        self.assertEqual(self.got["salt"][0].source, "live_track_salt")

    async def test_each_connection_feeds_only_its_own_kind(self):
        self.area("ships", FLORIDA)
        self.vessels("salt", ["367297000"], boxes=FLORIDA)
        await settle()
        area_ws = next(ws for ws in self.s.sockets if "FiltersShipMMSI" not in ws.sent[0])
        vess_ws = next(ws for ws in self.s.sockets if "FiltersShipMMSI" in ws.sent[0])
        await area_ws.q.put(position("367297000", 25.0, -80.0))
        await settle()
        self.assertEqual((len(self.got["ships"]), len(self.got["salt"])), (1, 0))
        await vess_ws.q.put(position("367297000", 25.0, -80.0))
        await settle()
        self.assertEqual((len(self.got["ships"]), len(self.got["salt"])), (1, 1))

    async def test_each_key_feeds_only_its_own_consumers(self):
        self.area("mine", FLORIDA)
        self.area("theirs", FLORIDA, key=OTHER_KEY)
        await settle()
        ws = next(w for w in self.s.sockets if w.sent[0]["APIKey"] == KEY)
        await ws.q.put(position("367000002", 25.0, -80.0))
        await settle()
        self.assertEqual((len(self.got["mine"]), len(self.got["theirs"])), (1, 0))

    async def test_static_feeds_the_shared_cache(self):
        self.area("a", FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        await ws.q.put(static5("367297000"))
        await ws.q.put(position("367297000", 25.0, -80.0, name="CG TEST"))
        await settle()
        self.assertIn("367297000", self.c.statics)
        self.assertEqual(self.got["a"][0].ship_type, 55)
        self.assertEqual(self.got["a"][0].length_m, 47.0)

    async def test_one_consumer_failing_does_not_stop_others(self):
        def boom(_obs):
            raise RuntimeError("consumer bug")
        self.c.add_area("bad", self.s, KEY, FLORIDA, boom, "x")
        self.area("good", FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        logging.disable(logging.CRITICAL)
        try:
            await ws.q.put(position("367000002", 25.0, -80.0))
            await ws.q.put(position("367000004", 25.1, -80.1))
            await settle()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(len(self.got["good"]), 2)

    async def test_refusal_logged_with_key_masked(self):
        self.area("a", FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        with self.assertLogs("saw_livetrack.aisstream", level="WARNING") as cm:
            await ws.q.put(frame({"error": f"Api Key {KEY} is not valid"}))
            await settle()
        self.assertTrue(any("refused" in line and "<api key>" in line and KEY not in line for line in cm.output))
        self.assertEqual(self.c._conns[(KEY, A.AREA)].status, "refused")

    async def test_confirmation_and_ignored_types(self):
        self.area("a", FLORIDA)
        await settle()
        ws = self.s.sockets[0]
        await ws.q.put(frame({"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}))
        await ws.q.put(frame({"MessageType": "AidsToNavigationReport", "MetaData": {"MMSI": 993660001}}))
        await ws.q.put(Msg(WSMsgType.TEXT, json.dumps({"x": 1})))
        await settle()
        conn = self.c._conns[(KEY, A.AREA)]
        self.assertTrue(conn.compression_enabled)
        self.assertEqual(conn.status, "subscribed")
        self.assertEqual(self.got["a"], [])

    async def test_reconnect_with_jitter_and_resubscribe(self):
        self.area("a", FLORIDA)
        await settle()
        first = self.s.sockets[0]
        logging.disable(logging.CRITICAL)
        try:
            await first.q.put(Msg(WSMsgType.CLOSED))
            await settle()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(len(self.s.sockets), 2)
        self.assertEqual(self.s.sockets[1].sent, [{"APIKey": KEY, "BoundingBoxes": FLORIDA}])
        wait = self.clock.slept[-1]
        self.assertTrue(A.BACKOFF_START_S * 0.5 <= wait <= A.BACKOFF_START_S * 1.5, wait)

    async def test_error_frame_reconnects(self):
        self.area("a", FLORIDA)
        await settle()
        logging.disable(logging.CRITICAL)
        try:
            await self.s.sockets[0].q.put(Msg(WSMsgType.ERROR, "missed pong"))
            await settle()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(len(self.s.sockets), 2)

    async def test_status_never_holds_the_key(self):
        self.area("a", FLORIDA)
        self.vessels("v", ["367297000"])
        await settle()
        self.assertNotIn(KEY, json.dumps(self.c.status()))
        self.assertEqual(sorted(c["kind"] for c in self.c.status()["connections"]), [A.AREA, A.VESSELS])


class TestClaims(Base):
    """0.2.20 (backlog 315 task 5): as on adsb_poll, each consumer says what it draws; another skips it."""

    async def test_claimed_by_others_excludes_your_own(self):
        self.c.claim("live_track_ships:a", ["367269000", "338926432"])
        self.c.claim("live_track_ships:b", ["366000001"])
        self.c.claim("salt", ["367269000"])
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset({"367269000", "338926432", "366000001"}))
        self.assertEqual(self.c.claimed_by_others("live_track_ships:a"), frozenset({"366000001", "367269000"}))
        self.assertEqual(self.c.claimed_by_others("nobody"),
                         frozenset({"367269000", "338926432", "366000001"}))

    async def test_a_claim_replaces_the_last(self):
        self.c.claim("ships", ["367269000"])
        self.c.claim("ships", ["338926432"])
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset({"338926432"}))
        self.c.claim("ships", [])
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset())

    async def test_claims_are_normalised(self):
        self.c.claim("ships", [367269000, " 338926432 ", "12345", "36726900x", None])
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset({"367269000", "338926432"}))

    async def test_release_and_remove_drop_claims(self):
        self.c.claim("ships", ["367269000"])
        self.c.release("ships")
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset())
        self.area("ships", FLORIDA)
        self.c.claim("ships", ["367269000"])
        self.c.claim("other", ["366000001"])
        await self.c.remove("ships")
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset({"366000001"}))
        await self.c.remove("other")                      # a claimer that never subscribed: its claims go too
        self.assertEqual(self.c.claimed_by_others("salt"), frozenset())

    async def test_claims_change_no_subscription(self):
        self.vessels("salt", ["367269000"])
        await settle()
        ws = self.s.sockets[0]
        self.c.claim("ships", ["367269000"])
        await settle()
        self.assertEqual(len(ws.sent), 1)
        await ws.q.put(position("367269000", 25.0, -80.0))
        await settle()
        self.assertEqual(len(self.got["salt"]), 1)       # delivery is unchanged: skipping is the consumer's choice

    async def test_status_counts_claims(self):
        self.c.claim("ships", ["367269000", "338926432"])
        self.c.claim("salt", [])
        self.assertEqual(self.c.status()["claims"], {"salt": 0, "ships": 2})


class TestEmptyList(Base):
    """0.2.20: a VESSELS consumer with no valid MMSI is refused: an empty FiltersShipMMSI would be no filter."""

    async def test_add_refused(self):
        for mmsis in ([], None, ["12345", "x"]):
            with self.assertRaises(ValueError):
                self.vessels("salt", mmsis)
            self.assertNotIn("salt", self.c._consumers)
        await settle()
        self.assertEqual(self.s.sockets, [])
        self.assertEqual(self.c.status()["connections"], [])

    async def test_update_to_empty_refused_and_list_kept(self):
        self.vessels("salt", ["367269000"])
        await settle()
        with self.assertRaises(ValueError):
            self.c.update("salt", mmsis=[])
        self.assertEqual(self.c._consumers["salt"].mmsis, frozenset({"367269000"}))
        self.c.update("salt", boxes=FLORIDA)              # no new list: the old one stands
        await settle()
        self.assertEqual(self.s.sockets[0].sent[-1]["FiltersShipMMSI"], ["367269000"])

    async def test_area_needs_no_list(self):
        self.area("ships", FLORIDA)
        self.c.update("ships", boxes=CHICAGO)
        await settle()
        self.assertNotIn("FiltersShipMMSI", self.s.sockets[0].sent[-1])


if __name__ == "__main__":
    unittest.main()
