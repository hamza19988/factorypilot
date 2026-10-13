# Connecting Evolus (about 15 minutes)

FactoryPilot drives Evolus entirely through its APIs and uses three building blocks:

| Building block | Used for | `.env` |
|---|---|---|
| Knowledge library | Searching the equipment manuals, with page-level sources | `EVOLUS_MANUALS_LIBRARY_ID` |
| Workflow | Receiving the work order, then routing and notifying the technician | `EVOLUS_WORKORDER_WORKFLOW_ID` |
| Agent via the OpenAI-compatible chat endpoint | The supervisor copilot, backed by our AMD vLLM endpoint | `EVOLUS_CHAT_MODEL` |

## Steps

1. **Workspace.** Sign up at app.evolus.ai with the event code. Copy the **API key** and **Application ID** into
   `EVOLUS_API_KEY` and `EVOLUS_APPLICATION_ID`. Requests are sent with the `X-ApiKey` and `X-Application-Id` headers.
2. **Model.** In the workspace model settings, add a custom OpenAI-compatible model pointing at
   `http://<droplet-ip>:8000/v1`, with model name `factorypilot-vl` and the API key from `VLLM_API_KEY`. This is the
   *AMD-hosted model integration* the Evolus judges look for.
3. **Knowledge library.** Create a library called *Equipment manuals* and upload the four files in `data/manuals/`.
   Each `## p.NN — Title` section is one page, which keeps citations precise. Put the library ID in
   `EVOLUS_MANUALS_LIBRARY_ID`.
4. **Workflow.** Create a workflow called *Maintenance work order intake*, with an input object containing these fields:
   `id, machine, priority, title, summary, root_cause, actions[], citations[], assigned_to, escalate`.
   Suggested steps: create a record or ticket, then notify the technician by email or chat, then (if `priority = critical`)
   notify the maintenance manager. Put the workflow ID in `EVOLUS_WORKORDER_WORKFLOW_ID`.
5. **Agent (optional, recommended).** Create an agent that uses the AMD model from step 2 and attaches the knowledge
   library. Put its model or agent ID in `EVOLUS_CHAT_MODEL`. The copilot then answers through Evolus.
6. **Routes.** Open the API reference inside the workspace and confirm the two route templates:
   `EVOLUS_PATH_SEARCH_LIBRARY` (default `/knowledge-libraries/{library_id}/search`) and
   `EVOLUS_PATH_RUN_WORKFLOW` (default `/workflows/{workflow_id}/run`). The client accepts the common response shapes
   (`results`, `items`, `data`, `chunks`; `runId`, `executionId`, `id`).
7. **Verify.** Run `python scripts/check_evolus.py`. Each block should print `[ok]`. In the app, the top bar shows
   `Evolus · 3/3 blocks live`, and **System** lists every call with its status and latency.

If any Evolus call fails during a demo, the agent keeps working. It falls back to the local index or queue, and the
trace shows which channel was used.
