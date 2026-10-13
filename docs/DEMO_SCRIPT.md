# Demo script (3 minutes, live or for the video)

**Before you start:** open the app, go to **System → Reset demo data** (this clears work orders and restarts the live
sensor clock), then go back to **Floor**. Make sure the top bar says `Model · AMD Instinct MI300X…` with a green dot. If
it says *Offline demo*, the app is running without the GPU, which is still a fine fallback.

| Time | Screen | Say | Do |
|---|---|---|---|
| 0:00 | Floor | "It's 11:40 on shift A. Supervisors find out about faults when the scrap bin fills up. FactoryPilot watches every machine. The screen stays grey unless something is abnormal." | Point at the four machine plates. |
| 0:15 | Floor, Conveyor C2 | "Conveyor C2 is in the warning zone. Vibration has been climbing since 10:24, and the forecast says about an hour to alarm. The operator also says parts are coming out scratched." | Click C2. Show the vibration chart crossing the dashed warning line, then the forecast box. |
| 0:35 | Inspect | "The technician takes one photo. Watch the agent work." | Click **Inspect Conveyor C2**, then **Run diagnosis**. |
| 0:40 | Agent trace | "Pre-screen finds a scratch. Sensor analytics confirm a bearing trend. It pulls pages 37 and 41 of the C2 manual. Then the vision-language model on our **AMD MI300X** reasons over the photo, the sensor data and the manual, streaming its answer." | Let the trace run. Point at the token stream and the `tok/s · TTFT` line. |
| 1:00 | Result | "Root cause: a worn guide-rail pad, with the bearing degrading in parallel. Actions come with real values from the manual, such as 8 Nm and 6 g of grease, plus the lock-out step. Every source is a real page. If the model makes up a citation, the guardrail removes it." | Scroll: overlay, actions, safety, sources. |
| 1:20 | Result → work order | "All five guardrails passed, so the work order went straight to Giulia through the **Evolus workflow**. No one typed anything." | Point at the work-order card. |
| 1:30 | Inspect, Press P1 | "Now a dangerous one. A crack on Press P1." | Select Press P1, pick the crack photo, **Run diagnosis**. |
| 1:45 | Result | "Critical. The severity gate fails on purpose, so the order is **held for a human**. The AI recommends, and the supervisor decides." | Show *Human approval required*. Click **Review and approve**. |
| 2:00 | Work orders | "The supervisor sees the evidence, approves with one click, and it's dispatched to Marco." | Type a short comment, click **Approve and dispatch**, and show the history line. |
| 2:15 | Copilot | "Our technicians speak Italian." | Choose context *Packaging Line L3* and click the Italian suggestion. The answer cites p.24 (22 Nm). |
| 2:35 | System | "Everything here is measured live on AMD: time to first token, tokens/s, and our vLLM benchmark at up to 64 concurrent users on one MI300X. On the Evolus side you can see each building block and every API call." | Show the metrics table and the benchmark table. |
| 2:50 | Close | "From hunting through manuals to a cited answer in under a minute, with the evidence attached and a human in charge of the risky calls. Open model, your own AMD GPU, your data stays yours." | |

## Backup plan

* **AMD endpoint down:** set `LLM_BACKEND=demo` and restart. The flow is identical, and the UI honestly says *Offline demo*.
* **Wi-Fi flaky:** everything (fonts included) is served locally. Only the model and Evolus need the network.
* **Need C2 in alarm right now:** set `SIM_SECONDS_PER_SAMPLE=0.5` and restart. C2 crosses into alarm within about a minute.
