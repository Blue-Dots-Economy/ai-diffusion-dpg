"""Blue Dots Action Gateway URLs come from SIGNALS_* env vars with today's UAT values as defaults."""
import re
from pathlib import Path

import yaml

AG = Path(__file__).resolve().parents[2] / "dev-kit/configs/blue-dots/action_gateway.yaml"
VARS = {"SIGNALS_BASE_URL": "https://signals.bluedotseconomy.org",
        "SIGNALS_SEARCH_URL": "https://signals.bluedotseconomy.org/signals-search",
        "SIGNALS_INSTANCE_URL": "https://signals.bluedotseconomy.org"}
PH = re.compile(r"^\$\{(\w+):-([^}]*)\}$")


def _urls():
    cfg = yaml.safe_load(AG.read_text(encoding="utf-8"))
    for tool in cfg["tools"]:
        if tool.get("base_url"):
            yield tool["id"], "base_url", tool["base_url"]
        params = list(tool.get("params") or [])
        for endpoint in tool.get("endpoints") or []:
            params += endpoint.get("params") or []
        for p in params:
            if p.get("name") == "instance_url" and p.get("source") == "static":
                yield tool["id"], "instance_url", p["value"]


def test_every_signals_url_uses_a_signals_var():
    seen = list(_urls())
    assert seen, "no URLs found"
    assert {f for _, f, _ in seen} == {"base_url", "instance_url"}
    for tool, field, value in seen:
        m = PH.match(value)
        assert m and m.group(1) in VARS, f"{tool}.{field} = {value!r}"


def test_blue_dots_urls_default_to_uat():
    for tool, field, value in _urls():
        var, default = PH.match(value).groups()
        assert default == VARS[var], f"{tool}.{field} default {default!r} != {VARS[var]!r}"
