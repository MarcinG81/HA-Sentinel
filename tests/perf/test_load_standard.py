"""B25: standard profile load test against the budgets of spec 21.1.

Run: pytest -m perf tests/perf -s
Writes a JSON report to $SENTINEL_PERF_REPORT (if set). Results from a developer machine
are not the reference hardware (4x ARM64, 4 GB, SSD) and must be labelled as such.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import time
import tracemalloc
import uuid

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN

pytestmark = pytest.mark.perf

ENTITIES = int(os.environ.get("SENTINEL_PERF_ENTITIES", 2000))
DEVICES, AUTOMATIONS = 300, 300
RATE, BURST = 100, 1000
SECONDS = int(os.environ.get("SENTINEL_PERF_SECONDS", 60))


def _automations() -> str:
    parts = []
    for i in range(AUTOMATIONS):
        a, b = f"sensor.load_{i % ENTITIES}", f"switch.load_{(i * 7) % ENTITIES}"
        parts.append(
            f"- id: a{i}\n  alias: Automation {i}\n  triggers:\n    - trigger: state\n"
            f"      entity_id: {a}\n      to: \"on\"\n  conditions:\n    - condition: state\n"
            f"      entity_id: {b}\n      state: \"off\"\n  actions:\n"
            f"    - action: switch.turn_on\n      target:\n        entity_id: {b}\n")
    return "".join(parts)


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


def _p95(values):
    values = sorted(values)
    return values[min(len(values) - 1, int(0.95 * (len(values) - 1)))]


async def test_standard_profile(hass, hass_ws_client):
    """No freezer here: freezegun also freezes perf_counter, which would fake every timing.
    The writer is drained explicitly instead of waiting for its timer."""
    rnd = random.Random(42)
    phase0 = time.perf_counter()
    hub = MockConfigEntry(domain="mqtt", title="Load hub")
    hub.add_to_hass(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    devices = [dev_reg.async_get_or_create(config_entry_id=hub.entry_id,
                                           identifiers={("mqtt", f"d{i}")}, name=f"Dev {i}")
               for i in range(DEVICES)]
    for i in range(ENTITIES):
        domain = "sensor" if i % 2 == 0 else "switch"
        ent_reg.async_get_or_create(domain, "mqtt", f"e{i}", suggested_object_id=f"load_{i}",
                                    device_id=devices[i % DEVICES].id, config_entry=hub)
    print(f"[phase] registries: {time.perf_counter() - phase0:.2f}s", flush=True)
    for i in range(ENTITIES):
        hass.states.async_set(f"sensor.load_{i}", "off")
        hass.states.async_set(f"switch.load_{i}", "off")

    track_memory = os.environ.get("SENTINEL_PERF_MEMORY") == "1"  # tracemalloc is slow
    if track_memory:
        tracemalloc.start()
        mem0 = tracemalloc.take_snapshot()
    phase = time.perf_counter()

    def mark(name: str) -> None:
        nonlocal phase
        print(f"[phase] {name}: {time.perf_counter() - phase:.2f}s", flush=True)
        phase = time.perf_counter()

    mark("fixture")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, "modules_autopilot": True})
    entry.add_to_hass(hass)
    t0 = time.perf_counter()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    await runtime.async_flush()
    start_s = time.perf_counter() - t0
    mark("setup")

    # Sustained 100 ev/s for 60 simulated seconds, ~10 % availability transitions.
    for _second in range(SECONDS):
        for _ in range(RATE):
            i = rnd.randrange(ENTITIES)
            value = "unavailable" if rnd.random() < 0.05 else rnd.choice(("on", "off"))
            hass.states.async_set(f"sensor.load_{i}", value)
        await hass.async_block_till_done()
        await runtime.async_flush()
    # Burst: 1000 events before the writer gets a chance to run.
    for _ in range(BURST):
        hass.states.async_set(f"switch.load_{rnd.randrange(ENTITIES)}",
                              rnd.choice(("on", "off", "unavailable")))
    burst_depth = runtime.queues.depth
    await runtime.async_flush()
    mark("load")

    client = await hass_ws_client(hass)
    yaml_text = _automations()
    imp = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=yaml_text,
                      content_hash="sha256:" + hashlib.sha256(yaml_text.encode()).hexdigest(),
                      resource_kind="automation", label="load.yaml",
                      request_key=str(uuid.uuid4()))
    assert imp["success"], imp
    mark("import")
    t1 = time.perf_counter()
    await runtime.config.async_rebuild_graph()
    graph_rebuild_s = time.perf_counter() - t1

    # The per-user API limit (60 queries/min) would otherwise reject the measurement loop.
    from custom_components.ha_sentinel.core.ratelimit import RateLimiter, RequestClass
    runtime.rate_limiter = RateLimiter(runtime.clock, {RequestClass.QUERY: 10_000,
                                                       RequestClass.MUTATION: 10_000})
    overview_ms, graph_ms = [], []
    node = next(n for n in runtime.config.graph.nodes.values() if n.type == "entity")
    for _ in range(40):
        t2 = time.perf_counter()
        assert (await _call(client, type=f"{DOMAIN}/overview"))["success"]
        overview_ms.append((time.perf_counter() - t2) * 1000)
        t2 = time.perf_counter()
        assert (await _call(client, type=f"{DOMAIN}/graph/query", uid=node.uid,
                            direction="dependents", depth=2))["success"]
        graph_ms.append((time.perf_counter() - t2) * 1000)
    t3 = time.perf_counter()
    lint = await _call(client, type=f"{DOMAIN}/autopilot/analyze",
                       snapshot_id=imp["result"]["snapshot_id"])
    lint_s = time.perf_counter() - t3
    assert lint["success"], lint

    sentinel_kib = None
    if track_memory:
        mem1 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        sentinel_kib = round(sum(s.size_diff for s in mem1.compare_to(mem0, "filename")
                                 if "ha_sentinel" in str(s.traceback)) / 1024, 1)
    metrics = runtime.metrics.snapshot()
    report = {
        "machine": {"system": platform.system(), "machine": platform.machine(),
                    "python": platform.python_version(), "reference_hardware": False},
        "profile": {"entities": ENTITIES * 2, "registered": ENTITIES, "devices": DEVICES,
                    "automations": AUTOMATIONS, "rate": RATE, "burst": BURST,
                    "seconds": SECONDS},
        "start_inventory_s": round(start_s, 3),
        "graph_rebuild_s": round(graph_rebuild_s, 3),
        "graph_nodes": len(runtime.config.graph.nodes),
        "graph_edges": runtime.config.graph.edge_count,
        "lint_300_automations_s": round(lint_s, 3),
        "overview_p95_ms": round(_p95(overview_ms), 2),
        "graph_neighbourhood_p95_ms": round(_p95(graph_ms), 2),
        "burst_queue_depth": burst_depth,
        "dropped": runtime.queues.stats.dropped_total,
        "received": runtime.queues.stats.received_total,
        "sentinel_python_heap_kib": sentinel_kib,
        "metrics": metrics,
    }
    if path := os.environ.get("SENTINEL_PERF_REPORT"):
        with open(path, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, indent=2))
    print(json.dumps(metrics["histograms"], indent=2))

    # Release gate budgets (spec 21.1), standard profile.
    assert metrics["histograms"]["ingestion_callback_us"]["p99"] <= 1000
    assert report["dropped"] == 0
    assert start_s <= 60
    assert report["overview_p95_ms"] <= 300
    assert report["graph_neighbourhood_p95_ms"] <= 500
    assert graph_rebuild_s <= 10
    assert lint_s <= 5
