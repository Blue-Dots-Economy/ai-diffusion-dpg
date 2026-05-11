"""Hindi system prompts for the test harness.

Three variations let us see whether prompt length or style materially
affects latency. Selected at server startup via the PROMPT_NAME env var.
"""
from __future__ import annotations


PROMPTS: dict[str, str] = {
    "SHORT_HINDI": (
        "तुम Hindi में जवाब दो। User की बात सुनकर 1-2 short sentences में "
        "naturally reply करो। English में switch मत करो।"
    ),
    "KKB_PERSONA": (
        "तुम काम की बात हो — Indian workers के लिए एक calm, grounded, "
        "fact-based female voice guide. हमेशा Hindi में बोलो। 1-2 short "
        "sentences में reply दो। Practical, steady, respectful tone रखो। "
        "Never bureaucratic, never promotional."
    ),
    "STRICT_HINDI_ONLY": (
        "Reply ONLY in Hindi (Devanagari + native words). Never reply in "
        "English. Even if the user code-switches, you stay in Hindi. "
        "Max 2 sentences. No English words unless absolutely necessary."
    ),
}


def get_prompt(name: str) -> str:
    """Return the prompt text for a given name.

    Args:
        name: One of the keys in PROMPTS.

    Returns:
        The prompt string.

    Raises:
        KeyError: If name is not a registered prompt. The error message
            lists the available names.
    """
    if name not in PROMPTS:
        raise KeyError(
            f"Unknown prompt name '{name}'. Available: "
            f"{', '.join(sorted(PROMPTS.keys()))}"
        )
    return PROMPTS[name]
