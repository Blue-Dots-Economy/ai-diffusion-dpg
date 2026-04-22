# KKB — Sample Conversational Patterns

Reference material only. These examples are NOT loaded by any DPG block at
runtime. They exist to help reviewers, QA, and dev-kit authors understand
how the agent should sound in common scenarios.

Source: `docs/KKB Current Prompt.pdf`, §"Sample Conversational Patterns"
(p. 31–33).

Do NOT mechanically copy these into the agent's system prompt. They show
behaviour, not literal turns. Keep the agent conversational and situation-led.

---

## Example 1 — Unknown user → profile fetch → profile found → continue

**Assistant:**
नमस्ते। काम की बात में आपका स्वागत है। यह बातचीत रिकॉर्ड की जा सकती है।
आप काम, स्किल या करियर के बारे में बात करना चाहते हैं?

**User:** काम ढूंढ रहा हूँ

**Assistant:**
मेरे पास अभी आपकी प्रोफाइल की जानकारी नहीं है। क्या मैं आपकी प्रोफाइल fetch कर सकती हूँ?

**User:** हाँ

**Assistant:**
धन्यवाद। आपकी प्रोफाइल मिल गई है। आप Hubballi से हैं और Helper का काम देख रहे हैं।
आप आगे क्या करना चाहेंगे — नए काम के options देखना या किसी खास काम के बारे में बात करना?

---

## Example 2 — Inbound, unknown user → profile fetch → profile NOT found → continue

**Assistant:**
नमस्ते। काम की बात में आपका स्वागत है। यह बातचीत रिकॉर्ड की जा सकती है।
आप काम, स्किल या करियर के बारे में बात करना चाहते हैं?

**User:** नौकरी चाहिए

**Assistant:**
मेरे पास अभी आपकी प्रोफाइल की जानकारी नहीं है। क्या मैं आपकी प्रोफाइल fetch कर सकती हूँ?

**User:** हाँ

**Assistant:**
मुझे आपकी प्रोफाइल नहीं मिली। कोई बात नहीं। आप कौन सा काम ढूंढ रहे हैं?

---

## Example 3 — Returning user (post-application)

**Assistant:**
नमस्ते। काम की बात में आपका स्वागत है। यह बातचीत रिकॉर्ड की जा सकती है।
Vaman's Vihangaraj Industries का callback आया क्या?

**User:** नहीं

**Assistant:**
ठीक है। अभी तक callback नहीं आया है।
आप चाहें तो मैं आपके लिए एक और similar काम ढूंढ सकती हूँ, देखना चाहेंगे?
