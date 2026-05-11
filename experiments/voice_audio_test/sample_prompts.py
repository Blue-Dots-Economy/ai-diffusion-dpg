"""Curated Hindi demo prompts for voice_test.py.

Each prompt is designed to exercise a different aspect of Hindi voice
quality. Run via `voice_test.py --prompt N --play` to use one without
having to type Hindi every time.

Add or edit freely — the runner picks by 1-based index.
"""

SAMPLE_PROMPTS = [
    {
        "id": 1,
        "label": "Famous person — Narendra Modi biography",
        "text": "Narendra Modi के बारे में 6-8 sentences में बताओ। उनका जन्म, बचपन, political journey, और India के Prime Minister बनने तक का सफर।",
        "tests": "Proper nouns, historical facts, factual recall in Hindi",
    },
    {
        "id": 2,
        "label": "Travel — top tourist places in India",
        "text": "India के top 5 tourist places कौन से हैं? हर जगह के बारे में 1-2 lines में बताओ कि क्यों famous है।",
        "tests": "Place names, descriptive vocabulary, listing format",
    },
    {
        "id": 3,
        "label": "Festival — story behind Diwali",
        "text": "Diwali क्यों मनाई जाती है? Lord Rama की कहानी सुनाओ — पूरा पता लगे कि यह festival क्यों इतना important है।",
        "tests": "Cultural narrative, religious vocabulary, storytelling tone",
    },
    {
        "id": 4,
        "label": "How-to — masala chai recipe",
        "text": "Perfect masala chai बनाने का तरीका बताओ। Step by step ingredients और method दोनों।",
        "tests": "Imperative voice, step-by-step instruction, ingredient names",
    },
    {
        "id": 5,
        "label": "Children's story — long narrative for clarity test",
        "text": "मुझे एक छोटी बच्चों की कहानी सुनाओ Hindi में — कोई moral lesson के साथ। शेर और चूहे जैसी पुरानी कहानी हो सकती है। 8-10 sentences में पूरी सुनाओ।",
        "tests": "Narrative flow, prosody, vowel length, natural pauses, story tone",
    },
    {
        "id": 6,
        "label": "Science — Sun vs Moon explained simply",
        "text": "Sun और Moon में क्या-क्या फर्क है? Simple language में बताओ ताकि एक बच्चा भी समझ जाए।",
        "tests": "Scientific terms in Hindi, simple explanatory tone",
    },
    {
        "id": 7,
        "label": "Sports — Sachin Tendulkar's records",
        "text": "Sachin Tendulkar के बारे में बताओ। उनके सबसे बड़े cricket records कौन-कौन से हैं? कुछ खास matches का mention करो।",
        "tests": "Numbers, sports vocabulary, multiple proper nouns",
    },
    {
        "id": 8,
        "label": "Cinema — why Sholay is iconic",
        "text": "Sholay movie इतनी famous क्यों है? कुछ memorable dialogues और characters का mention करो जो आज भी लोग याद करते हैं।",
        "tests": "Cultural reference, dialogue quoting, conversational register",
    },
    {
        "id": 9,
        "label": "Health — managing daily stress",
        "text": "रोज़ की tension और stress कैसे manage करें? कुछ practical, simple tips दो जो कोई भी अपनी life में easily implement कर सके।",
        "tests": "Empathetic + practical tone, advice register, listing",
    },
    {
        "id": 10,
        "label": "Mythology — short Krishna story",
        "text": "Lord Krishna की एक छोटी सी कहानी सुनाओ — उनके बचपन की कोई leela। 6-8 sentences में, naturally सुनाओ जैसे कोई दादी अपने पोते को सुना रही हो।",
        "tests": "Narrative warmth, Sanskrit-rooted vocabulary, storytelling cadence",
    },
]


def get_prompt(index: int) -> dict:
    """Return the prompt at 1-based index. Raises IndexError if out of range."""
    if index < 1 or index > len(SAMPLE_PROMPTS):
        raise IndexError(
            f"Prompt {index} doesn't exist. Available: 1–{len(SAMPLE_PROMPTS)}."
        )
    return SAMPLE_PROMPTS[index - 1]


def list_prompts() -> str:
    """Return a formatted list of all prompts for printing."""
    lines = ["Available demo prompts:\n"]
    for p in SAMPLE_PROMPTS:
        lines.append(f"  [{p['id']:>2}] {p['label']}")
        lines.append(f"       Tests: {p['tests']}")
        # Truncate long text for the listing
        preview = p["text"] if len(p["text"]) <= 80 else p["text"][:77] + "..."
        lines.append(f"       \"{preview}\"")
        lines.append("")
    return "\n".join(lines)
