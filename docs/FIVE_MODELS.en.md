# Five models on the original workflows

[Open the module](https://d3i7ogfx5ml7dc.cloudfront.net/#comparison) · [中文](FIVE_MODELS.md)

Compare Strands Decider, Liquid d1-2B, H2O-Lightning 4B, Jev and GPT using the original payment and routing modules' tasks, facts, instructions, candidates and ordering. The three local models remain resident on the same g6.xlarge / L4; requests run serially at concurrency one.

## Original behavior restored

| Component | Behavior |
| --- | --- |
| Four payment cases | USDC at 3/12 confirmations; USDT expected 100, received 99.80; refund destination unverified and approval missing; 4800 USDC settlement unpaid after 120 minutes |
| Five payment steps | Intake → verify → approve → process → close. Every decision uses the same original snapshot; execute the five saved suggestions afterward |
| Payment inputs | Original `crypto.payload_for(..., input_profile="baseline")`, without feedback, extra conditions or rewritten completion facts; original candidate order |
| Routing | Original customer-support, code, settlement-analysis and bilingual-index tasks: choose a workflow, then select a model using the original benchmark evidence |
| Target execution | Optional per-column “execute target model” button after routing; target generation is separate from routing completion and decision timing |
| GPT | Original classifier system prompt, single `{"choice":"candidate_id"}` output, 1024 output tokens and low effort; GPT-6.1 Sol for payments, GPT-6 Sol for routing |
| Invocation | Existing providers reuse `crypto.decision` / `routing.decision`; Liquid/H2O receive identical `state` / `questions` through their native services |

Payment/routing task editing and candidate reversal are locked to the original inputs. Different model choices, execution guards and actual final state remain visible. Unmet payment prerequisites can correctly leave the funds pending and ticket open for follow-up.

The Device Control tab contains three scenarios: home air-conditioning setup, relative temperature adjustment and saved temperature presets. All retain their three-step interaction, task editing and candidate reversal. The UI has three categories—payments, routing and device control—with 11 cases total; this retest covers the eight restored payment/routing cases.

## Original-input retest (2026-10-29)

The live browser completed **140/140 real decision calls** across eight cases and five models. All returned valid choices, and all 140 actual inputs matched captures from the original modules taken before this fix. Each scenario/provider ran once, serially in Decider → Liquid → H2O → Jev → GPT order. Latency includes backend communication and service wrappers; this batch had no warmup, first-request exclusion or network subtraction. These small samples do not establish general accuracy or stable latency.

[Full requests, responses, execution and timing](results/five-models-original-2026-10-29.json) · [Payment screenshot](screenshots/five-models-original/payment-pending-results.png) · [Refund execution](screenshots/five-models-original/payment-refund-execute.png) · [Routing screenshot](screenshots/five-models-original/routing-support-results.png)

### Four payment cases

Each cell shows **cumulative latency for five decisions / matches to the original demo reference**. Reference agreement measures choices, separately from funds processing.

| Original case | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6.1 Sol |
| --- | ---: | ---: | ---: | ---: | ---: |
| USDC pending, 3/12 confirmations | 244.9 ms / 4/5 | 183.5 ms / 2/5 | 391.2 ms / 3/5 | 3719.7 ms / 4/5 | 4748.8 ms / 5/5 |
| USDT underpaid by 0.20 | 242.2 ms / 4/5 | 185.5 ms / 2/5 | 388.1 ms / 3/5 | 1740.4 ms / 4/5 | 4292.4 ms / 5/5 |
| Refund, destination unverified and unapproved | 240.4 ms / 2/5 | 187.5 ms / 4/5 | 389.8 ms / 4/5 | 1719.5 ms / 5/5 | 7186.3 ms / 5/5 |
| Merchant settlement overdue | 242.7 ms / 4/5 | 191.9 ms / 4/5 | 388.2 ms / 4/5 | 1747.5 ms / 5/5 | 4147.4 ms / 5/5 |
| **Reference matches across 20 steps** | **14/20** | **12/20** | **14/20** | **18/20** | **20/20** |
| **Mean backend latency per decision** | **48.5 ms** | **37.4 ms** | **77.9 ms** | **446.4 ms** | **1018.7 ms** |

Decider's case results, 4/5, 4/5, 2/5 and 4/5, match the earlier original fixed-workflow record. The superseded version's higher completion counts came from changed initial inputs. Liquid was fastest in this batch; GPT best matched the original reference. H2O and Decider had equal total agreement but different mistakes. Jev's first USDC intake took 1987.1 ms and remains included; this batch did not determine whether Lambda cold start caused it.

Selected actual differences:

| Step | Original reference | Decider | Liquid | H2O | Jev | GPT |
| --- | --- | --- | --- | --- | --- | --- |
| USDC approval | `approved` | `await_approval` | `await_approval` | `await_approval` | `await_approval` | `approved` |
| USDT processing | `hold` | `credit_order` | `wait_settlement` | `wait_settlement` | `hold` | `hold` |
| Refund approval | `verify_destination` | `await_approval` | `verify_destination` | `verify_destination` | `verify_destination` | `verify_destination` |
| Refund processing | `hold` | `refund_payment` | `credit_order` | `wait_settlement` | `hold` | `hold` |
| Refund close | `follow_up` | `resolve` | `follow_up` | `follow_up` | `follow_up` | `follow_up` |

All payment runs executed their five saved suggestions. The original funds prerequisites remain unmet: no order was credited, refund paid or settlement completed, and all tickets remain open for follow-up. This includes GPT's correct handling. Execution guards adjusted four Decider actions and two Liquid actions; none for the others. For example, Decider's proposed refund was changed from `refund_payment` to `hold`, and `resolve` to `follow_up`. H2O's incorrect wait actions do not trigger funds guards, so **zero guards does not imply correct choices**. Final customer messages come from the original simulated executor and are not evidence of model-generated answer quality.

### Four routing cases

The original tasks request: a customer reply about USDC at 3/12 confirmations; a payment webhook with deduplication, signature verification, Decimal and concurrency safety; a 12000 USDC settlement discrepancy with a 0.8% fee and 24 USDC hold; and bilingual payment-ticket embedding. Exact task text and candidate evidence are saved with every request.

All five models matched the expected **workflow type in 4/4 cases**. Their second-step model choices follow. The table reports actual selections without imposing a single correct answer on open-ended text-model selection.

| Case | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6 Sol |
| --- | --- | --- | --- | --- | --- |
| Frequent payment support | Claude Opus 5 | Claude Opus 4.6 | GPT 5.5 | Claude Opus 5.5 | GPT-6 Luna |
| Payment webhook code | Claude Opus 5 | Claude Opus 5.5 | Claude Sonnet 5.5 | Claude Sonnet 5.5 | Claude Sonnet 5.5 |
| Settlement analysis | Claude Opus 5 | Claude Opus 5.5 | Claude Opus 5.5 | Claude Opus 5.5 | GPT-6.1 Sol |
| Bilingual ticket indexing | **Embed English** | Embed Multilingual | Embed Multilingual | Embed Multilingual | Embed Multilingual |

| Cumulative latency for two decisions | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6 Sol |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frequent payment support | 212.7 ms | 194.1 ms | 438.6 ms | 644.6 ms | 1372.0 ms |
| Payment webhook code | 211.3 ms | 196.1 ms | 439.5 ms | 701.8 ms | 1251.4 ms |
| Settlement analysis | 212.6 ms | 195.0 ms | 439.4 ms | 779.7 ms | 1454.9 ms |
| Bilingual ticket indexing | 101.5 ms | 78.4 ms | 165.3 ms | 733.4 ms | 998.6 ms |

Decider identified all four workflows, but still selected the English-only embedding model for the bilingual task, contrary to the original candidate's language capability. The other four selected the multilingual model; see the [actual request/response screenshot](screenshots/five-models-original/routing-retrieval-model.png). Decider selected Claude Opus 5 for all three text tasks, while other models varied by task. Routing selections alone do not establish target answer quality or optimal cost. This batch preserved the original two-step behavior and did not automatically execute targets.

## Input verification

Before applying the fix, inputs were captured from the live original modules: 20 payment steps, four routing classifications and 20 branch candidate payloads. The [captured fixtures](../tests/fixtures/original_comparison_inputs.json) are independent comparison evidence.

Retests compare actual state text, instructions, candidate content and order against those captures, checking both original input hashes and fully serialized JSON with preserved ordering. References never enter provider requests. Catalog and ranking snapshots remain fixed during this batch.

Payment decisions share the original snapshot across providers and steps. Routing's second input depends on that provider's first choice; identical workflows receive identical candidate evidence. Errors and different choices remain actual observations.

## UI and timing

Five summary columns show cumulative decision latency, choices and final state; step tabs show exact inputs, responses and execution records. Payments show funds/ticket status. Routing highlights workflow and model selection, with optional target output separately displayed.

Step and cumulative timing use backend invocation elapsed time: the original providers retain `backend_total_ms`; Liquid/H2O measure ECS-to-resident-gateway calls. Cumulative latency is exactly the sum of displayed steps. Tool time is separate. Jev includes its existing Lambda bridge; native tokenizers and service wrappers differ.

Login, CSRF, account isolation, private S3 and CloudFront OAC remain in place. One comparison runs globally in the single API process; at most 20 records remain for 30 minutes. Optional target results live in the current page/export and are not restored after refresh.

Deployment: Decider v19 BF16 / vLLM 0.31.0; Liquid d1-2B BF16 / official Transformers; H2O 4B FP8 / patched vLLM 0.30.0. All reside on existing `i-0ae7eb0abcb815af3`, concurrency one. [Operations](../deployment/open_models/README.md).

[Original payment module](../app/crypto.py) · [Original routing module](../app/routing.py) · [Five-model orchestration](../app/comparison.py) · [Scenario/state implementation](../app/comparison_workflows.py). Deploy with `--keep-model-catalog` to preserve the candidate snapshot.

The superseded version changed initial conditions and workflow behavior. Its [historical observations](FIVE_MODELS_CHANGED_INPUTS.en.md), including completion counts such as 8/11, are no longer presented as original-module comparison results.
