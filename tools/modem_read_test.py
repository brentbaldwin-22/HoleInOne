#!/usr/bin/env python3
"""Does the Pi's modem reading work on firmware we have not seen?

The runbook is explicit that the LM1200's JSON shape is NOT confirmed
on our unit -- "NETGEAR's LB/LM series serve that JSON, but confirm
rather than assume". So the reader cannot be written against a fixed
path: it scavenges any leaf key it recognises from anywhere in the
document. That is the behaviour under test, because a reader that
works only against the shape I imagined is a reader that reports "no
signal" from a modem answering perfectly well -- and nobody can check
it without the SSH session this whole feature exists to avoid.

The other half is the silence guarantee. A rig with no cellular modem
must report NOTHING, not "modem unreachable", or every Pi on Ethernet
grows a permanent false warning and the card stops being read.

    python3 tools/modem_read_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))

from agent import common  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if isinstance(self._payload, Exception):
            raise ValueError("not json")
        return self._payload


def with_modem(payload, status=200, gateway="192.168.5.1"):
    """Run read_modem against a stubbed modem, with a cold cache."""
    common._modem_cache.update({"at": 0.0, "value": None})
    real_get, real_gw = common.requests.get, common._modem_gateway
    common.requests.get = lambda url, timeout=None: FakeResp(payload, status)
    common._modem_gateway = lambda timeout=3.0: gateway
    try:
        return common.read_modem()
    finally:
        common.requests.get, common._modem_gateway = real_get, real_gw


def main() -> int:  # noqa: C901
    # ---- THE SHAPE WE EXPECT ----------------------------------------
    nested = {"wwan": {"signalStrength": {"rsrp": -78, "rsrq": -9,
                                          "sinr": 22, "bars": 4},
                       "currentPSserviceType": "LTE",
                       "band": "B4"},
              "wwanadv": {"rssi": -60},
              "custom": {"dataUsage": {"dataTransferred": 1288490188}}}
    m = with_modem(nested)
    check("rsrp is found three levels down", m.get("rsrp"), -78)
    check("so is sinr", m.get("sinr"), 22)
    check("and rssi, from a different subtree entirely", m.get("rssi"), -60)
    check("and the band", m.get("band"), "B4")
    check("and the usage figure", m.get("usage"), 1288490188)
    check("a reading that found fields is marked ok", m.get("ok"), True)

    # ---- FIRMWARE THAT NESTS IT SOMEWHERE ELSE ----------------------
    # The actual point of scavenging. A fixed-path reader returns
    # nothing here, and nothing is indistinguishable from a dead modem.
    elsewhere = {"status": [{"radio": {"lte": {"RSRP": "-104 dBm",
                                               "SNR": "3.5"}}}]}
    m = with_modem(elsewhere)
    check("a different nesting still yields rsrp", m.get("rsrp"), "-104 dBm")
    check("and SNR is accepted as SINR, which some firmware calls it",
          m.get("sinr"), "3.5")
    check("found through a list, not just dicts", m.get("ok"), True)

    # THE CONTROL. Scavenging must not match everything in sight, or
    # the tests above pass on a function that grabs the first leaf.
    m = with_modem({"device": {"name": "LM1200", "uptime": 90210}})
    check("a document with no signal fields finds none", m.get("ok"), False)
    check("and says so rather than inventing a reading",
          m.get("rsrp"), None)
    check("and the error names what was wrong",
          "no known signal fields" in m.get("error", ""), True)

    # ---- A MODEM THAT ANSWERS WITH SOMETHING ELSE -------------------
    m = with_modem({}, status=404)
    check("a 404 is reported as an unreadable modem, not a dead one",
          m.get("ok"), False)
    check("and carries the status code", "404" in m.get("error", ""), True)
    check("and the gateway, so it can be read on site",
          m.get("gateway"), "192.168.5.1")

    m = with_modem(ValueError("html"))
    check("an HTML page where JSON was expected is handled",
          "not JSON" in m.get("error", ""), True)

    # ---- THE SILENCE GUARANTEE --------------------------------------
    # No route to a modem: report NOTHING. A rig on Ethernet must not
    # grow a permanent "modem unreachable" warning.
    common._modem_cache.update({"at": 0.0, "value": None})
    real_get, real_gw = common.requests.get, common._modem_gateway

    def boom(url, timeout=None):
        raise OSError("no route to host")

    common.requests.get = boom
    common._modem_gateway = lambda timeout=3.0: None
    try:
        check("a rig with no modem reports nothing at all",
              common.read_modem(), None)
    finally:
        common.requests.get, common._modem_gateway = real_get, real_gw

    # ...but a modem we DID find and cannot reach is news. This is the
    # control that stops the above being "always returns None".
    common._modem_cache.update({"at": 0.0, "value": None})
    real_get, real_gw = common.requests.get, common._modem_gateway
    common.requests.get = boom
    common._modem_gateway = lambda timeout=3.0: "192.168.5.1"
    try:
        m = common.read_modem()
        check("a modem that IS routed but unreachable is reported",
              m is not None and m.get("ok") is False, True)
        check("with the reason", "OSError" in m.get("error", ""), True)
    finally:
        common.requests.get, common._modem_gateway = real_get, real_gw

    # ---- THE CACHE --------------------------------------------------
    # The heartbeat is every ~60s and the modem can be wedged. Fetching
    # on every beat is how a diagnostic becomes the outage.
    calls = []
    common._modem_cache.update({"at": 0.0, "value": None})
    real_get, real_gw = common.requests.get, common._modem_gateway

    def counted(url, timeout=None):
        calls.append(url)
        return FakeResp(nested)

    common.requests.get = counted
    common._modem_gateway = lambda timeout=3.0: "192.168.5.1"
    try:
        common.read_modem()
        common.read_modem()
        common.read_modem()
        check("three heartbeats cost one fetch", len(calls), 1)
        # THE CONTROL: it is a cache, not a one-shot.
        common._modem_cache["at"] = 0.0
        common.read_modem()
        check("and it refetches once the reading is stale", len(calls), 2)
    finally:
        common.requests.get, common._modem_gateway = real_get, real_gw

    # ---- A DOCUMENT THAT WOULD HANG A HEARTBEAT ---------------------
    # Parsed from a device we do not control. Depth and node budget are
    # what keep a strange or hostile page from stalling the beat.
    deep = cur = {}
    for _ in range(400):
        cur["next"] = {}
        cur = cur["next"]
    cur["rsrp"] = -80
    out: dict = {}
    t0 = time.monotonic()
    common._scavenge(deep, out)
    check("a 400-deep document is not mined past the depth limit",
          out.get("rsrp"), None)
    check("and returns promptly rather than walking it all",
          time.monotonic() - t0 < 1.0, True)

    wide = {"rows": [{"k": i} for i in range(10000)] + [{"rsrp": -80}]}
    out = {}
    t0 = time.monotonic()
    common._scavenge(wide, out)
    check("a 10k-element list is cut off by the node budget",
          out.get("rsrp"), None)
    check("and that too returns promptly",
          time.monotonic() - t0 < 1.0, True)

    # ---- THE GATEWAY COMES OFF THE ROUTING TABLE --------------------
    def fake_route(text):
        real = common.subprocess.run

        class R:
            stdout, returncode = text, 0

        common.subprocess.run = lambda *a, **k: R()
        try:
            return common._modem_gateway()
        finally:
            common.subprocess.run = real

    check("the usb default route is the modem",
          fake_route("default via 192.168.5.1 dev usb0 proto dhcp\n"
                     "192.168.5.0/24 dev usb0 scope link\n"),
          "192.168.5.1")
    check("so is a wwan one",
          fake_route("default via 10.0.0.1 dev wwan0\n"), "10.0.0.1")
    # THE CONTROL. A Pi on a desk behind an office router must not have
    # that router interrogated for cellular signal and reported as the
    # tee's uplink.
    check("a wifi default route is NOT a modem",
          fake_route("default via 192.168.1.1 dev wlan0\n"), None)
    check("and neither is onboard ethernet",
          fake_route("default via 192.168.1.1 dev eth0\n"), None)
    check("no default route at all is no modem",
          fake_route("172.17.0.0/16 dev docker0 scope link\n"), None)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
