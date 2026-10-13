# lablab.ai submission copy

**Title:** FactoryPilot

**Short description (one line):**
A maintenance agent that turns a photo and a sensor log into a cited diagnosis and a dispatched work order, reasoning on an open VLM on AMD Instinct MI300X and acting through Evolus.

**Long description:**
When a production line starts making bad parts, the shift supervisor has to work out what is wrong from a photo, a sensor
dashboard and a long manual, by hand, while scrap piles up. FactoryPilot does that join-up in seconds. A technician
uploads one photo; the agent runs a golden-sample visual pre-screen, analyses the last hours of sensor data (drift,
spikes, ISO 10816 vibration zones and a forecast of hours until alarm), retrieves the relevant manual pages from an Evolus
knowledge library, and asks Qwen2.5-VL, served by vLLM on an AMD Instinct MI300X with ROCm, for a schema-constrained
diagnosis: root cause, severity, calibrated confidence, ordered actions with manual values, safety notes and citations.
Five deterministic guardrails then check confidence, severity, citations, evidence agreement and trip-level sensors.
Safe calls are dispatched to the right technician through an Evolus workflow. Risky ones are held for one-click supervisor
approval. A live floor view, a work-order queue and a multilingual copilot complete the product. Every model call reports
time to first token and tokens per second, so the AMD performance is visible in the app, not just claimed.

**Track:** Intelligent Industry (+ Evolus Business Agents on AMD)

**Technology tags:** AMD Instinct MI300X, ROCm, vLLM, AMD Developer Cloud, Qwen2.5-VL, Evolus, FastAPI, Python

**Category tags:** Manufacturing, Predictive maintenance, Computer vision, AI agents, Industrial AI

**Assets:** cover image `docs/cover.png` · screenshots `docs/screenshots/` · demo script `docs/DEMO_SCRIPT.md` · slide deck (Slides artifact, export as PDF or PPTX)

**Before submitting, fill in:** live URL (`infra/amd/deploy_app.sh` on the droplet), GitHub URL, benchmark numbers on the AMD slide, demo video link.
