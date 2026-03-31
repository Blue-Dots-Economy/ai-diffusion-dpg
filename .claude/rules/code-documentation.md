# Rule: Code Documentation

Every public class, method, and function must have a docstring. Private helpers (`_` prefix) should have one if the logic is non-obvious.

**Format:** Use Google-style docstrings.

```python
def assemble_prompt(self, request: PromptRequest) -> PromptResponse:
    """Assemble the full LLM prompt from NLU results and session history.

    Args:
        request: Contains intent, entities, session history, and raw input.

    Returns:
        PromptResponse with the assembled system prompt and user message.

    Raises:
        ValueError: If request.intent is None or empty.
        KnowledgeEngineError: If RAG retrieval fails after retries.
    """
```

**Rules:**
- The first line is a single-sentence summary ending with a period.
- Document all parameters, return values, and raised exceptions.
- Do not restate the function name or describe *how* it works — describe *what* it does and *why* the caller needs it.
- Module-level docstrings must state the module's role within the DPG framework and which block it belongs to.
