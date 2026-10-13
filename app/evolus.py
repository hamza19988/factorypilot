"""Evolus REST client (https://api.evolus.ai).

Building blocks FactoryPilot drives through the API:
  1. Knowledge library -> search equipment manuals (with sources)
  2. Workflow          -> create + route the maintenance work order
  3. Chat (OpenAI-compatible endpoint) -> the shift-supervisor copilot, served by the
     Evolus agent that is wired to our own vLLM endpoint on AMD Developer Cloud

Auth uses the `X-ApiKey` and `X-Application-Id` headers. Route paths are configurable in
.env because they come from the API reference inside the event workspace.
Every call is recorded in `evolus.calls` so the UI can show exactly what was sent where.
"""
from __future__ import annotations

import time
from collections import deque

import httpx

from .config import settings


class EvolusClient:
    def __init__(self) -> None:
        self.calls: deque[dict] = deque(maxlen=50)

    @property
    def headers(self) -> dict:
        h = {"X-ApiKey": settings.evolus_api_key, "Content-Type": "application/json"}
        if settings.evolus_app_id:
            h["X-Application-Id"] = settings.evolus_app_id
        return h

    def _post(self, path: str, payload: dict, block: str) -> dict:
        url = settings.evolus_base_url.rstrip("/") + path
        t0 = time.perf_counter()
        status = None
        try:
            r = httpx.post(url, json=payload, headers=self.headers, timeout=60)
            status = r.status_code
            r.raise_for_status()
            return r.json()
        finally:
            self.calls.appendleft({"block": block, "method": "POST", "path": path, "status": status,
                                   "ms": round((time.perf_counter() - t0) * 1000), "ts": time.time()})

    def search_manuals(self, query: str, top_k: int = 4) -> list[dict]:
        path = settings.evolus_path_search_library.format(library_id=settings.evolus_library_id)
        data = self._post(path, {"query": query, "top_k": top_k, "topK": top_k}, "knowledge library")
        items = data if isinstance(data, list) else (
            data.get("results") or data.get("items") or data.get("data") or data.get("chunks") or [])
        hits = []
        for it in items[:top_k]:
            meta = it.get("metadata", {}) if isinstance(it, dict) else {}
            src = it.get("source") or meta.get("source") or it.get("documentName") or it.get("title") or "Evolus doc"
            page = it.get("page") or meta.get("page")
            hits.append({
                "source": f"{src} p.{page}" if page and "p." not in str(src) else str(src),
                "title": it.get("title") or meta.get("title") or "",
                "text": it.get("text") or it.get("content") or it.get("chunk") or "",
                "score": it.get("score", 0), "provider": "Evolus knowledge library",
            })
        return hits

    def create_work_order(self, order: dict) -> dict:
        path = settings.evolus_path_run_workflow.format(workflow_id=settings.evolus_workflow_id)
        data = self._post(path, {"input": order, "inputs": order}, "workflow")
        ref = (data.get("runId") or data.get("run_id") or data.get("executionId") or data.get("id")
               or data.get("work_order_id") or "")
        return {"evolus_ref": str(ref), "raw_status": data.get("status", "submitted")}

    def status(self) -> dict:
        return {
            "live": settings.evolus_live,
            "blocks": {
                "knowledge_library": settings.evolus_library_live,
                "workflow": settings.evolus_workflow_live,
                "chat_agent": settings.evolus_chat_live,
            },
            "recent_calls": list(self.calls)[:10],
        }


evolus = EvolusClient()
