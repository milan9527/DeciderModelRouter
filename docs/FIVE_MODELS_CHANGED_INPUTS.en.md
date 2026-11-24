> Historical record: this version changed payment facts, added feedback, and automatically executed routing targets. It is superseded. These results are not a comparison on the original modules’ inputs. See [the restored original workflow](FIVE_MODELS.en.md).

# Five-model business workflow comparison

[Open the module](https://d3i7ogfx5ml7dc.cloudfront.net/#comparison) · [中文](FIVE_MODELS.md)

Compare **Strands Decider, Liquid d1-2B, H2O-Lightning 4B, Jev and GPT 6.1 Sol low effort** in five columns. The primary measures are decision latency and whether actual execution achieves the business task.

The three local models remain resident on one **g6.xlarge / L4**. Providers run serially at single concurrency, without loading or unloading weights between calls. Each starts with the same task and initial facts, then continues with its own actual execution state.

## Scenarios and operation

| Group | Business cases | Steps | Final acceptance |
| --- | --- | --- | --- |
| Payment support | USDC credit, payment top-up, refund, merchant settlement: 4 cases | Intake → verify → approve → process funds → close ticket | Simulated funds processed and ticket resolved |
| Model routing | Customer support, code, settlement analysis, bilingual indexing: 4 cases | Choose workflow → choose model → execute target | Workflow matches task type and target returns a complete result |
| Device control | Turn on AC; reduce temperature while preserving other settings: 2 cases | Power → temperature → mode | All three actual device fields match the request |
| Temperature presets | Replace saved temperature range: 1 case | Choose replace/add/clear → select and save values → read back and confirm | Exactly 22, 23, 24 and 25°C saved, and model confirms completion |

There are **11 workflows with 3–5 steps each**. Select a case and run the comparison. The five summary columns show cumulative decision time, tool time, provider workflow time, actual goal checks and final output. Click a step to compare exact inputs, candidates, responses and execution records. Task editing, reversed candidate order and full JSON export are available.

Payments use simulated orders, ledgers and tickets; devices use simulated state. No real funds or devices are affected. Initial payment facts satisfy the execution requirements, so models must actually process funds and close the ticket. Execution guards preserve funds and closure requirements; both the requested and actual action remain visible. The application does not substitute answers, repair incorrect temperatures or retry until a task succeeds.

Routing uses the original two model decisions: choose the workflow, then choose a model using public benchmark evidence, then actually call that model. It does not automatically pick the top-ranked model or replace a failed target. **Basic routing acceptance is not a complete evaluation of output quality or proof of optimal model selection.** Inspect the displayed target response.

Temperature sets use the same 15 binary questions (16–30°C) for every provider, rather than a provider-specific multi-select protocol. A wrong “add” operation really retains unwanted old values, which final acceptance detects. Editing the task disables sample reference/goal scoring; actual execution remains available for manual review.

## Deployment and timing

| Provider | Runtime |
| --- | --- |
| Strands Decider v19 | vLLM 0.31.0, BF16; existing `:8100/v1/systemone` |
| Liquid d1-2B | Official Transformers `system_one`, BF16 |
| H2O-Lightning 4B v1.4.1 | Official shim and patched vLLM 0.30.0, online FP8 |
| Jev | System One, `jev-latest`, existing IAM Lambda bridge |
| GPT 6.1 Sol | Bedrock Converse, `us.openai.gpt-6.1-sol`, low effort |

All three local models run on the existing On-Demand `i-0ae7eb0abcb815af3` in `us-east-1`: 4 vCPUs, 16 GiB RAM and one L4. Twelve initial GPU smoke calls succeeded; afterward GPU usage was **19,147 / 23,034 MiB**, with approximately 4.9 GiB host RAM available and no swap. No instance was added. Vision and original/SFT services remain on another existing GPU.

Liquid/H2O and Decider have isolated dependency environments. H2O's FP8 deployment differs from the earlier exclusive-GPU BF16 tests, whose speed and quality results must not be reused here. See [pinned provenance and hashes](../validation/open_decision_models/models.json) and [operations](../deployment/open_models/README.md). No new fine-tuning was performed.

- **Step decision time:** ECS backend inference request to complete response; includes private/cloud service round trips and Jev's Lambda bridge.
- **Cumulative decision time:** exact sum of step `timings.processing_ms`. The temperature set contains 15 questions in one request, so it is not the same load as a single-question call.
- **Tool time:** simulated state updates or the actual Bedrock target invocation, recorded separately.
- **Provider workflow time:** starts when that provider's turn begins and ends when it finishes, excluding preceding providers.
- **Whole-run wait:** wall-clock time for all five providers to run serially.

This is a single-concurrency behavior and latency demonstration, not a throughput benchmark. Decider now admits 32 HTTP requests with one active question and one engine sequence; Liquid/H2O each permit one active inference. Other demo modules or public vLLM clients may affect shared GPU latency. Provider-reported timings and usage are retained separately from backend timing.

## Evidence and API

Providers receive `state` and `questions`; references stay outside model requests. Different choices can produce different subsequent state, so later inputs need not be byte-identical. Every step retains its exact input and fingerprint. Reference matches, executed-step count and actual business goals are separate measures.

GPT uses a prompt-only JSON output contract. Invalid JSON, timeouts and target errors remain failures, without silent repair. A failed prerequisite stops that provider's dependent steps while other providers continue.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/comparison/catalog` | `workflow-v2` cases, initial facts, steps and providers |
| `POST /api/comparison/runs` | Start with `case_id`, optional `task`, and `variant`; return run ID immediately |
| `GET /api/comparison/runs/{id}` | Retrieve per-provider progress, execution, results and timing |

Existing login, CSRF protection and account isolation apply. The single API process permits one active comparison globally (409 when busy), retains at most 20 records for 30 minutes, and loses in-memory records on restart. Refresh resumes polling without resubmitting requests. Private S3, CloudFront OAC and ECS remain in place.

[Workflow/state acceptance](../app/comparison_workflows.py) · [Serial orchestration](../app/comparison.py) · [UI](../frontend/static/comparison.js) · [Resident gateway](../deployment/open_models/gateway.py). The original 89 independent snapshots remain in `app/comparison_cases.json` and the [seven-model benchmark](../validation/open_decision_models/README.md); they are no longer the active workflow dropdown.

## Production observations (2026-10-29)

The production browser ran 11 cases × 5 providers: **185 decision calls, all successful**, plus 20 selected Bedrock target invocations. Each case/provider ran once with original candidate order. Cumulative decision times exclude target generation; workflow completion includes downstream outcomes and is not decision-model accuracy.

| Provider | Decision P50 / mean | Reference matches | Workflows completed |
| --- | --- | --- | --- |
| Strands Decider | 51.3 / 75.2 ms | 44/47 | 8/11 |
| Liquid d1-2B | 49.3 / 56.4 ms | 42/47 | 7/11 |
| H2O-Lightning 4B FP8 | 85.2 / 139.1 ms | 43/47 | 7/11 |
| Jev | 316.0 / 381.0 ms | 44/47 | 6/11 |
| GPT 6.1 Sol low | 956.5 / 1035.8 ms | 47/47 | 10/11 |

Each provider made 37 requests, with input lengths and question counts varying by step. This is a scenario sample, not a fixed-load stress test or general ranking. The 47 scored questions include 15 temperature-membership questions; specific target-model selection has no unique reference and is excluded from reference counts.

Cells show **cumulative workflow decision time · final status**.

| Workflow | Decider | Liquid d1-2B | H2O 4B FP8 | Jev | GPT 6.1 Sol |
| --- | --- | --- | --- | --- | --- |
| USDC order credit | 257.8 ms · complete | 268.5 ms · complete | 482.7 ms · incomplete | 3444.8 ms · incomplete | 5966.3 ms · complete |
| Top-up verification and credit | 253.6 ms · complete | 267.4 ms · complete | 441.1 ms · incomplete | 1573.8 ms · incomplete | 4372.5 ms · complete |
| Stablecoin refund | 249.2 ms · complete | 258.5 ms · complete | 436.5 ms · complete | 1621.2 ms · complete | 4576.8 ms · complete |
| Merchant settlement | 255.5 ms · complete | 265.4 ms · incomplete | 440.6 ms · complete | 1804.9 ms · complete | 4829.3 ms · complete |
| Customer-support routing | 217.6 ms · complete | 187.7 ms · complete | 626.3 ms · complete | 601.8 ms · call failed | 2452.6 ms · complete |
| Code routing | 214.4 ms · call failed | 193.4 ms · call failed | 441.2 ms · incomplete | 759.5 ms · incomplete | 2114.9 ms · incomplete |
| Settlement-analysis routing | 212.3 ms · call failed | 194.4 ms · call failed | 444.5 ms · call failed | 655.1 ms · call failed | 2914.0 ms · complete |
| Bilingual-index routing | 103.9 ms · complete | 79.9 ms · complete | 160.8 ms · complete | 618.8 ms · complete | 1953.9 ms · complete |
| Turn on AC | 146.9 ms · complete | 135.1 ms · complete | 233.9 ms · complete | 1004.4 ms · complete | 2897.5 ms · complete |
| Reduce temperature only | 147.3 ms · incomplete | 104.4 ms · incomplete | 233.2 ms · complete | 1068.2 ms · complete | 2299.8 ms · complete |
| Replace temperature presets | 725.3 ms · complete | 133.5 ms · complete | 1207.3 ms · complete | 946.1 ms · complete | 3945.7 ms · complete |

Behavior worth inspecting:

- **Payments:** Decider completed all four funds-and-ticket goals. Liquid completed three, treating an already-settled case as delayed and escalating it. H2O/Jev waited instead of crediting the deposit/top-up examples. Some intake/verification choices differed from references even when final business goals were achieved.
- **Device:** for “reduce 26°C by 2°C, leave everything else unchanged,” Decider saved 18°C and cooling; Liquid saved 20°C. H2O, Jev and GPT reached the correct final state. The executor did not repair those errors to 24°C.
- **Routing:** all five correctly classified all four task types, while target selections differed. For coding, Decider/Liquid targets reached the approximately 40-second read timeout; H2O/Jev/GPT selected Claude Sonnet 5.5 and reached the 4096-output-token cap. None completed that task. For settlement analysis, only GPT's selected GPT-6.1 Sol returned a complete result; the other four targets timed out. Across 20 target calls there were seven timeouts and three token-truncated responses. These were not decision API failures.
- **Temperature sets:** every provider replaced, saved and confirmed the correct set. The 15-question step takes longer than single-question steps for Decider/H2O; Liquid was fastest on this multi-question request.

All three local models returned fast, valid decisions under this configuration, with remaining business-choice errors. Decider performed well on these payment workflows; H2O achieved the relative-temperature goal; Liquid had the lowest mean decision time in this sample. Inspect actual task outcomes alongside response success.

[Full inputs, outputs, execution and timing](results/resident-workflows-2026-10-29.json) · [GPU smoke](results/resident-gpu-smoke-2026-10-29.json). Failed target wait time is retained in the original `execute.total_ms`; the final release also includes it in tool-time totals. Decision observations were not remeasured or rewritten.

Actual screenshots: [fund processing](screenshots/resident-workflows/payment-pending-execute.png) · [device temperature](screenshots/resident-workflows/device-relative-temperature.png) · [temperature selection](screenshots/resident-workflows/temperature-presets-selection.png).

288 automated tests passed. Real browser verification covered all 11 cases without JavaScript errors. Separate synthetic checks covered provider failure, candidate reversal, editing, JSON export and 390px mobile layout; synthetic fixtures are excluded from measured evidence.

Historical evidence: [earlier single-step website runs](results/five-models-live-2026-10-29.json) · [earlier GPU smoke](results/five-model-gpu-smoke-2026-10-29.json) · [seven-model validation](../validation/open_decision_models/README.md). Those inputs and deployments differ and remain separate from this batch.

Final-release verification: [customer-support routing record](results/resident-final-check-2026-10-29.json) · [model-selection screenshot](screenshots/resident-workflows/routing-support-model.png) · [result screenshot](screenshots/resident-workflows/routing-support-results.png). All five completed both decisions; the **40.0-second** read timeout of Jev’s selected target is correctly included in tool time, with the original selection and error type retained.
