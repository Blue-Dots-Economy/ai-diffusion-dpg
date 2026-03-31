# Rule: Logging and Observability

Every significant operation must emit a structured log entry. Never use bare `print()` in module code.

Required fields per log entry:

| Field | Description |
|---|---|
| `operation` | Name of the function or step |
| `status` | `success`, `failure`, or `skipped` |
| `error` | Error message and type (failure only) |
| `latency_ms` | Elapsed ms (external calls and LLM calls) |

```python
import logging, time
logger = logging.getLogger(__name__)

start = time.time()
# ... operation ...
logger.info("llm_call", extra={
    "operation": "llm_wrapper.call",
    "status": "success",
    "latency_ms": int((time.time() - start) * 1000),
    "model": model_used,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
})
```

Never log PII, phone numbers, or message content outside the designated audit log path managed by the Learning Layer.
