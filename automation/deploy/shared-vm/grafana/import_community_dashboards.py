#!/usr/bin/env python3
"""Fetch the pinned grafana.com community dashboards used by the shared VM.

Deployment tooling for the shared-VM Grafana, not part of any DPG block. Each
dashboard is downloaded at a fixed revision and normalised for file
provisioning, then written to ``grafana/dashboards/``. The output is
committed, so a deploy never needs internet access to grafana.com; rerun
this only to bump a revision or add a dashboard.

Normalisation:
  * ``${DS_*}`` import placeholders become a ``${datasource}`` template
    variable, because the provisioned datasources have no fixed UID.
  * A stable ``uid``, a "DPG - " title, the ``dpg`` tag and a header link to
    every other DPG dashboard are set, so the set reads as one product.
  * Saved variable selections from the author's environment are cleared.

Standard library only. Usage (from this directory)::

    python3 import_community_dashboards.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "dashboards"
URL = "https://grafana.com/api/dashboards/{id}/revisions/{rev}/download"
TIMEOUT_S = 30

# grafana.com id, pinned revision, output uid, title, datasource type, and a
# regex over panel queries: panels whose every query matches are dropped
# because they can never show data on this deployment.
#
# 15983 is pinned at rev21 (tested against collector contrib v0.98, nearest
# to our 0.96). Later revisions use quoted UTF-8 label names, which need
# Prometheus 3.x; this deployment runs 2.50.
DASHBOARDS = [
    # All probes are plain HTTP inside dpg_net: no certificates, no TLS.
    (14928, 6, "dpg-health-probes", "DPG - Health Probes (detail)", "prometheus",
     r"probe_ssl_earliest_cert_expiry|probe_tls_version_info|probe_http_ssl"),
    (15798, 16, "dpg-containers", "DPG - Containers", "prometheus", None),
    (763, 6, "dpg-redis", "DPG - Redis", "prometheus", None),
    # Not Kubernetes; and on 0.96 only memory_limiter emits the per-processor
    # accepted/refused/dropped series, which this pipeline does not use.
    (15983, 21, "dpg-otel-collector", "DPG - OTel Collector", "prometheus",
     r"otelcol_otelsvc_k8s_|otelcol_processor_(accepted|refused|dropped)_"),
    (13639, 2, "dpg-logs", "DPG - Logs", "loki", None),
]

# Per-dashboard (old, new) substitutions applied to query expressions.
REWRITES = {
    # The DPG services ship logs as OTLP JSON records, not logfmt: parse the
    # JSON and show the message (plus the error, when there is one) instead
    # of the raw record. Every field stays available in the expanded line.
    13639: [(
        "| logfmt",
        '| json | line_format "{{.body}}{{if .attributes_error}}  ::  {{.attributes_error}}{{end}}"',
    )],
    # Memory gauge divides by maxmemory, which is 0 (unlimited) here, so it
    # read "∞%". Fall back to the host's memory (cAdvisor) when unset.
    763: [(
        'sum(100 * (redis_memory_used_bytes{instance=~"$instance"}  / redis_memory_max_bytes{instance=~"$instance"}))',
        '100 * sum(redis_memory_used_bytes{instance=~"$instance"}) / (sum(redis_memory_max_bytes{instance=~"$instance"} > 0) or sum(machine_memory_bytes))',
    )],
}

# Per-dashboard template-variable overrides, merged into the variable.
HIDDEN = {"hide": 2}
VAR_OVERRIDES = {
    # One job and one host on a single Docker VM: the pickers only add noise.
    15798: {"job": HIDDEN, "node": HIDDEN},
    763: {"namespace": HIDDEN},
    # A per-probe drill-down: its stat panels print one value per selected
    # target, so "All" (18 probes) overprints into an unreadable block. Pick
    # one probe at a time; the Service Status dashboard is the all-up view.
    14928: {"target": {"includeAll": False, "multi": False}},
}

DS_PLACEHOLDER = re.compile(r"\$\{DS_[A-Za-z0-9_-]+\}")

NAV_LINK = {
    "type": "dashboards",
    "title": "DPG dashboards",
    "tags": ["dpg"],
    "asDropdown": True,
    "includeVars": False,
    "keepTime": True,
}


def _replace_placeholders(node: object) -> object:
    """Return ``node`` with every ``${DS_*}`` placeholder rewritten.

    Args:
        node: Any JSON value from a dashboard document.

    Returns:
        The same structure with import placeholders pointing at
        ``${datasource}``.
    """
    if isinstance(node, dict):
        return {k: _replace_placeholders(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_placeholders(v) for v in node]
    if isinstance(node, str):
        return DS_PLACEHOLDER.sub("${datasource}", node)
    return node


def _rewrite_exprs(panels: list, old: str, new: str) -> None:
    """Replace ``old`` with ``new`` in every query expression, in place.

    Args:
        panels: A dashboard's ``panels`` list; nested row panels included.
        old: Substring to replace.
        new: Replacement text.
    """
    for panel in panels:
        _rewrite_exprs(panel.get("panels") or [], old, new)
        for target in panel.get("targets") or []:
            if target.get("expr"):
                target["expr"] = target["expr"].replace(old, new)


def _disable_exemplars(panels: list) -> None:
    """Turn off exemplar queries on every target, in place.

    Prometheus here runs without ``--enable-feature=exemplar-storage``, so
    each exemplar request fails and Grafana logs an error on every refresh.

    Args:
        panels: A dashboard's ``panels`` list; nested row panels included.
    """
    for panel in panels:
        _disable_exemplars(panel.get("panels") or [])
        for target in panel.get("targets") or []:
            if target.get("exemplar"):
                target["exemplar"] = False


def _drop_panels(panels: list, drop: re.Pattern) -> list:
    """Remove panels whose every query matches ``drop``, then empty rows.

    Handles both row layouts: collapsed rows carry their panels nested, and
    expanded rows are followed by their panels in the flat list.

    Args:
        panels: A dashboard's top-level ``panels`` list.
        drop: Pattern tested against each panel's query expressions.

    Returns:
        The filtered panel list.
    """
    def keep(panel: dict) -> bool:
        exprs = [t.get("expr") or "" for t in panel.get("targets") or []]
        return not exprs or not all(drop.search(e) for e in exprs)

    kept = []
    for panel in panels:
        if panel.get("type") == "row":
            panel["panels"] = [p for p in panel.get("panels") or [] if keep(p)]
            kept.append(panel)
        elif keep(panel):
            kept.append(panel)

    # A row is empty when it has no nested panels and the next entry is
    # another row (or the end of the list).
    result = []
    for i, panel in enumerate(kept):
        if panel.get("type") == "row" and not panel["panels"]:
            nxt = kept[i + 1] if i + 1 < len(kept) else None
            if nxt is None or nxt.get("type") == "row":
                continue
        result.append(panel)
    return result


def normalise(raw: dict, uid: str, title: str, ds_type: str, source: str, drop: str | None) -> dict:
    """Adapt a downloaded dashboard for file provisioning on the shared VM.

    Args:
        raw: Dashboard JSON as served by grafana.com.
        uid: Stable dashboard UID to assign.
        title: Display title to assign.
        ds_type: Datasource plugin type the dashboard queries.
        source: Human-readable provenance, appended to the description.
        drop: Optional regex; panels whose every query matches are removed.

    Returns:
        The provisioning-ready dashboard.
    """
    dash = _replace_placeholders(raw)
    if drop:
        dash["panels"] = _drop_panels(dash.get("panels") or [], re.compile(drop))
    for key in ("__inputs", "__requires", "__elements", "id", "iteration"):
        dash.pop(key, None)
    dash.update(uid=uid, title=title, editable=False)
    # One default window across the set, so switching dashboards from the
    # header link (which keeps the time range) never lands on a 24h squash.
    dash["time"] = {"from": "now-1h", "to": "now"}
    description = (dash.get("description") or "").strip()
    dash["description"] = f"{description} [{source}]".strip()
    dash["tags"] = sorted(set(dash.get("tags") or []) | {"dpg"})
    dash["links"] = [NAV_LINK]

    templating = dash.setdefault("templating", {}).setdefault("list", [])
    if not any(v.get("type") == "datasource" and v.get("name") == "datasource" for v in templating):
        templating.insert(0, {"name": "datasource", "label": "Data source", "type": "datasource", "query": ds_type, "hide": 2})
    for var in templating:
        if var.get("type") in ("query", "datasource"):
            var.pop("current", None)
        if var.get("type") == "query":
            var.pop("options", None)
            var["refresh"] = 2  # re-resolve on time-range change, not just load
    return dash


def main() -> int:
    """Download, normalise and write every pinned dashboard.

    Returns:
        Process exit status: 0 on success, 1 if any download failed.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failed = 0
    for gid, rev, uid, title, ds_type, drop in DASHBOARDS:
        url = URL.format(id=gid, rev=rev)
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                raw = json.load(resp)
        except (OSError, ValueError) as exc:
            print(f"FAIL {gid} rev{rev}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
            continue
        source = f"grafana.com dashboard {gid} rev{rev}"
        dash = normalise(raw, uid, title, ds_type, source, drop)
        for old, new in REWRITES.get(gid, []):
            _rewrite_exprs(dash.get("panels") or [], old, new)
        _disable_exemplars(dash.get("panels") or [])
        overrides = VAR_OVERRIDES.get(gid, {})
        for var in dash["templating"]["list"]:
            var.update(overrides.get(var.get("name"), {}))
        out = OUT_DIR / f"{uid.removeprefix('dpg-')}.json"
        out.write_text(json.dumps(dash, indent=2) + "\n")
        print(f"ok   {gid} rev{rev} -> {out.name}  ({title})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
