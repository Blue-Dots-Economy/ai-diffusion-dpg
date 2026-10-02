import json

import httpx

from eval.voice_bench.bridge import BridgeClient


def _sse(*chunks, done=True):
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _c(delta, finish=None):
    return {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _client(body: bytes, status=200, capture=None):
    def h(req):
        if capture is not None:
            capture.append(json.loads(req.content))
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})
    return BridgeClient("http://bridge", ["एक मिनट।"], ["धन्यवाद", "Thank you"], transport=httpx.MockTransport(h))


def test_turn_splits_status_phrase_and_text():
    sent = []
    cl = _client(_sse(_c({"role": "assistant", "content": ""}), _c({"content": "एक मिनट।"}),
                      _c({"content": "लखनऊ में दो नौकरियाँ हैं।"}), _c({"content": " पहली ठीक लगी?"}),
                      _c({}, "stop")), capture=sent)
    t = cl.turn("नौकरी चाहिए", "919900001000", "vb-T01-0-a")
    assert t.status_phrase == "एक मिनट।" and t.reply == "लखनऊ में दो नौकरियाँ हैं। पहली ठीक लगी?"
    assert t.t_first_content_ms is not None and t.t_first_reply_ms >= t.t_first_content_ms
    assert not t.session_ended and t.error is None
    body = sent[0]
    assert body["metadata"] == {"caller_phone": "919900001000", "call_id": "vb-T01-0-a"}
    assert body["tools"][0]["function"]["name"] == "end_conversation" and body["stream"] is True


def test_hangup_tool_call_marks_session_end():
    cl = _client(_sse(_c({"content": "धन्यवाद, नमस्ते।"}), _c({"content": " धन्यवाद"}),
                      _c({"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                                          "function": {"name": "end_conversation", "arguments": "{}"}}]}),
                      _c({}, "tool_calls")))
    assert cl.turn("बस", "919900001000", "c").session_ended


def test_m0_terminal_word_marks_session_end():
    cl = _client(_sse(_c({"content": "आपका दिन शुभ हो।"}), _c({"content": " Thank you"}), _c({}, "stop")))
    t = cl.turn("बस", "919900001000", "c")
    assert t.session_ended and t.reply.endswith("Thank you")


def test_http_error_and_truncated_stream_are_errors_not_raises():
    assert _client(b'{"error": {}}', status=502).turn("x", "919900001000", "c").error == "http_502"
    t = _client(_sse(_c({"content": "आधा"}), done=False)).turn("x", "919900001000", "c")
    assert t.error == "stream_truncated" and t.reply == "आधा"
