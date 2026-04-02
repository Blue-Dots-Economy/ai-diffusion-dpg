# Memory Layer Design — v2

> This is the authoritative design document for the Memory Layer redesign.
> All implementation tasks (A1, B1, D1, G1–G7, K1–K7) must follow this spec exactly.

---

## 1. Core Principles

- **Memory Layer stores. It does not extract.** Agent Core (via LLM) decides what to extract from user input and calls `write()`. Memory Layer receives key/value/scope and stores it. No logic, no inference.
- **Redis is the hot path.** Every turn reads from Redis — no Memgraph in the decision loop during an active session.
- **Memgraph is the knowledge graph.** Persistent user identity, profile, journey history, and conversational signals all live in Memgraph across sessions. Raw conversation messages (user text, system text) are never stored.
- **Config-driven schema.** Declared fields come from `domain.yaml`. The Memory Layer code never hardcodes field names, node labels, or relationship types.
- **Ad-hoc data is first-class.** Anything the user says that is relevant but not in the declared config schema is stored as a child attribute node — not discarded.
- **`user_id` is the universal user key.** It flows from Reach Layer → Agent Core → Memory Layer via `TurnInput.user_id`. What `user_id` maps to (phone number, email, WhatsApp ID, or any other identifier) is determined by the Reach Layer config for each deployment. The Memory Layer receives `user_id` as an opaque string — it never knows or cares what type of identifier it is. Redis is keyed by `session_id`. Memgraph is keyed by `user_id`.
- **A user can have multiple concurrent sessions, each with its own TTL.** `user:{user_id}` is a lightweight index in Redis of all active sessions for that user. Its TTL is set equal to the session TTL and is reset on every turn — so it expires naturally if no session has any activity for the configured TTL duration. On reconnect, the Reach Layer presents only the most recently accessed session — the user can continue it or start fresh. All sessions are stored; only the latest is surfaced to the user.

---

## 2. Store Architecture

| Store | Technology | What lives here | Lifetime |
|---|---|---|---|
| Session | Redis hash, TTL-bound | Hot session state — current node, collection round, current question, signals, language, consent, loop count | Session-scoped (TTL from config) |
| Persistent | Memgraph | User identity, profile, journey history, context graph | Permanent (with consent) |

**Turn scope** is already handled by local variables inside Agent Core's `process_turn()` — `turn_id`, `current_user_message`, `normalised_input`, `nlu_result`, `rag_chunks`, `trust_input_result`. These are ephemeral Python variables that exist for one turn and are discarded after the response. No Memory Layer involvement needed. `turn_id` is generated as `uuid4()` at the start of each turn and passed to Learning Layer in `TurnEvent` for audit.

**Broadcast scope** is out of scope for this implementation.

---

## 3. Memgraph Graph Model

### 3.1 Full Structure

```
(User {user_id})
  │
  ├──[:HAS_PROFILE]──→  (UserProfile)
  │                         │
  │                         │  declared fields — defined in domain.yaml
  │                         │  (field names, types, and defaults are all config-driven)
  │                         │
  │                         └──[:HAS_ATTRIBUTE]──→ (UserAttribute {
  │                                                    key, value, raw,
  │                                                    turn, journey_id
  │                                                 })
  │                                                 (one node per ad-hoc attribute)
  │
  ├──[:HAS_JOURNEY_HISTORY]──→ (JourneyHistory)    ← grouping node, no properties
  │                                  │
  │                                  └──[:JOURNEY]──→ (Journey {
  │                                                       journey_id,
  │                                                       started_at, ended_at,
  │                                                       end_reason
  │                                                    })
  │                                                       │
  │                                                       └── configurable child nodes
  │                                                           (labels, relationships, and fields
  │                                                            defined in domain.yaml under
  │                                                            JourneyHistory.child.children)
  │
  └──[:HAS_CONTEXT]──→  (ContextGraph)             ← grouping node, no properties
                             │
                             └──[:SIGNAL]──→ (Signal {
                                                 type, turn, raw, journey_id
                                              })
                                                 │
                                                 └──[:HAS_ATTRIBUTE]──→ (ContextAttribute {
                                                                            key, value, raw,
                                                                            turn, journey_id
                                                                         })
                                                                         (one node per ad-hoc context item)
```

### 3.2 Node Descriptions

| Node | Type | Properties | Purpose |
|---|---|---|---|
| `User` | Root | `user_id` | Anchor node. All data hangs off here. |
| `UserProfile` | Subnode | declared fields from `domain.yaml` | Who the user is — domain-specific profile data |
| `UserAttribute` | Ad-hoc child of UserProfile | key, value, raw, turn, journey_id | Anything relevant the user says that is not in the declared schema |
| `JourneyHistory` | Subnode | (none — grouping only) | Groups all past journeys for this user |
| `Journey` | Child of JourneyHistory | journey_id, started_at, ended_at, end_reason | One node per session — structural fields only |
| `JourneyChild` | Configurable child of Journey | labels, relationships, and fields defined in `domain.yaml` | Domain-specific outcomes per journey (e.g. job roles, appointments, recommendations) |
| `ContextGraph` | Subnode | (none — grouping only) | Groups all conversational signals across journeys |
| `Signal` | Child of ContextGraph | type, turn, raw, journey_id | Per-turn conversational signals (objection, emotion, constraint, etc.) |
| `ContextAttribute` | Ad-hoc child of Signal | key, value, raw, turn, journey_id | Structured extraction from a signal that doesn't fit Signal.type |

### 3.3 Relationship Types

| Relationship | From → To | Meaning |
|---|---|---|
| `HAS_PROFILE` | User → UserProfile | User's identity and profile |
| `HAS_ATTRIBUTE` | UserProfile → UserAttribute | Ad-hoc profile attribute |
| `HAS_JOURNEY_HISTORY` | User → JourneyHistory | Grouping edge |
| `JOURNEY` | JourneyHistory → Journey | One journey per session |
| `<DOMAIN_REL>` | Journey → JourneyChild | Domain-specific outcome — label and relationship name defined in `domain.yaml` |
| `HAS_CONTEXT` | User → ContextGraph | Grouping edge |
| `SIGNAL` | ContextGraph → Signal | One conversational signal |
| `HAS_ATTRIBUTE` | Signal → ContextAttribute | Ad-hoc structured extraction from signal |

### 3.4 Pattern: Declared Fields vs Ad-hoc Attributes

The same pattern applies to all three subnodes:

- **Declared fields** — defined in `domain.yaml` config. Stored as properties directly on the subnode (UserProfile, Journey, Signal). Fast to read, indexed.
- **Ad-hoc attributes** — anything the LLM extracts that is not in the declared schema. Stored as child `*Attribute` nodes connected by `HAS_ATTRIBUTE`. Extensible, no schema change needed.

**Who writes ad-hoc attributes:** Agent Core (via LLM extraction). Memory Layer receives the key/value and creates the child node. Memory Layer never decides what is or isn't ad-hoc — it just stores what Agent Core sends.

---

## 4. Redis Schema

Two keys are maintained per user:

---

### 4.1 User Index Key

```
Key:   user:{user_id}
Type:  Hash
TTL:   state.session.ttl_minutes × 60 seconds — reset on every turn (same as session key)

Fields:
  {session_id_1}    "2026-03-25T10:00:00Z"   ← ISO-8601 last_accessed timestamp
  {session_id_2}    "2026-03-26T15:30:00Z"
  ...                                          ← one field per active session
```

This key is a lightweight index of all sessions belonging to a user. Each field is a `session_id`; the value is the ISO-8601 timestamp of the last turn in that session.

**TTL is reset on every turn.** Whenever any session has activity, the TTL on `user:{user_id}` is refreshed — so the key stays alive as long as there is activity on at least one session. If the user never returns and no session has activity, the key expires naturally after `ttl_minutes` of inactivity, along with the last active session key.

**Note:** Individual session fields inside the hash may become stale if a session expires while the user index key is still alive (kept alive by a different active session). These dead fields are cleaned up lazily on the next `get_active_sessions()` call.

- **On reconnect:** `get_active_sessions()` scans all session_id fields, filters out those whose `session:{session_id}` key has expired in Redis, and removes dead entries from the hash.
- **On flush:** `flush_session()` removes the session's field from the user hash after closing the Journey node. If no fields remain, deletes the user key immediately.
- **On turn:** `write()` updates `last_accessed` timestamp for the session_id field and resets the TTL on the user key.

---

### 4.2 Session Key

```
Key:   session:{session_id}
Type:  Hash
TTL:   state.session.ttl_minutes × 60 seconds  (default: 2880 min / 2 days)

Fields (all stored as strings — type coercion on read):

  Infrastructure fields (always present, framework-level — not configurable):
    user_id          "<opaque string — phone number, email, WhatsApp ID, or any channel identifier>"
    journey_id       "<session_id>"
    is_returning     "true" | "false"

  Domain fields (from domain.yaml state.session.schema — one entry per declared field):
    <field_name>     <default_value from config>
    ...
```

**Three categories of session fields:**

| Category | Redis | Memgraph | Description |
|---|---|---|---|
| Infrastructure | ✓ always present | ✗ | `user_id`, `journey_id`, `is_returning` — framework needs these regardless of domain |
| Domain session fields | ✓ hot path | ✓ persistent copy | Fields extracted during the session that also need to survive across sessions (e.g. language preference). Written to Memgraph via `write(scope="persistent")`, cached in Redis for fast turn-by-turn access |
| Session outcome fields | ✓ tracked live | ✓ promoted on flush | Fields tracked during the session whose **final value** is saved to a Journey node when the session closes — via `merge_on_session_end` config rules |

**On new session start:**
1. `redis.exists(f"session:{session_id}")` → false
2. Check Memgraph for `user_id` → determines `is_returning`
3. Create Journey node in Memgraph under JourneyHistory
4. `redis.hset(f"session:{session_id}", mapping=initial_state)`
5. `redis.expire(f"session:{session_id}", ttl_seconds)`
6. `redis.hset(f"user:{user_id}", session_id, now_iso8601())`  ← register in user index
7. `redis.expire(f"user:{user_id}", ttl_seconds)`              ← set/reset user key TTL

**On subsequent turns:**
- `redis.hgetall(f"session:{session_id}")` — full hash read, O(1)
- `redis.hset(f"user:{user_id}", session_id, now_iso8601())`  ← update last_accessed
- `redis.expire(f"user:{user_id}", ttl_seconds)`              ← reset user key TTL

**On flush_session:**
- Journey node closed in Memgraph
- `redis.delete(f"session:{session_id}")`
- `redis.hdel(f"user:{user_id}", session_id)`  ← remove from user index
- If no fields remain in `user:{user_id}`: `redis.delete(f"user:{user_id}")`

---

## 5. Interface — MemoryLayerBase

```python
"""
agent_core/src/interfaces/memory_layer.py

The only API Agent Core ever calls. Five methods.
Agent Core never touches Redis or Memgraph directly.
"""

from abc import ABC, abstractmethod
from typing import Any
from src.models import ContextBundle


class MemoryLayerBase(ABC):

    @abstractmethod
    def context_bundle(self, session_id: str, user_id: str) -> ContextBundle:
        """
        Called at the START of every turn.

        First call for a new session_id:
          - Checks Memgraph for user_id (returning user?)
          - Creates Journey node in Memgraph
          - Initialises Redis hash with default session state
          - Loads prior journey summary if returning user

        All subsequent calls:
          - Reads Redis hash (hot path — no Memgraph)
          - Reads UserProfile from Memgraph (declared fields + UserAttribute nodes)
          - Returns ContextBundle

        Returns empty ContextBundle on any failure. Never raises.
        """

    @abstractmethod
    def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """
        Called AFTER every turn (async daemon thread).

        scope="session"
          → Redis HSET on session:{session_id} + reset TTL

        scope="persistent"
          → Memgraph MERGE on the correct node, resolved from entity_map config
          → If key matches a declared field: write as property on the node
          → If key does not match any declared field: create/update UserAttribute child node

        scope="signal"
          → Create Signal node under ContextGraph
          → If structured extraction present: create ContextAttribute child nodes

        scope="journey_event"
          → Create/update Role or DropOff node under current Journey

        Never raises.
        """

    @abstractmethod
    def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """
        Called when a session ends:
          - termination_intent detected by NLU
          - SIGTERM received
          - Escalation to HITL

        Operations (in order):
          1. Read full Redis hash
          2. Execute merge_on_session_end rules from config
             → promotes session fields to Neo4j Journey node properties
          3. Close Journey node: SET ended_at, end_reason
          4. If consent=false:
             MATCH (u:User {user_id: $user_id}) DETACH DELETE u
             (erases all graph data for this user)
          5. redis.delete("session:{session_id}")
          6. redis.hdel("user:{user_id}", session_id)
          7. If no fields remain in user:{user_id}: redis.delete("user:{user_id}")

        Never raises.
        """

    @abstractmethod
    def get_active_sessions(self, user_id: str) -> list[dict]:
        """
        Called when a user reconnects, before any session is started.

        Reads user:{user_id} hash from Redis.
        For each session_id field:
          - Checks if session:{session_id} still exists in Redis (TTL not expired)
          - If expired: removes that field from user:{user_id} hash (lazy cleanup of stale fields)
        Returns list of alive sessions, sorted by last_accessed descending.

        Return format:
          [
            {
              "session_id": "<session_id>",
              "last_accessed": "2026-03-26T15:30:00Z"   # ISO-8601
            },
            ...
          ]

        Returns empty list [] if no active sessions exist or user key not found.
        Never raises.
        """

    @abstractmethod
    def delete_user(self, user_id: str) -> None:
        """
        DPDP right-to-erasure.
        MATCH (u:User {user_id: $user_id}) DETACH DELETE u
        Removes User + all subnodes (UserProfile, JourneyHistory, ContextGraph)
        and all their descendants via CASCADE.
        Also deletes user:{user_id} key from Redis.
        Never raises.
        """
```

---

## 6. Data Contracts

### 6.1 ContextBundle

ContextBundle is the **single object that `context_bundle()` returns to Agent Core at the start of every turn.**
Instead of Agent Core making separate calls to Redis and Memgraph itself, Memory Layer assembles everything into
one package and hands it over. Think of it as: *"everything the LLM needs to know about this user right now."*

Three fields, three different sources, three different time horizons:

| Field | Source | Time horizon | What it represents |
|---|---|---|---|
| `session` | Redis hash | This session only | What's happening right now — current node, question, signals, language |
| `profile` | Memgraph UserProfile | Across all sessions | Who this user is — trade, location, constraints, ad-hoc attributes |
| `journey` | Memgraph JourneyHistory | Last session only | What happened last time — roles offered, outcome, drop-off point |

**Why one object instead of separate calls:**
Without ContextBundle, Agent Core would have to call Redis directly, call Memgraph directly for profile,
call Memgraph again for journey summary, and know how to parse each store's response format. That violates
the design rule — Memory Layer stores, Agent Core orchestrates. Agent Core must not know anything about
Redis or Memgraph internals. `context_bundle()` is the single read boundary.

```python
# agent_core/src/models.py
# Replaces SessionState everywhere in Agent Core

@dataclass
class ContextBundle:
    session: dict         # full Redis hash — current session state
                          # always contains: user_id, journey_id, is_returning
                          # plus all domain session fields declared in domain.yaml

    profile: dict         # UserProfile declared fields + all UserAttribute nodes
                          # {
                          #   "<declared_field_1>": "<value>",
                          #   "<declared_field_2>": "<value>",
                          #   ...all fields from domain.yaml UserProfile.declared_fields...,
                          #   "attributes": [
                          #     {"key": "<ad_hoc_key>", "value": "<value>", "raw": "<user text>"}
                          #   ]
                          # }

    journey: dict | None  # Prior journey summary — only for returning users
                          # {
                          #   "outcomes": [...],          # domain-specific journey child nodes
                          #   "signals": [...],           # ContextGraph signals from last session
                          #   "end_reason": "...",
                          #   ...promoted session fields from merge_on_session_end config...
                          # }
                          # None for new users
```

### 6.2 How Agent Core Uses ContextBundle

| Step | Field used | Purpose |
|---|---|---|
| Workflow routing | `bundle.session["current_node"]` | Which branch to take |
| Consent check | `bundle.session["consent"]` | Gate for profile collection |
| NLU context | `bundle.session["current_question"]` + `bundle.session["current_node"]` | Resolve ambiguous intents |
| System prompt | `bundle.profile` + `bundle.session` | Build LLM context |
| Returning user context | `bundle.journey` | Brief LLM on prior session outcomes |
| HITL threshold | `bundle.session["loop_count"]` | Trigger escalation if loop_count >= threshold from config |
| Profile filters for KE | `bundle.profile["<declared_field>"]` | Entity filters for RAG — field names are domain-specific |

---

## 7. Config Schema

**File:** `memory_layer/config/domain.yaml`

```yaml
state:
  session:
    ttl_minutes: 60          # how long a Redis session lives without activity
    schema:
      # Infrastructure fields — always present, do not declare here:
      #   user_id, journey_id, is_returning
      #
      # Declare all domain-specific session fields below.
      # These are initialised with their defaults when a new session starts.
      <field_name>: { type: string,  default: "<value>" }
      <field_name>: { type: int,     default: 0 }
      <field_name>: { type: bool,    default: false }
      <field_name>: { type: enum,    values: [<value_1>, <value_2>, ...] }
      <field_name>: { type: list,    default: [] }
      # ... one entry per domain session field

  persistent:
    backend: memgraph
    graph:
      user_node:
        label: User
        key: user_id              # always user_id — opaque string from Reach Layer

      subnodes:
        UserProfile:
          rel: HAS_PROFILE
          declared_fields:
            - <field_name>        # domain-specific profile fields
            - <field_name>        # e.g. language, location, or any domain attribute
            # ... one entry per domain profile field
          adhoc:
            label: UserAttribute
            rel: HAS_ATTRIBUTE
            fields: [key, value, raw, turn, journey_id]

        JourneyHistory:
          rel: HAS_JOURNEY_HISTORY
          grouping: true            # no properties on this node
          child:
            label: Journey
            rel: JOURNEY
            fields: [journey_id, started_at, ended_at, end_reason]
            # Journey structural fields are fixed — domain adds extra fields via merge_on_session_end
            children:
              # Domain-specific outcome nodes per journey
              # Define as many child types as the domain needs
              - label: <OutcomeNodeLabel>     # e.g. "Role" for job placement, "Appointment" for health
                rel: <RELATIONSHIP_NAME>      # e.g. "OFFERED", "BOOKED"
                fields: [<field_1>, <field_2>, ...]

        ContextGraph:
          rel: HAS_CONTEXT
          grouping: true            # no properties on this node
          child:
            label: Signal
            rel: SIGNAL
            fields: [type, turn, raw, journey_id]
            adhoc:
              label: ContextAttribute
              rel: HAS_ATTRIBUTE
              fields: [key, value, raw, turn, journey_id]

    merge_on_session_end:
      # Promotes session fields to Journey node properties on flush_session()
      # Use this for session outcome fields whose final value should be recorded on the Journey
      - session_field: <field_name>
        target: Journey.<property_name>
      # ... one entry per session outcome field to promote
```

**SCOPE_MAP** — compiled at boot from config, used by `write()` to route fields:

```python
# Built once at startup from domain.yaml
# Agent Core uses this to determine scope before calling write()
SCOPE_MAP = {
    # Infrastructure fields (always session)
    "user_id":           "session",
    "journey_id":        "session",
    "is_returning":      "session",

    # Domain session fields (from domain.yaml state.session.schema)
    "<field_name>":      "session",   # one entry per declared session field
    # ...

    # Domain persistent fields (from domain.yaml persistent.subnodes.UserProfile.declared_fields)
    "<field_name>":      "persistent",  # one entry per declared profile field
    # ...

    # Signal (Memgraph ContextGraph — always this key)
    "signal":            "signal",

    # Journey events (from domain.yaml JourneyHistory.child.children)
    "<event_key>":       "journey_event",  # one entry per journey child type
    # ...
}
# Any key NOT in SCOPE_MAP → treated as ad-hoc → scope="persistent", stored as UserAttribute
```

---

## 8. Operation Flows

### 8.1 New User — First Turn

```
1. Agent Core calls context_bundle(session_id, user_id)
2. Memory Layer: redis.exists("session:{session_id}") → false
3. Memory Layer: memgraph.find_user(user_id) → None  (new user)
4. Memory Layer: memgraph.create_user_graph(user_id)
   → CREATE (u:User)
   → CREATE (u)-[:HAS_PROFILE]->(up:UserProfile {user_id})
   → CREATE (u)-[:HAS_JOURNEY_HISTORY]->(jh:JourneyHistory)
   → CREATE (u)-[:HAS_CONTEXT]->(cg:ContextGraph)
5. Memory Layer: memgraph.create_journey(user_id, journey_id)
   → MATCH (jh:JourneyHistory)<-[:HAS_JOURNEY_HISTORY]-(u:User {user_id})
   → CREATE (jh)-[:JOURNEY]->(j:Journey {journey_id, started_at})
6. Memory Layer: redis.hset("session:{session_id}", initial_state)
   → user_id=user_id, journey_id=journey_id, is_returning="false"
   → all domain session fields initialised from domain.yaml defaults
7. Memory Layer: redis.expire("session:{session_id}", ttl)
8. Memory Layer: redis.hset("user:{user_id}", session_id, now_iso8601())  ← register in user index
9. Memory Layer: redis.expire("user:{user_id}", ttl)                       ← set user key TTL
10. Returns ContextBundle(session=initial_state, profile={user_id: user_id}, journey=None)
```

### 8.2 Returning User — Session Resumes

```
1. Agent Core calls context_bundle(session_id, user_id)
2. Memory Layer: redis.exists → false (new session_id for new session)
3. Memory Layer: memgraph.find_user(user_id) → User node found
4. Memory Layer: memgraph.create_journey(user_id, journey_id)  (new Journey node)
5. Memory Layer: memgraph.get_profile(user_id)
   → reads UserProfile declared fields + all UserAttribute nodes
6. Memory Layer: memgraph.get_last_journey_summary(user_id)
   → reads last Journey + its JourneyChild nodes + Signal nodes
   → builds journey summary dict
7. Memory Layer: redis.hset with profile data pre-populated, is_returning="true"
   → declared profile fields copied into Redis for fast turn-by-turn access
8. Memory Layer: redis.expire("session:{session_id}", ttl)
9. Memory Layer: redis.hset("user:{user_id}", session_id, now_iso8601())  ← register in user index
10. Memory Layer: redis.expire("user:{user_id}", ttl)                      ← set/reset user key TTL
11. Returns ContextBundle(
       session={user_id: "...", journey_id: "...", is_returning: "true", ...domain fields...},
       profile={<declared_field_1>: "...", ..., attributes: [...]},
       journey={outcomes: [...], signals: [...], end_reason: "..."}
    )
```

### 8.3 After Every Turn (Async Write)

```
Agent Core (daemon thread):
  for key, value in state_updates.items():
      scope = SCOPE_MAP.get(key, "persistent")   # unknown keys → persistent as UserAttribute
      memory.write(session_id, user_id, scope, key, value)

Memory Layer:
  scope="session"      → redis.hset("session:{session_id}", key, value) + reset TTL
                          redis.hset("user:{user_id}", session_id, now_iso8601())  ← update last_accessed
                          redis.expire("user:{user_id}", ttl_seconds)              ← reset user key TTL
  scope="persistent"   → memgraph.upsert_profile_field(user_id, key, value)
                          if key in declared_fields: SET up.{key} = $value
                          else: MERGE UserAttribute node
  scope="signal"       → memgraph.create_signal(user_id, journey_id, value)
  scope="journey_event"→ memgraph.create_journey_child(user_id, journey_id, key, value)
```

### 8.4 Session End (flush_session)

```
Agent Core calls flush_session(session_id, user_id, end_reason)
  when: nlu_result.intent == "termination_intent"
     or: was_escalated == True
     or: SIGTERM received

Memory Layer:
  1. session = redis.hgetall("session:{session_id}")
  2. For each rule in merge_on_session_end config:
       val = session.get(rule["session_field"])
       if val: memgraph.upsert_path(user_id, journey_id, rule["target"], val)
  3. memgraph.close_journey(user_id, journey_id, end_reason)
       → MATCH Journey {journey_id} SET ended_at=$now, end_reason=$reason
  4. if session["consent"] == "false":
       memgraph.delete_user(user_id)
       → MATCH (u:User {user_id: $user_id}) DETACH DELETE u
  5. redis.delete("session:{session_id}")
  6. redis.hdel("user:{user_id}", session_id)          ← remove from user index
  7. if redis.hlen("user:{user_id}") == 0:
       redis.delete("user:{user_id}")                  ← clean up user key if no sessions left
```

### 8.5 DPDP Erasure

```
Agent Core calls memory.delete_user(user_id)

Memory Layer:
  MATCH (u:User {user_id: $user_id}) DETACH DELETE u
  → Cascades through all subnodes and their descendants
  redis.delete("user:{user_id}")
  → Removes user session index
```

### 8.6 Reconnecting User — Active Sessions Found

```
User reconnects (new inbound message with user_id, no session_id yet)

1. Agent Core calls memory.get_active_sessions(user_id)
2. Memory Layer:
     reads user:{user_id} hash
     for each session_id field:
       if redis.exists("session:{session_id}") → false:
         redis.hdel("user:{user_id}", session_id)   ← lazy cleanup of stale fields
     returns sorted list of alive sessions with last_accessed timestamps (most recent first)

3. Agent Core takes sessions[0] — the most recently accessed session.
   Reach Layer presents it to the user:
     "You have an active session from 2026-03-26 15:30. Continue or start fresh?"
     [Continue]  [Start fresh]

4a. User chooses Continue:
     Agent Core calls context_bundle(sessions[0].session_id, user_id)
     Memory Layer: redis.exists("session:{session_id}") → true
     → reads Redis hash directly (hot path — no Memgraph init needed)
     → resets TTL: redis.expire("session:{session_id}", ttl_seconds)
     → updates last_accessed: redis.hset("user:{user_id}", session_id, now_iso8601())
     → Returns ContextBundle with current session state restored

4b. User chooses Start fresh:
     Agent Core generates a new session_id
     → falls through to flow 8.1 (new session init)
     → new session registered in user:{user_id} index
     → old sessions remain alive with their own TTLs (not deleted)

Note: All active sessions are stored in the index. Only the most recently accessed
is surfaced to the user. Future deployments can choose to show the full list
without any change to the Memory Layer.
```

### 8.7 Reconnecting User — No Active Sessions

```
User reconnects (user_id known, no active sessions in Redis)

1. Agent Core calls memory.get_active_sessions(user_id)
2. Memory Layer:
     reads user:{user_id} hash → all session_id fields expired (or key itself expired)
     → removes all dead fields (lazy cleanup)
     → returns []

3. Agent Core sees empty list → starts new session flow (8.1 or 8.2)
   memgraph.find_user(user_id):
     → User node exists: is_returning=true (returning user, prior journeys in Memgraph)
     → User node absent: is_returning=false (brand new user)
```

---

## 9. Implementation Components

| Task | Component | File | What it does |
|---|---|---|---|
| A1 | `ContextBundle` dataclass | `agent_core/src/models.py` | Replaces `SessionState` |
| B1 | `MemoryLayerBase` (5 methods) | `agent_core/src/interfaces/memory_layer.py` | New interface |
| K1 | `RedisSessionStore` | `memory_layer/src/session_store.py` | HSET/HGETALL/DELETE/EXPIRE on session keys; HSET/HDEL/HGETALL/DELETE on user index key (`user:{user_id}`) |
| K2 | `GraphUserStore` | `memory_layer/src/graph_user_store.py` | CREATE user graph, get/upsert UserProfile + UserAttribute |
| K3 | `GraphJourneyStore` | `memory_layer/src/graph_journey_store.py` | CREATE/CLOSE Journey, get last journey summary, Role/DropOff nodes |
| K4 | `GraphContextStore` | `memory_layer/src/graph_context_store.py` | CREATE Signal + ContextAttribute nodes |
| K5 | `MemoryLayer` | `memory_layer/src/memory_layer.py` | Implements MemoryLayerBase. Orchestrates K1–K4. |
| K6 | `domain.yaml` | `memory_layer/config/domain.yaml` | Full KKB config (schema above) |
| K7 | `MemoryLayerServer` | `memory_layer/src/server.py` | FastAPI — 4 endpoints mirroring the 4 methods |
| D1 | `HttpMemoryClient` | `agent_core/src/http_clients/memory_layer.py` | HTTP client implementing MemoryLayerBase |
| G1 | Orchestrator — turn start | `agent_core/src/orchestrator.py` | Call `context_bundle()` replacing `read_session()` |
| G2 | Orchestrator — routing | `agent_core/src/orchestrator.py` | Workflow routing from `bundle.session["current_node"]` |
| G3 | Orchestrator — prompt | `agent_core/src/orchestrator.py` | Pass `bundle.profile` + `bundle.session` to `build_system_prompt()` |
| G4 | Orchestrator — messages | `agent_core/src/orchestrator.py` | `build_messages()` — no history, RAG chunks only |
| G5 | Orchestrator — async write | `agent_core/src/orchestrator.py` | Async `write()` loop replacing `write_session()` |
| G6 | Orchestrator — session end | `agent_core/src/orchestrator.py` | `flush_session()` on termination/escalation |
| G7 | Orchestrator — HITL | `agent_core/src/orchestrator.py` | Bypass LLM when `loop_count >= threshold` from config |

---

## 10. FastAPI Endpoints (Memory Layer Server — port 8002)

| Method | Endpoint | Calls | Request | Response |
|---|---|---|---|---|
| POST | `/context_bundle` | `memory.context_bundle()` | `{session_id, user_id}` | `ContextBundle` as JSON |
| POST | `/write` | `memory.write()` | `{session_id, user_id, scope, key, value}` | `{status: "ok"}` |
| POST | `/flush_session` | `memory.flush_session()` | `{session_id, user_id, end_reason}` | `{status: "ok"}` |
| GET | `/sessions/{user_id}` | `memory.get_active_sessions()` | — | `[{session_id, last_accessed}, ...]` |
| DELETE | `/user/{user_id}` | `memory.delete_user()` | — | `{status: "ok"}` |
| GET | `/health` | — | — | `{status: "ok"}` |

---

## 11. Responsibility Split

| Layer | Responsibility |
|---|---|
| Agent Core (LLM) | Extracts declared fields + ad-hoc attributes from user input |
| Agent Core (Orchestrator) | Resolves scope from SCOPE_MAP, calls `write()` per field |
| Agent Core (Orchestrator) | On reconnect: calls `get_active_sessions()`, presents options to user, routes to `context_bundle()` for a chosen session or starts a new one |
| Memory Layer | Receives key/value/scope, stores in Redis or Neo4j. No extraction logic. |
| Memory Layer | Maintains `user:{user_id}` index — registers sessions on start, updates `last_accessed` on turn, removes entries on flush, lazy-cleans expired entries on reconnect |

---

## 12. Next Track — After Memory Layer Is Complete

**KGExternal — Knowledge Engine market knowledge graph:**

This will be implemented as a separate track immediately after the Memory Layer is done.

- Memgraph for structured ONEST market data: `(Job)-[:REQUIRES_TRADE]→(Trade)`, `(Job)-[:LOCATED_IN]→(District)`
- ChromaDB stays for document RAG (labour schemes, trade descriptions)
- KE retrieval uses both: structured Memgraph query + semantic ChromaDB search, results merged before returning chunks
- This is a KE change — does not affect the Memory Layer interface or implementation
- Requires a separate design pass before implementation begins

**Broadcast scope** — pub/sub fan-out. No dependency from current design. Defer indefinitely.

---

## 13. KKB Deployment Example

> This section shows what `domain.yaml` looks like for the KKB (Kaam Ki Baat) labour advisory deployment.
> This is domain-specific configuration — it is not part of the generic Memory Layer design.

```yaml
state:
  session:
    ttl_minutes: 2880    # 2 days — allows users to continue sessions across days
    schema:
      current_node:      { type: string,  default: "awaiting_consent" }
      mental_state:      { type: enum,    values: [fog, orientation, evaluation, commitment, follow_through] }
      market_signal:     { type: enum,    values: [strong, weak, absent] }
      skill_match:       { type: enum,    values: [direct, partial, gap] }
      income_urgency:    { type: enum,    values: [immediate, short_term, flexible] }
      collection_round:  { type: int,     default: 0 }
      current_question:  { type: string,  default: "" }
      options_presented: { type: list,    default: [] }
      loop_count:        { type: int,     default: 0 }
      consent:           { type: bool,    default: false }

  persistent:
    backend: memgraph
    graph:
      user_node:
        label: User
        key: user_id              # for KKB: user_id = phone number (set by Reach Layer config)

      subnodes:
        UserProfile:
          rel: HAS_PROFILE
          declared_fields:
            - language
            - trade
            - education
            - location
            - experience_years
            - consent_flag
            - anonymous_flag
          adhoc:
            label: UserAttribute
            rel: HAS_ATTRIBUTE
            fields: [key, value, raw, turn, journey_id]

        JourneyHistory:
          rel: HAS_JOURNEY_HISTORY
          grouping: true
          child:
            label: Journey
            rel: JOURNEY
            fields: [journey_id, started_at, ended_at, end_reason]
            children:
              - label: Role
                rel: OFFERED
                fields: [role_id, title, employer, pay_min, pay_max, shortlisted, committed, follow_up_outcome]
              - label: DropOff
                rel: DROPPED_AT
                fields: [node, reason, timestamp]

        ContextGraph:
          rel: HAS_CONTEXT
          grouping: true
          child:
            label: Signal
            rel: SIGNAL
            fields: [type, turn, raw, journey_id]
            adhoc:
              label: ContextAttribute
              rel: HAS_ATTRIBUTE
              fields: [key, value, raw, turn, journey_id]

    merge_on_session_end:
      - session_field: mental_state
        target: Journey.mental_state_at_end
      - session_field: market_signal
        target: Journey.branch_taken
      - session_field: options_presented
        target: Role                        # creates OFFERED edges for each role_id in the list

  reengagement:
    triggers:
      - event: DOP_MT              # drop-off after market truth
        delay_hours: 72
        channel: outbound_call
        message_template: kkb_reengagement_mt
      - event: DOP_EG              # employer ghost
        delay_hours: 72
        channel: outbound_call
        message_template: kkb_employer_ghost
      - event: DOP_RL              # evaluation loop
        loop_threshold: 3
        action: hitl_counsellor
```

**KKB SCOPE_MAP** (compiled from the above config):

```python
SCOPE_MAP = {
    # infrastructure
    "user_id":           "session",
    "journey_id":        "session",
    "is_returning":      "session",

    # session fields
    "current_node":      "session",
    "mental_state":      "session",
    "market_signal":     "session",
    "skill_match":       "session",
    "income_urgency":    "session",
    "collection_round":  "session",
    "current_question":  "session",
    "options_presented": "session",
    "loop_count":        "session",
    "consent":           "session",

    # persistent profile fields
    "trade":             "persistent",
    "education":         "persistent",
    "location":          "persistent",
    "experience_years":  "persistent",
    "language":          "persistent",
    "anonymous_flag":    "persistent",

    # signal
    "signal":            "signal",

    # journey events
    "role_offered":      "journey_event",
    "drop_off":          "journey_event",
}
```
