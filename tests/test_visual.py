"""Presentation control safety; real network flows also run via --check."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from p2psim.visual import VisualDemo, create_app
from p2psim import visual


def test_local_dashboard_rejects_foreign_origins_hosts_and_bad_scenarios():
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["service"] == "visual-demo"
        assert client.get("/api/state").json()["status"] == "idle"
        assert client.post("/api/run", json={"scenario": 5}).status_code == 422
        assert client.post("/api/run", json={"scenario": 1},
                           headers={"Origin": "https://foreign.example"}).status_code == 403
        assert client.post("/api/run", json={"scenario": 1},
                           headers={"Host": "foreign.example"}).status_code == 400


@pytest.mark.asyncio
async def test_dashboard_rejects_overlapping_runs_and_closes_owned_stack(monkeypatch):
    demo = VisualDemo()
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def running(scenario):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(demo, "run", running)
    demo.start(1)
    await entered.wait()
    with pytest.raises(HTTPException) as error:
        demo.start(2)
    assert error.value.status_code == 409
    await demo.close()
    assert closed.is_set()
    assert demo.task.cancelled()


@pytest.mark.asyncio
async def test_dashboard_reports_failure_and_restores_injected_fault(monkeypatch):
    demo = VisualDemo()
    calls = []

    class Stack:
        client = True

        async def api(self, peer, method, path):
            calls.append((peer, path))

        async def close(self):
            calls.append(("stack", "close"))

    demo.stack = Stack()

    async def broken(scenario):
        demo.state["scenario"] = scenario
        raise RuntimeError("injected controller failure")

    async def refresh():
        pass

    monkeypatch.setattr(demo, "case", broken)
    monkeypatch.setattr(demo, "refresh", refresh)
    demo.start(4)
    await demo.task
    assert demo.state["status"] == "failed"
    assert demo.state["results"][0]["passed"] is False
    assert "injected controller failure" in demo.state["error"]
    assert ("peer-a", "/api/debug/serving/true") in calls
    assert ("peer-a", "/api/debug/upload-delay/60") in calls
    await demo.close()
    assert ("stack", "close") in calls


@pytest.mark.asyncio
async def test_start_failure_closes_partial_stack(monkeypatch, tmp_path):
    closed = []

    class PartialStack:
        def __init__(self, runtime):
            self.runtime = runtime

        async def start(self):
            raise RuntimeError("peer startup failed")

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(visual, "DemoStack", PartialStack)
    demo = VisualDemo(tmp_path)
    with pytest.raises(RuntimeError, match="peer startup failed"):
        await demo.case(1)
    assert closed == [True]
