import dataclasses
import io
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from app import agent, config, knowledge, llm, sensors, vision, workorders
from app.main import app
from app.simulate import make_log

client = TestClient(app)
SAMPLES = config.settings.data_dir / "samples"


def _png(color="gray", size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _csv(scenario="bearing", rows=300) -> bytes:
    return make_log("Conveyor C2", scenario, rows).to_csv(index=False).encode()


@pytest.fixture(autouse=True)
def _clean():
    workorders.reset()
    yield


# ---------------------------------------------------------------- analytics units

def test_sensor_analysis_detects_bearing_failure():
    a = sensors.analyze(make_log("Conveyor C2", "bearing", 480))
    assert a["status"] in {"alarm", "trip"}
    assert a["health"] < 50
    assert a["forecast"]["hours_to_alarm"] == 0
    assert any("Vibration" in f["text"] for f in a["findings"])


def test_sensor_analysis_normal_is_quiet():
    a = sensors.analyze(make_log("CNC M4", "normal", 480))
    assert a["status"] == "ok" and a["health"] >= 90 and not a["findings"]


def test_sensor_analysis_rattle_is_flagged():
    a = sensors.analyze(make_log("Packaging Line L3", "rattle", 480))
    assert a["status"] == "warn"
    assert any("spikes" in f["text"] for f in a["findings"])


@pytest.mark.parametrize("name,kind", [
    ("conveyor_c2_scratch.png", "scratch"), ("press_p1_crack.png", "crack"),
    ("cnc_m4_corrosion.png", "corrosion"), ("packaging_l3_missing_bolt.png", "missing_component"),
    ("packaging_l3_dent.png", "dent"),
])
def test_vision_prescreen_classifies_samples(name, kind):
    r = vision.inspect((SAMPLES / name).read_bytes())
    assert r["defect_suspected"]
    assert r["regions"][0]["type"] == kind
    assert r["overlay_png"].startswith("data:image/jpeg;base64,")


def test_vision_clean_part_and_arbitrary_photo():
    assert not vision.inspect((SAMPLES / "part_ok.png").read_bytes())["defect_suspected"]
    r = vision.inspect(_png(size=(300, 200)))  # no golden sample of this size -> local anomaly mode
    assert r["method"] == "local anomaly map" and not r["defect_suspected"]


def test_manual_retrieval_prefers_machine_manual():
    hits = knowledge.search_local("bearing vibration re-grease", "Conveyor C2")
    assert hits[0]["source"] == "Conveyor_C2_Manual.pdf p.41"


# ---------------------------------------------------------------- API (offline mode)

def test_health_and_status():
    assert client.get("/health").json()["mock"] is True
    s = client.get("/api/status").json()
    assert s["llm"]["amd"] is False and "blocks" in s["evolus"]


def test_fleet_and_log_download():
    fleet = client.get("/api/fleet").json()
    assert {m["id"] for m in fleet} == {"conveyor-c2", "press-p1", "cnc-m4", "packaging-l3"}
    r = client.get("/api/machines/conveyor-c2/log.csv")
    assert r.status_code == 200 and b"vibration_rms_mm_s" in r.content


def test_diagnose_end_to_end_legacy_endpoint():
    r = client.post("/diagnose", data={"machine": "Conveyor C2"},
                    files={"image": ("p.png", (SAMPLES / "conveyor_c2_scratch.png").read_bytes(), "image/png"),
                           "logs": ("l.csv", _csv(), "text/csv")})
    assert r.status_code == 200
    body = r.json()
    d = body["diagnosis"]
    assert d["defect_detected"] is True and d["defect_type"] == "scratch"
    assert body["work_order"]["id"].startswith("WO-")
    assert "vibration" in body["log_summary"].lower()
    assert all(c in {h["source"] for h in body["manual_hits"]} for c in d["manual_citations"])


def test_stream_emits_trace_and_result():
    with client.stream("POST", "/api/diagnose/stream",
                       data={"machine": "Press P1", "sample": "press_p1_crack.png", "live_logs": "true"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "result"
    steps = {e["id"] for e in events if e["type"] == "step"}
    assert steps == {"vision", "sensors", "manuals", "reason", "guard", "act"}
    res = events[-1]
    assert res["diagnosis"]["severity"] == "critical"
    assert res["diagnosis"]["needs_human"] is True
    assert res["work_order"]["status"] == "awaiting_approval"


def test_clean_part_creates_no_order():
    r = client.post("/api/diagnose", data={"machine": "CNC M4", "sample": "part_ok.png"},
                    files={"logs": ("l.csv", make_log("CNC M4", "normal", 300).to_csv(index=False).encode(), "text/csv")})
    assert r.json()["diagnosis"]["defect_detected"] is False
    assert r.json()["work_order"] is None


def test_sensor_only_diagnosis_without_photo():
    r = client.post("/api/diagnose", data={"machine": "Conveyor C2"},
                    files={"logs": ("l.csv", _csv("bearing", 480), "text/csv")})
    d = r.json()["diagnosis"]
    assert d["defect_type"] == "bearing_wear" and d["severity"] in {"high", "critical"}


def test_requires_some_input():
    assert client.post("/api/diagnose", data={"machine": "Conveyor C2"}).status_code == 400


def test_approval_workflow():
    res = agent.diagnose((SAMPLES / "press_p1_crack.png").read_bytes(), "image/png", None, "Press P1")
    oid = res["work_order"]["id"]
    assert client.post(f"/api/workorders/{oid}/complete").status_code == 409  # cannot close before approval
    o = client.post(f"/api/workorders/{oid}/approve", json={"by": "Test supervisor", "comment": "die swapped"}).json()
    assert o["status"] == "dispatched" and "approved by Test supervisor" in o["history"][-1]["event"]
    assert client.post(f"/api/workorders/{oid}/complete").json()["status"] == "closed"
    assert len(client.get("/api/workorders").json()) == 1


def test_low_confidence_forces_human(monkeypatch):
    def fake(*a, **k):
        return {"defect_detected": True, "defect_type": "scratch", "confidence": 0.3, "severity": "low",
                "summary": "", "likely_root_cause": "", "evidence": [], "recommended_actions": [],
                "manual_citations": [], "needs_human": False}
    monkeypatch.setattr(agent.reasoner, "offline_diagnosis", fake)
    out = agent.diagnose(_png(), "image/png", None, "Press P1")
    assert out["diagnosis"]["needs_human"] is True
    assert "confidence" in out["diagnosis"]["human_reason"]


def test_copilot_offline_stream():
    with client.stream("POST", "/api/copilot", json={"question": "How do I replace the bearing?",
                                                     "machine_id": "conveyor-c2"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Conveyor_C2_Manual.pdf p.41" in text
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------- AMD code path against a fake vLLM

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def fake_vllm(monkeypatch):
    from tests import fake_vllm as fv
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(fv.app, port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    new = dataclasses.replace(config.settings, vllm_base_url=f"http://127.0.0.1:{port}/v1", vllm_api_key="k",
                              mock=False, mode="auto", evolus_api_key="")
    for mod in (config, agent, llm, workorders, knowledge):
        monkeypatch.setattr(mod, "settings", new, raising=False)
    from app import copilot
    monkeypatch.setattr(copilot, "settings", new)
    llm._clients.clear()
    yield fv
    llm._clients.clear()
    server.should_exit = True
    th.join(timeout=5)


def test_amd_path_streams_tokens_and_verifies_citations(fake_vllm):
    events = list(agent.run((SAMPLES / "conveyor_c2_scratch.png").read_bytes(), "image/png", _csv(),
                            "Conveyor C2", language="Italiano", pace=0))
    assert any(e["type"] == "token" for e in events)
    res = events[-1]
    d = res["diagnosis"]
    assert d["severity"] == "high" and d["confidence"] == 0.84
    # the invented citation is stripped by the guardrail
    assert d["manual_citations"] == ["Conveyor_C2_Manual.pdf p.37"]
    assert res["telemetry"]["tokens_per_s"] > 0
    req = fake_vllm.LAST_REQUEST
    assert req["response_format"]["type"] == "json_schema"
    assert req["messages"][1]["content"][0]["type"] == "image_url"
    assert "Italiano" in req["messages"][0]["content"]


def test_amd_copilot_streams(fake_vllm):
    from app import copilot
    events = list(copilot.answer("why vibration?", "conveyor-c2"))
    assert "bearing" in "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert events[-1]["telemetry"]["tokens_per_s"] > 0
