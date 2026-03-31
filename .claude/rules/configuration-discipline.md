# Rule: Configuration Discipline

No domain-specific or environment-specific value may be hardcoded in source code.

- Model names, API endpoints, temperature, thresholds, blocked phrases, persona text, and timeouts must come from YAML config or environment variables.
- Source code may define defaults for optional parameters, but anything that varies between deployments must be externally configurable.
- Read config once at startup via `config/loader.py`. Never re-read config files inside request paths.

```python
# Wrong
model = "claude-sonnet-4-5-20250514"

# Correct
model = config.agent.primary_model
```
