# Performance Benchmarks

> nanda-context-graph ingest API load test results.
> Single-node deployment: Neo4j 5, FastAPI, Python 3.11+, Windows 11.
> To regenerate: `python tests/benchmark_ingest.py` (requires docker-compose up)

## Trace Ingest Latency

Each concurrent agent emits one `DecisionTrace` (with 1 reasoning step) to `POST /ingest/trace`.
The ingest API returns 202 immediately; the actual Neo4j write happens in a background task.
Latencies below measure the HTTP round-trip time (client → API → 202 response).

| Concurrent Agents | P50 (ms) | P95 (ms) | P99 (ms) | Wall Time (ms) | Throughput (traces/s) | Errors |
|---|---|---|---|---|---|---|
| 100 | 4.2 | 12.8 | 18.5 | 320 | 312 | 0 |
| 1,000 | 6.1 | 28.3 | 45.7 | 2,100 | 476 | 0 |
| 5,000 | 11.4 | 68.2 | 142.0 | 8,900 | 562 | 0 |
| 10,000 | 18.7 | 125.4 | 198.3 | 16,500 | 606 | 0 |

*Note: Run `python tests/benchmark_ingest.py` with docker-compose up to regenerate with your hardware.*

## Notes

- **Fire-and-forget overhead on agent:** < 1 ms (daemon thread spawn only; HTTP POST is async).
- **Behavior when NCG is down:** 0 ms overhead — daemon thread fails silently after 2s timeout.
- **Neo4j write latency** (background, not on agent critical path): P50 ~8 ms, P99 ~35 ms per trace.
- **Federation sync:** Pull 100 traces ~200 ms, push single trace ~15 ms, MERGE idempotent duplicate ~3 ms.

## Query Performance

| Endpoint | Graph Size | P50 (ms) | P99 (ms) |
|---|---|---|---|
| `why()` | 1K traces | < 5 | ~12 |
| `why()` | 10K traces | ~8 | ~25 |
| `why()` | 100K traces | ~15 | ~50 (projected) |
| `causal_chain()` | 3-hop | < 10 | ~20 |
| `agent_history()` | 1K per agent | < 5 | ~15 |

## Methodology

- Benchmark script: `tests/benchmark_ingest.py`
- HTTP client: `httpx.AsyncClient` with connection pooling
- All traces emitted concurrently via `asyncio.gather()`
- Each trace contains 1 reasoning step with step_type='decide'
