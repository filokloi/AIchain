# AIchain Operator Metrics Review

## 1. Current Observability Reality Check
- **What already existed**: 
  - The `/health` endpoint exported base state indices. 
  - The `/status` endpoint dumped catalog configurations and static routing rules.
  - The `AuditLogger` maintained a granular write-only local JSON log inside `~/.openclaw/aichain/audit.jsonl`.
- **What was missing**: Real-time aggregation metrics. The operator had no immediate, queryable way to determine cumulative latencies, real route distributions over time, or occurrences of backend failovers without manually scraping an ever-growing line-delimited JSON file.
- **What operator decisions were not yet supported by data**: Decisions regarding whether `budget_preference` limits or timeout layers are actually triggering often, or if the primary `fast_brain` model is healthy and fulfilling its layer-4 purpose. 

## 2. Metrics Design
- **Chosen metrics set**: 
  - `total_requests`: Core ingress counter.
  - `average_latency_ms`: Employs an Exponentially Weighted Moving Average (`alpha=0.1`) for rolling visibility mapping the recent trailing sequence, rather than a monolithic historic average.
  - `fallback_events`: Trigger counts mapping how often the primary routed model failed or timed out.
  - `routes_selected`: Target model string distribution matching frequencies.
- **Why these metrics were selected**: They directly answer whether the router is fast enough, whether the primary fast-brain is carrying the load, and whether fallbacks are actively rescuing bad paths.
- **What was intentionally not added**:
  - Request body payload dumps or conversational token histories, preserving memory guarantees and privacy.
  - Heavy graphing utilities, PromQL registries, or external SaaS dependencies that violate the AIchain "low-operation cost" core mission.

## 3. Changes Implemented
- **`aichaind/telemetry/metrics.py`**
  - **Purpose**: A lightweight `OperatorMetrics` registry class managing rolling aggregations and disk-flushing snapshots natively in memory securely via threading locks.
- **`aichaind/transport/http_server.py`**
  - **Purpose**: Bound the global metric registry directly into the proxy handlers.
  - **What changed**: The `do_GET` route for `/status` returns the metrics natively. The `_handle_chat_completions` executor increments latency trailing averages and fallback/timeout events transparently per-request outcome.
- **`aichaind/main.py`**
  - **Purpose**: Instantiate the registry on startup and guarantee a clean snapshot flush whenever `aichaind` experiences graceful shutdown.
- **`docs/OPERATOR_RUNBOOK.md`**
  - **Purpose**: An interpretation manual teaching operators how to rely on `/status` values to audit backend hostiles or proxy failure behavior.

## 4. Validation Performed
- **Scenarios tested**: Booted the `OperatorMetrics` injection, spawned a live HTTP gateway thread, and executed `aichain_smoke_test.py`.
- **Expected vs actual metrics behavior**: Upon hitting `http://127.0.0.1:8080/v1/chat/completions`, the smoke-test reliably triggered exactly `total_requests: 1` and initialized `routes_selected["deepseek/deepseek-chat"]: 1`, with a proper integer trailing average execution latency metric tracking properly.
- **Regression checks**: Verified all prior routing blocks (`pii`, `policy`, `manual_override`) retain intact structures.
- **Secret/safety checks**: Operator metrics return absolute zero proprietary secrets. The distribution map is only counting API names, fulfilling the operator transparency requirement.

## 5. Operator Usability
- **Can an operator actually interpret the new metrics?**: Yes. The `OPERATOR_RUNBOOK.md` precisely spells out when and how to invoke `/status`.
- **What questions can now be answered reliably?**: "Is my fast brain answering most of my queries?" and "Are APIs timing out invisibly behind the proxy?"
- **What still remains hard to see?**: Individual granular API key consumption per-provider (due to cloud API limits preventing programmatic tracking).

## 6. Remaining Risks / Limits
- Moving averages reset on cold-reboot daemon wipeouts if the daemon crashes non-gracefully (i.e., `kill -9`). A clean `SIGTERM` accurately flushes the tracking JSON to preserve continuity. 

## 7. Final Metrics Verdict
- **Is AIchain now instrumented enough for evidence-driven operator use?**: **Yes**. AIchain possesses the absolute minimal set of robust telemetry to evaluate real operator usage footprints continuously without incurring architectural analytics bloat.
- **What is the most natural next phase after operator metrics?**: Since AIchain v5 is completed, POSIX compatibility classified, and Telemetry deployed, the immediate step is integrating the complete feature set upstream, concluding the deployment cycle.

## 8. Repo Artifact Proposal
- **Location**: `docs/OPERATOR_METRICS_REVIEW.md`
