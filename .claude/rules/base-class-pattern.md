# Rule: Base Class Pattern

Every core component must define a base class or abstract interface **before** any concrete implementation.

- Base class declares required methods with signatures and return types.
- Concrete implementations inherit from the base class and implement every method.
- No concrete implementation may be used by another module unless it inherits from the base class.

```python
from abc import ABC, abstractmethod

class MemoryLayerBase(ABC):

    @abstractmethod
    def read(self, session_id: str) -> dict:
        """Load session state. Returns empty dict if no state exists."""

    @abstractmethod
    def write(self, session_id: str, state: dict) -> None:
        """Persist session state."""
```

All classes derived from a base class must:

- Implement **every** method declared in the base class — no partial implementations.
- Preserve the exact method signature. Do not add, remove, or rename parameters in derived classes.
- Return the same output type and structure the base class documents. Different implementations must not return different shapes.

If a method is not applicable in a stub, return the correct empty/default value — not `NotImplementedError` in production paths.

Every function must explicitly handle edge conditions. Do not assume inputs are well-formed.

| Condition | Expected behaviour |
|---|---|
| Empty string input | Return a structured empty result, not an error |
| `None` for a required parameter | Raise a descriptive `ValueError` immediately |
| Missing key in a dict | Use `.get()` with a safe default; never use `[]` blindly |
| Empty list or zero results | Return an empty result with a clear status field |
| Unexpected type from upstream | Log the type mismatch and return a structured error response |

Functions must fail safely — never crash the caller with an unhandled exception.

Each module exposes a defined public interface only.

- Other modules interact exclusively through the base class interface or documented public methods.
- Internal helpers must not be imported by other modules.
- Prefix internal functions with `_` to signal they are not public.

```python
# Correct
from stubs.memory_stub import SessionMemory

# Wrong — never reach into internals
from stubs.memory_stub import _build_state_key
```
