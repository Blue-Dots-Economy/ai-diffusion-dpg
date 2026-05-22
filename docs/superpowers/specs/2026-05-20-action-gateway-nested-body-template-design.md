# Action Gateway — Nested Request Body Templating

**Goal:** Let any REST tool declared in `action_gateway.yaml` send a request body with **arbitrary nested structure** by declaring the body shape in YAML. The adapter walks the declared shape and substitutes placeholders at call time. Generic — works for any future API whose body isn't a flat dict.

**Why now:** The Blue Dots `apply_job` tool needs a 2-level nested body (`source_item.item_id`, `target_item.item_id`, `requirements_snapshot.job_id`). The current adapter sends a flat `json=all_params`, so the only working option today is to ask the LLM to construct the entire nested body — including all the protocol-static strings like `"blue_dot"`, `"seeker"`, `"profile_1.0"`. That works but is brittle: one LLM slip and the upstream rejects the call. Once nested-body templating is a first-class config feature, every future API that needs nesting gets it for free.

**Scope:** Single-block change inside Action Gateway. No Agent Core changes, no other block changes. Additive — current flat-dict behaviour stays when the new field is absent.

---

## Design

### One new YAML field: `body_template`

Lives on `EndpointDefinition` (per-endpoint), alongside `params`. Optional. When present and the method is non-GET, the adapter walks the template instead of sending `all_params` flat.

```yaml
endpoints:
  - name: apply_job
    method: POST
    path: /api/v1/action/perform
    body_template:
      action_name: "apply"
      source_item:
        item_network: "blue_dot"
        item_domain: "seeker"
        item_type: "profile_1.0"
        item_id: "{profile_item_id}"
      target_item:
        item_network: "blue_dot"
        item_domain: "provider"
        item_type: "job_posting_1.0"
        item_id: "{job_item_id}"
        item_instance_url: "http://65.2.66.144:2742/"
      requirements_snapshot:
        job_id: "{job_item_id}"
        cover_note: ""
        resume_url: ""
    params:
      - name: profile_item_id
        source: agent
        type: string
        required: true
      - name: job_item_id
        source: agent
        type: string
        required: true
```

LLM sees two params (`profile_item_id`, `job_item_id`) — both strings. It passes the two values, the adapter assembles the nested body.

### Placeholder syntax: `{key}` (same as existing path templates)

Two substitution modes for string values inside the template:

| Template string | Behaviour | Example |
|---|---|---|
| Exactly `"{key}"` (whole-value placeholder) | Replaced with the **raw value** of `key` — preserves type (int stays int, list stays list, object stays object). | `"item_id": "{job_item_id}"` with `job_item_id="abc"` → `"item_id": "abc"` |
| `"prefix-{key}-suffix"` (embedded placeholder) | Standard string interpolation; value is `str()`-cast and spliced in. | `"summary": "Applied to {role}"` with `role="Plumber"` → `"summary": "Applied to Plumber"` |
| String with no `{...}` | Literal — copied as-is into the output. | `"item_domain": "seeker"` → `"item_domain": "seeker"` |
| Non-string values (int, bool, null, list, dict) | Copied / recursed into as-is. Only string values are scanned for placeholders. | `"limit": 100` → `"limit": 100` |

The whole-value rule is what lets you template a list or object without stringifying it:

```yaml
body_template:
  skills: "{skills}"     # LLM sends skills=["plumbing"] → body has "skills": ["plumbing"]
  state: "{item_state}"  # LLM sends item_state={"name":"R", ...} → body has "state": {"name":"R", ...}
```

### Where placeholder values come from

A single merged dict, in this order (later wins on key collision):
1. Static params declared in the endpoint's `params` list (`source: static, value: X`).
2. Agent params supplied by the LLM at call time (`source: agent`).

**Out of scope this round:** session-memory placeholders inside `body_template`. The motivating Blue Dots case has the backend fixing Gap 1 (moving `profile_item_id` off the URL path), so the LLM can pass it as a regular `source: agent` param. If a future API needs to inject a session-memory value directly into a body without LLM involvement, that's a follow-up — same place in the code (the merge dict) gets an extra source.

### Missing-placeholder behaviour

When a placeholder key resolves to nothing in the merged dict, **the entire field is dropped from the output**:

```yaml
body_template:
  item_state:
    name: "{name}"
    age: "{age}"
```

LLM sends only `name="Ramesh"` (no age). Adapter produces `{"item_state": {"name": "Ramesh"}}` — no `"age"` key at all.

Reasoning: matches HTTP best practice ("omit the field" beats "send null" or "send empty string"). Required params are enforced by the JSON Schema check that runs BEFORE the adapter assembles the body, so missing-required can't reach this code path.

After substitution, if any nested dict becomes empty (all its fields dropped), the parent's key is also dropped. So if the LLM sends nothing optional, the body cleanly contains only what was supplied + static fields.

### Top-level shape

`body_template` may be either a dict (object body — common case) or a list (top-level array body — rare but real). The walker handles both. Anything else (string, scalar) at the top → schema validation error at startup.

### GET endpoints — body_template is ignored

GETs already work today via the `params=all_params` (query string) path. The adapter's existing `if method == "GET"` branch keeps its behaviour. `body_template` is only consulted when method is non-GET.

### Backward compatibility

`body_template` is optional. When absent, the adapter's existing non-GET path is unchanged:
- `json=all_params` (flat dict).

KKB's existing tools (`onest_market_lookup` GET, the three `/mock/*` POST/PATCH endpoints) keep working untouched. No KKB config edits.

---

## Files that change (per `.claude/rules/runtime-devkit-sync.md`)

| File | Change |
|---|---|
| `action_gateway/src/schema/config.py` | Add `body_template: Optional[dict \| list] = None` to `EndpointDefinition`. |
| `action_gateway/src/adapters/rest_api.py` | New `_render_body_template()` helper. In `execute()`, when method != GET and `body_template` present, build body via the helper; else keep `json=all_params`. |
| `action_gateway/tests/test_rest_api_adapter.py` | New test cases covering whole-value, embedded, missing-key drop, nested object, nested array, list top-level. |
| `dev-kit/dev_kit/schemas/domain/action_gateway.py` | Mirror the `body_template` field on the dev-kit `EndpointDefinition` mirror. Lenient (domain-half). |
| `dev-kit/dev_kit/schema.py` | Mirror the same field in the flat-file copy (host-mode deploy gate). |
| `dev-kit/Dockerfile` | No code change, but **must be rebuilt** so the baked runtime schemas under `/app/dpg_runtime_schemas/action_gateway/config.py` pick up the new field — otherwise Docker-mode deploy keeps rejecting valid YAML. |
| `dev-kit/tests/schemas/domain/test_action_gateway.py` | One accept-valid + one reject-invalid test for `body_template`. |

`FIELD_RULES` (`dev-kit/dev_kit/agent/field_rules/action_gateway.py`) does **not** need a new entry: `body_template` is developer-facing structure (lives inside a tool definition), not a chat-time wizard field. The wizard does not edit individual `body_template` shapes; if a user wants nested-body APIs, a developer authors the YAML directly.

`dev-kit/dpg/action_gateway.yaml` (framework defaults) does not change — `body_template` is per-tool, not framework.

---

## Implementation plan

### Task 1: Schema — add `body_template` field

**Files:**
- Modify: `action_gateway/src/schema/config.py` — `EndpointDefinition`

- [ ] **Step 1: Write the failing test**

Add to `action_gateway/tests/test_schema_config.py` (create if absent — mirror existing schema tests):

```python
def test_endpoint_accepts_body_template_dict():
    cfg = EndpointDefinition(
        name="apply",
        method="POST",
        path="/perform",
        body_template={"action": "apply", "id": "{x}"},
    )
    assert cfg.body_template == {"action": "apply", "id": "{x}"}

def test_endpoint_accepts_body_template_list():
    cfg = EndpointDefinition(
        name="bulk",
        method="POST",
        path="/bulk",
        body_template=[{"id": "{a}"}, {"id": "{b}"}],
    )
    assert isinstance(cfg.body_template, list)

def test_endpoint_body_template_default_none():
    cfg = EndpointDefinition(name="x", method="GET", path="/x")
    assert cfg.body_template is None
```

Run: `cd action_gateway && uv run pytest tests/test_schema_config.py::test_endpoint_accepts_body_template_dict -v`
Expected: FAIL with "extra fields not permitted" or AttributeError.

- [ ] **Step 2: Add the field**

Edit `action_gateway/src/schema/config.py` around line 160 (inside `EndpointDefinition`):

```python
class EndpointDefinition(BaseModel):
    ...
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    method: HttpMethod = HttpMethod.POST
    path: str = ""
    params: list[ParamDefinition] = Field(default_factory=list)
    body_template: Optional[dict | list] = None    # NEW
```

Update the class docstring to document `body_template`.

- [ ] **Step 3: Tests pass**

Run: `cd action_gateway && uv run pytest tests/test_schema_config.py -v`
Expected: all three new tests PASS.

---

### Task 2: Adapter — `_render_body_template()` helper

**Files:**
- Modify: `action_gateway/src/adapters/rest_api.py`

- [ ] **Step 1: Write failing tests**

Add to `action_gateway/tests/test_rest_api_adapter.py` — six tests covering the rules:

```python
def test_body_template_whole_value_string():
    rendered = _render_body_template(
        {"item_id": "{x}"},
        {"x": "abc-123"},
    )
    assert rendered == {"item_id": "abc-123"}

def test_body_template_whole_value_preserves_type():
    rendered = _render_body_template(
        {"count": "{n}", "skills": "{s}", "flag": "{b}"},
        {"n": 5, "s": ["plumbing"], "b": True},
    )
    assert rendered == {"count": 5, "skills": ["plumbing"], "flag": True}

def test_body_template_embedded_substitution_stringifies():
    rendered = _render_body_template(
        {"summary": "applied to {role} in {city}"},
        {"role": "Plumber", "city": "Bengaluru"},
    )
    assert rendered == {"summary": "applied to Plumber in Bengaluru"}

def test_body_template_missing_key_drops_field():
    rendered = _render_body_template(
        {"name": "{name}", "age": "{age}"},
        {"name": "Ramesh"},
    )
    assert rendered == {"name": "Ramesh"}

def test_body_template_empty_nested_dict_dropped():
    rendered = _render_body_template(
        {"item_state": {"age": "{age}"}, "kept": "ok"},
        {},
    )
    assert rendered == {"kept": "ok"}

def test_body_template_nested_object_and_list():
    rendered = _render_body_template(
        {
            "action": "apply",
            "source_item": {"id": "{pid}"},
            "items": [{"id": "{a}"}, {"id": "{b}"}],
        },
        {"pid": "p-1", "a": "x", "b": "y"},
    )
    assert rendered == {
        "action": "apply",
        "source_item": {"id": "p-1"},
        "items": [{"id": "x"}, {"id": "y"}],
    }

def test_body_template_top_level_list():
    rendered = _render_body_template(
        [{"id": "{a}"}, {"id": "{b}"}],
        {"a": "x", "b": "y"},
    )
    assert rendered == [{"id": "x"}, {"id": "y"}]
```

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py -k body_template -v`
Expected: ImportError on `_render_body_template`.

- [ ] **Step 2: Implement the helper**

Add to `action_gateway/src/adapters/rest_api.py` near the top, alongside `_apply_projection`:

```python
import re

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_WHOLE_PLACEHOLDER_RE = re.compile(r"^\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")


_SENTINEL_DROP = object()


def _render_body_template(template, values: dict):
    """Walk a body template, substituting {placeholder} strings from values.

    Substitution rules:
      - A string equal to "{key}" is replaced by the raw value of values[key],
        preserving type (list, dict, int, bool stay as-is).
      - A string containing "{key}" embedded among other characters is
        rendered via standard string substitution (values get str()-cast).
      - A placeholder whose key is not in values causes the containing field
        to be dropped from the parent dict/list.
      - Non-string scalars (int, bool, None) and missing-placeholder-free
        strings are copied as-is.
      - Nested dicts/lists are walked recursively. A dict that becomes empty
        after substitution is itself dropped from its parent.

    Args:
        template: The template tree (dict, list, str, or scalar).
        values: Merged dict of static + agent params.

    Returns:
        The rendered structure. May be _SENTINEL_DROP at the top level if the
        entire template resolves to nothing — callers should treat that as
        "no body".
    """
    if isinstance(template, dict):
        out: dict = {}
        for k, v in template.items():
            rendered = _render_body_template(v, values)
            if rendered is _SENTINEL_DROP:
                continue
            if isinstance(rendered, dict) and not rendered:
                # nested dict became empty → drop
                continue
            out[k] = rendered
        return out
    if isinstance(template, list):
        rendered_items = []
        for item in template:
            r = _render_body_template(item, values)
            if r is _SENTINEL_DROP:
                continue
            rendered_items.append(r)
        return rendered_items
    if isinstance(template, str):
        whole = _WHOLE_PLACEHOLDER_RE.match(template)
        if whole:
            key = whole.group(1)
            if key in values and values[key] is not None and values[key] != "":
                return values[key]
            return _SENTINEL_DROP
        # embedded — substitute keys, drop the field if any referenced key is missing
        keys = _PLACEHOLDER_RE.findall(template)
        if not keys:
            return template
        for k in keys:
            if k not in values or values[k] is None or values[k] == "":
                return _SENTINEL_DROP
        return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), template)
    # scalars (int, bool, float, None) — copy through
    return template
```

- [ ] **Step 3: Wire it into `execute()`**

In `rest_api.py:execute()`, around line 295, modify the non-GET branch:

```python
if method == "GET":
    response = await self._http_client.request(
        method=method, url=url, params=all_params,
        headers=headers, timeout=timeout_s,
    )
else:
    body_template = endpoint.get("body_template")
    if body_template is not None:
        body = _render_body_template(body_template, all_params)
        if body is _SENTINEL_DROP:
            body = {}
    else:
        body = all_params
    response = await self._http_client.request(
        method=method, url=url, json=body,
        headers=headers, timeout=timeout_s,
    )
```

- [ ] **Step 4: All tests pass**

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py -v`
Expected: every existing test PLUS the seven new `body_template` tests PASS.

- [ ] **Step 5: Integration test — end-to-end through adapter**

Add to `test_rest_api_adapter.py`:

```python
@pytest.mark.asyncio
async def test_execute_uses_body_template_for_non_get(monkeypatch):
    config = {
        "id": "apply_job",
        "type": "rest_api",
        "category": "write",
        "base_url": "http://example.test",
        "auth": {"type": "none"},
        "timeout_ms": 1000,
        "endpoints": [{
            "name": "apply",
            "method": "POST",
            "path": "/apply",
            "body_template": {
                "action": "apply",
                "source_item": {"item_id": "{profile_id}"},
                "target_item": {"item_id": "{job_id}"},
            },
            "params": [
                {"name": "profile_id", "source": "agent", "type": "string", "required": True},
                {"name": "job_id", "source": "agent", "type": "string", "required": True},
            ],
        }],
    }
    adapter = RestApiAdapter(config)

    captured = {}
    async def fake_request(method, url, json=None, headers=None, timeout=None, **kw):
        captured["json"] = json
        return httpx.Response(200, json={"application_id": "A1", "status": "ok"})
    monkeypatch.setattr(adapter._http_client, "request", fake_request)

    result = await adapter.execute(
        tool_name="apply_job",
        params={"profile_id": "p-1", "job_id": "j-1"},
        session_id="s",
        user_id="+91...",
    )
    assert result.success is True
    assert captured["json"] == {
        "action": "apply",
        "source_item": {"item_id": "p-1"},
        "target_item": {"item_id": "j-1"},
    }
```

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py::test_execute_uses_body_template_for_non_get -v`
Expected: PASS.

---

### Task 3: Dev-kit mirror — keep schemas in sync

**Files:**
- Modify: `dev-kit/dev_kit/schemas/domain/action_gateway.py` (per-block mirror, lenient)
- Modify: `dev-kit/dev_kit/schema.py` (flat-file copy, host-mode deploy gate)
- Modify: `dev-kit/tests/schemas/domain/test_action_gateway.py` (accept/reject tests)

- [ ] **Step 1: Locate `EndpointDefinition` mirror in `dev-kit/dev_kit/schemas/domain/action_gateway.py` and add the same `body_template: Optional[dict | list] = None` field.**

- [ ] **Step 2: Locate the same class in `dev-kit/dev_kit/schema.py` (flat-file copy) and add the field there too.**

- [ ] **Step 3: Add one accept-valid test:**

```python
def test_endpoint_accepts_body_template():
    # valid YAML with body_template parses cleanly
    ...
```

and one reject-invalid test (e.g., body_template that's a scalar):

```python
def test_endpoint_rejects_scalar_body_template():
    with pytest.raises(ValidationError):
        EndpointDefinition(name="x", method="POST", path="/x", body_template="oops")
```

- [ ] **Step 4: Verify dev-kit tests pass:**

```bash
cd dev-kit && uv run pytest tests/schemas/domain/test_action_gateway.py -v
```

---

### Task 4: Rebuild the dev-kit Docker image

The baked runtime schemas at `/app/dpg_runtime_schemas/action_gateway/config.py` are copied from `action_gateway/src/schema/config.py` at build time. Until the image is rebuilt, the canonical "Deploy in Docker" gate validates against the OLD schema and rejects valid `body_template` YAML.

- [ ] **Step 1: Rebuild**

```bash
docker build -f dev-kit/Dockerfile -t dpg-dev-kit .
```

- [ ] **Step 2: Verify the baked schema includes the new field**

```bash
docker run --rm dpg-dev-kit grep -n "body_template" /app/dpg_runtime_schemas/action_gateway/config.py
```

Expected: prints the new line.

---

### Task 5: Documentation note

**Files:**
- Modify: `action_gateway/src/adapters/rest_api.py` — class docstring on `RestApiAdapter`

- [ ] **Step 1: Add a one-paragraph note** to the `RestApiAdapter` class docstring describing `body_template`: what it does, when it's used (non-GET only), placeholder syntax, missing-key drop semantics. Point to `_render_body_template` for details.

No new top-level docs file. `CLAUDE.md`'s description of Action Gateway already says "Action Gateway is the sole interface with external systems" — that's accurate regardless.

---

## Self-review

1. **Spec coverage** — every requirement maps to a task:
   - Nested body shape from YAML → Task 1 (schema) + Task 2 (renderer)
   - LLM passes only moving parts → Task 2 (placeholder rules)
   - Type preservation for lists/objects → Task 2 (whole-value rule + test)
   - Missing-key drop → Task 2 (drop sentinel + test)
   - Backward compatibility → Task 2 (only branches when `body_template` present)
   - Schema-sync across runtime + dev-kit → Tasks 3 and 4
2. **Placeholder scan** — no TBDs. The "out of scope" lines explicitly mark session-memory placeholders as deferred; not a placeholder, a deferred decision.
3. **Type consistency** — `_render_body_template` returns dict | list | str | scalar | `_SENTINEL_DROP`. Caller in `execute()` checks `is _SENTINEL_DROP` and substitutes `{}`. Consistent across tests and helper.
4. **YAGNI check** — did NOT add: session-memory injection (deferred until needed), conditional fields, default values, alternative placeholder syntaxes. All wait for real demand.

---

## What this unblocks for Blue Dots

With Gap 1 fixed on the backend (profile_item_id moves out of the URL) AND this `body_template` work landed:

- `update_profile` → URL is static `/api/v1/item`. Body is `body_template` with `{"item_state": {"name": "{name}", "skills": "{skills}", ...}}`. LLM passes flat optional fields. Missing ones get dropped automatically.
- `apply_job` → Body is the full nested `body_template` shown at the top of this doc. LLM passes only `profile_item_id` and `job_item_id`. All protocol strings (`"blue_dot"`, `"seeker"`, etc.) live in the YAML, never in the LLM prompt.

Both tools become robust against LLM drift on protocol strings.
