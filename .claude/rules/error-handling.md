# Rule: Error Handling

All calls to external systems (LLM API, vector DB, connectors, mock servers) must include:

- **Timeout** — explicit timeout on every external call; never rely on the default.
- **Retry** — retry transient failures (rate limits, timeouts) at least once with exponential backoff.
- **Structured errors** — return a typed exception or dict the caller can handle programmatically; never return raw exception strings.
- **No silent swallowing** — never use bare `except: pass`; always log and re-raise or return a structured failure.

```python
# Wrong
try:
    result = call_external_api()
except Exception:
    pass

# Correct
try:
    result = call_external_api()
except TimeoutError as e:
    logger.error("API timeout", extra={"error": str(e)})
    raise ExternalCallError("API timed out") from e
```
