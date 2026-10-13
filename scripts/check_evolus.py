"""Verify the Evolus configuration in .env: auth, knowledge-library search, workflow run, chat.

Usage: python scripts/check_evolus.py [--no-workflow]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.evolus import evolus  # noqa: E402
from app.llm import client  # noqa: E402

if not settings.evolus_live:
    sys.exit("EVOLUS_API_KEY is not set in .env")

ok = True
print("Base:", settings.evolus_base_url)
if settings.evolus_library_live:
    try:
        hits = evolus.search_manuals("Conveyor C2 bearing vibration", top_k=3)
        print(f"[ok] knowledge library: {len(hits)} hits ->", [h["source"] for h in hits])
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[fail] knowledge library:", e)
else:
    print("[skip] EVOLUS_MANUALS_LIBRARY_ID not set")

if settings.evolus_workflow_live and "--no-workflow" not in sys.argv:
    try:
        res = evolus.create_work_order({"id": "WO-TEST-0000", "machine": "Conveyor C2", "priority": "low",
                                        "title": "FactoryPilot connectivity test", "summary": "test",
                                        "root_cause": "", "actions": [], "citations": [],
                                        "assigned_to": "test", "escalate": False})
        print("[ok] workflow run:", res)
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[fail] workflow:", e)
else:
    print("[skip] workflow")

if settings.evolus_chat_live:
    try:
        r = client("evolus").chat.completions.create(model=settings.evolus_chat_model, max_tokens=20,
                                                     messages=[{"role": "user", "content": "Reply with: Evolus OK"}])
        print("[ok] chat agent:", r.choices[0].message.content)
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[fail] chat agent:", e)
else:
    print("[skip] EVOLUS_CHAT_MODEL not set")

for c in evolus.calls:
    print("   ", c)
sys.exit(0 if ok else 1)
