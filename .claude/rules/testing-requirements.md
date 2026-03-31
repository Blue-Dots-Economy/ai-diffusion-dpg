# Rule: Testing Requirements

Every module must cover three test categories:

| Category | What to test |
|---|---|
| Normal execution | Correct output for valid, well-formed inputs |
| Edge cases | Empty inputs, boundary values, missing optional fields |
| Failure scenarios | External call failure, invalid config, blocked output, upstream timeout |

- Tests live in `tests/` inside the module directory.
- Mock all external dependencies — no real API calls in unit tests.
- Test file names must match: `test_llm_wrapper.py` tests `llm_wrapper.py`.
- Maintain **≥ 70% line coverage** across `agent_core/` and `knowledge_engine/`.
