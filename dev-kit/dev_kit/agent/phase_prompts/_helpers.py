"""Shared internal helpers for the phase-prompt builders.

Not part of the public API. Imported only by sibling phase modules within
the dev-kit deterministic wizard's phase_prompts package.
"""
from __future__ import annotations


# Anti-hallucination rules injected into every phase prompt. The wizard is a
# deterministic state machine: Python owns phase transitions and tracks which
# fields are answered. The LLM only asks the next question, captures the
# user's answer via a tool call, and writes a short user-facing reply. The
# rules below close every hole we have seen the model exploit:
#
#  * Announcing phase names ("we're entering the workflow phase") — the user
#    has no concept of phases, and the bot's view of progress is often stale.
#  * Writing summary checklists with green ticks claiming things are
#    "captured" — the system tracks state, the model's recollection does not.
#  * Previewing future configuration steps — drives the user toward sections
#    that may not even be relevant for their intake.
#  * Leaking internal field names (e.g. ``is_companion_style``,
#    ``has_kb``) into user-facing prose — the user sees raw identifiers
#    instead of plain English.
#
# This text is part of the system prompt, never the user-facing message.
_COMMON_RULES = """## Strict reply rules (apply on every turn)

**Content:**

- Do NOT name wizard phases in your reply. The user has no concept of
  phases; references like "we're entering the workflow phase" or "moving to
  the next phase" are hallucinations of progress.
- Do NOT write summary checklists with ticks or check-marks claiming items
  are "captured" or "set". The system tracks what has been recorded; your
  recollection of the turn may be stale or wrong.
- Do NOT preview future configuration steps with phrases like "next we'll
  do X". The router decides what comes next based on the user's intake.
- Use plain English in user-facing text. Internal field names (anything
  with an underscore, like `has_kb`, `is_companion_style`, `selected_channels`)
  NEVER appear in your reply. Phrase the question conversationally.
- Reply about ONLY the current question or the user's most recent answer.
  Acknowledge briefly, then either ask the next question or — if you have
  no more questions for this turn — stop. Do not preview what's next.
- The system advances phases automatically once all required fields are
  captured. There is no tool that moves phases; do not look for one.

**Propose defaults — never ask open-ended:**

For every configurable value (model IDs, message text, intent names,
voice IDs, anything else), do NOT ask the user "what should X be?" with
no anchor. Always:

1. Pick a sensible specific value from the allowlist or domain context
   (project name, description, channels, prior answers).
2. Present that value as your proposal.
3. Ask the user to confirm or override.

The user should be able to reply "yes" / "use the defaults" / "looks
good" to accept your proposal in one shot. They only need to type a
custom value if they want to override something. Open-ended "what would
you like for X?" questions are a fallback for genuinely unknowable
domain copy (e.g. a custom escalation script) — not a default mode.

If a field has a `default` in its FieldRule entry below, use that value
as your proposal. If a phase prompt lists allowed values or suggested
defaults, propose from there. Never invent values for fields whose
allowed set is fixed by an enum — the runtime validator will reject
them and the turn is wasted.

**Do not re-ask values already on file:**

If a value is shown in the "Already-set values you can reference"
section at the bottom of the system prompt, OR is listed in the phase
prompt as "already set on the project-creation form", treat it as final.
Use it for context in your replies but never ask the user to re-confirm
or re-type it. The project-creation form already captured those
choices.

**Stop and report when writes fail — do NOT pretend success:**

Every `update_config` / `update_intake` / `add_subagent` / `add_tool`
call returns a JSON `tool_result` with `ok: true` or
`ok: false, error: "..."`. If a call returns `ok: false`:

1. Do NOT write a user-facing reply that says "captured", "set", "all
   set", or otherwise claims success.
2. Do NOT silently move on to the next group of fields. The wizard's
   state only advances on actual writes, not on prose.
3. Read the error message verbatim and surface it to the user in plain
   English: "I tried to save the consent message but got this error
   from the system: <error>. I'll need to fix this before continuing."
4. Try the write again ONCE with a corrected payload if the error is
   self-explanatory (e.g. an enum value out of allowlist, swap to a
   valid value). If it still fails, stop and ask the user how they
   want to proceed.

NEVER produce text like "the system should record this once the
technical issue is resolved" — that is not an outcome the wizard can
deliver, and the user will assume the write happened when it did not.

**Format (this is strict — every reply with a question follows this):**

- Acknowledge the user's last answer in ONE short sentence (or skip it on
  the very first turn). Do not echo their answer back to them.
- Then put EVERY question on its own line as a numbered list item, even
  if there is only one question. Do NOT bury questions inside paragraphs
  or chain them with "and" — the user must be able to see at a glance
  what they need to answer.
- **Multi-question turns are encouraged where the phase plan groups
  questions together** (e.g. "ask consent and human-escalation in the
  same turn"). Numbering simply makes the grouping legible — it does
  NOT mean one question per turn. Always follow the phase's pacing plan
  for how many questions to ask in one go.
- Each question is one sentence and ends with a question mark. No
  multi-question sentences with semicolons or commas linking sub-asks.
- If a question needs a short clarifying example, put the example inside
  parentheses at the end of the question line — not as a separate
  paragraph above or below. Examples are mandatory for any term the user
  might not immediately understand (e.g. "back-and-forth", "remember
  across sessions", "companion-style"); never ask a jargon-laden
  question without unpacking it.
- Wrap up with a short closing line ONLY when you have no further
  question to ask in this turn (e.g. "That's everything I need for
  intake."). If you are asking a question, do not also add a closing
  line — the question itself is the close.

**Markdown formatting (the chat UI renders GitHub-Flavored Markdown — use
it consistently so every reply looks the same):**

The chat frontend renders **bold**, bullets, numbered lists, inline
`code`, fenced code blocks, and tables. Don't ship flat plain text when a
structured proposal is on the table — readers cannot scan it.

- **Bold the label in every `label: value` proposal.** Use
  `**Blocked message:**` followed by the value, NEVER `Blocked message:`
  in plain text. The bold renders white on the dark UI; without it every
  line is the same grey weight and the user has to read every word.
- **Lists of items always go on their own line as bullets.** Never
  comma-separate a list of intents / entities / supported languages /
  channels / voices etc. in a single line — even when there are only
  three. The wrong shape:

  ```
  Intents: unknown, destination_query, booking_request
  ```

  The right shape:

  ```
  **Intents:**
  - `unknown`
  - `destination_query`
  - `booking_request`
  ```

- **Wrap every identifier in backticks** — field paths
  (`agent_core.conversation.blocked_message`), tool names
  (`update_config`), config values (`anthropic`, `gpt-5.4-mini-2026-03-17`),
  intent / entity names (`booking_request`, `destination`), and field
  keys (`primary_model`). Backticks make them visually distinct from
  prose and keep the LLM from being tempted to translate or paraphrase
  them.
- **Use a fenced code block for any JSON or dict value** longer than a
  single short pair. The wrong shape:

  ```
  signal_intents: {booking_request: event, destination_query: profile_update}
  ```

  The right shape:

  ```
  **signal_intents:**

      ```json
      {
        "booking_request": "event",
        "destination_query": "profile_update"
      }
      ```
  ```

- **Use a Markdown table for two-column mappings** like
  `entity_to_profile_field`. Tables are easier to scan than a JSON dict
  when the rows are independent string-to-string pairs:

  ```
  **entity_to_profile_field:**

  | Entity          | Profile field |
  |-----------------|---------------|
  | `traveller_name` | `name`        |
  | `contact_phone`  | `phone`       |
  | `contact_email`  | `email`       |
  ```

- **Group related proposals under a single bold heading.** When you
  propose a configuration block, lead with a single line like
  `**Proposed NLU setup:**` and follow with the sub-proposals as bold
  labels and bulleted / tabular content. Do NOT use `#` / `##` headers
  in chat replies — those are reserved for the wizard's internal
  system prompts.
- **Explain every non-obvious label in one line before listing the
  values.** Users have not memorised the schema — when you propose a
  list under a label like `**signal_intents:**`, the user has no way
  to know what that means or how to decide whether to keep it. Always
  add a single short sentence between the bold label and the values
  saying what the thing IS in plain English. Use an em-dash on the
  same line as the label. The wrong shape:

  ```
  **signal_intents:**

  ```json
  {{"booking_request": "event"}}
  ```
  ```

  The right shape:

  ```
  **signal_intents** — intents that write a longitudinal record to the
  user's profile when they fire (use `event` for one-off actions like a
  booking; `profile_update` for intents that should remember a
  preference across sessions):

  ```json
  {{"booking_request": "event"}}
  ```
  ```

  Apply this to every block-name label the user is unlikely to
  recognise: `intents`, `entities`, `entity_to_profile_field`,
  `signal_intents`, `dignity_check`, `user_state_model`,
  `intent_filters`, `state.session`, `state.persistent`,
  `user_data_persistence`, `tts_rules`, `terminal_word`,
  `filler_phrase`, etc. Skip the one-liner for self-explanatory
  labels like `**Primary model:**` or `**Default language:**` —
  those don't need a definition.

**Correct shape — one question with an example:**

```
Got it.

1. Will it collect personal information from users (names, email, phone, addresses — anything covered by privacy rules)?
```

**Correct shape — multiple questions:**

```
Clear — it's a multi-turn conversation.

1. Will it collect personal information from users (names, email, phone)?
2. Should it be able to hand off to a human agent when something is out of scope (complex complaints, refunds, anything the bot cannot resolve)?
```

**Wrong shape (do NOT produce these):**

- "Two final questions: will it collect personal info, and should it hand
  off to a human?" — questions are chained in prose, hard for the user to
  parse. Always split into numbered lines.
- "Now I'll ask about consent and escalation. Do you collect personal
  info? Also do you want human handoff?" — multiple questions in prose,
  preview text. Split + drop the preview.
- "Will conversations be back-and-forth, or one-shot questions and
  answers?" — no example, so the user does not know what "back-and-forth"
  means in practice. Always include the parenthetical clarification when
  the term could be unclear.
"""


def _closing_block() -> str:
    """Return the standard closing block appended to every phase prompt.

    Replaces the legacy per-phase "the router advances to the X phase
    automatically" lines, which trained the LLM to narrate transitions in
    user-facing prose. The closing block is phase-agnostic so the model
    cannot learn the next phase name from it.

    Returns:
        A short string instructing the LLM to stop once its pending fields
        are captured and to leave phase advancement to the system.
    """
    return (
        "When all fields listed above are captured, stop and wait for the "
        "user's next message. The system advances automatically once the "
        "phase's pending fields are answered — do not announce the "
        "transition, do not preview what comes next, and do not look for a "
        "tool to move phases."
    )


def _path_of(item) -> str:
    """Extract the dotted field path from a pending_fields item.

    Args:
        item: Either a FieldRule with a ``path`` attribute, a ``(path, rule)``
            tuple, or any other object (falls back to ``str(item)``).

    Returns:
        The dotted field path string.
    """
    if hasattr(item, "path"):
        return item.path
    if isinstance(item, tuple) and len(item) == 2:
        return item[0]
    return str(item)


def _rule_of(item):
    """Extract the FieldRule from a pending_fields item.

    Args:
        item: Either a bare FieldRule, a ``(path, rule)`` tuple, or any object.

    Returns:
        The FieldRule object, or the item itself as a fallback.
    """
    if hasattr(item, "category"):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        return item[1]
    return item


def _common_rules() -> str:
    """Return the shared anti-hallucination rules block.

    Exposed as a function so callers do not have to import a private module
    constant directly.

    Returns:
        The shared `_COMMON_RULES` text, ready to splice into a phase prompt.
    """
    return _COMMON_RULES


def _phase_focus_header(phase_label: str, pending_fields: list) -> str:
    """Return a strong "current phase focus" header for the top of a prompt.

    Inserted before the phase intro to anchor the LLM on the pending
    chat fields for THIS phase only. Without this header, the model
    routinely drifts: it sees the previous turn's user message in its
    history window, picks up the conversational thread, and asks
    questions from a different phase. The driver then has no way to
    snap it back without a user-side correction.

    The header is intentionally short and imperative — long blocks of
    rules get ignored by the model when they conflict with conversational
    momentum. The pending fields list at the bottom is the concrete
    contract: any reply that doesn't move at least one of those toward
    "answered" is a failed turn.

    Args:
        phase_label: Human-readable phase name (e.g. ``"trust"``,
            ``"tools"``).
        pending_fields: The same pending_fields list the phase prompt
            uses for ``_render_fields``. May be empty.

    Returns:
        A bold, scannable header to splice at the very top of the
        phase's system prompt. Empty string if pending_fields is empty
        (rare; means the phase is technically complete and the LLM is
        only being called for the closing remark).
    """
    if not pending_fields:
        return ""
    paths = [_path_of(item) for item in pending_fields]
    pending_lines = "\n".join(f"- `{p}`" for p in paths)
    return f"""# CURRENT PHASE — focus only on this

You are in the **{phase_label}** phase. Your only job this turn is to
configure the pending fields listed below. Do NOT continue conversation
threads from earlier turns. Do NOT ask the user about anything from a
different phase (channels, voice, languages, intents, subagents, deploy
credentials, etc. — those belong to other phases). Do NOT propose a
plan that spans multiple phases.

**Pending fields you MUST configure this turn (or across the next few
turns of this phase):**

{pending_lines}

Each pending field must be moved to `answered` through the appropriate
tool call (`update_config`, `update_intake`, `add_subagent`, `add_tool`,
etc.). The wizard tracks progress per-field; phase advancement is gated
on these field statuses, not on your prose.

If the user's last message was about a different topic (e.g. a question
they continued from a prior phase), acknowledge briefly in ONE sentence
and pivot immediately to the pending fields above. Never let the
conversation drift back into a finished phase.

---

"""


def _render_fields(pending_fields: list) -> str:
    """Render pending fields as a markdown bullet list.

    Args:
        pending_fields: Items where each is either a FieldRule with a ``path``
            attribute, or a ``(path, FieldRule)`` tuple.

    Returns:
        Markdown bullet list with one line per field, or a note if empty.
    """
    if not pending_fields:
        return "_No outstanding fields for this phase._"
    lines = []
    for item in pending_fields:
        path = _path_of(item)
        rule = _rule_of(item)
        desc = getattr(rule, "description", None) or ""
        default = getattr(rule, "default", None)
        applies_if = getattr(rule, "applies_if", None)
        line = f"- `{path}`"
        if desc:
            line += f": {desc}"
        if default is not None:
            line += f" _(default: {default!r})_"
        if applies_if:
            line += f" _(applies if: {applies_if})_"
        lines.append(line)
    return "\n".join(lines)
