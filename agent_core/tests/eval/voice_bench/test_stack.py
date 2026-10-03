import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from eval.voice_bench.config import TargetCfg
from eval.voice_bench.stack import PatchMismatch, StackError, TargetStack, apply_patch, compose_override

PATCH = yaml.safe_load((Path(__file__).parents[3] / "eval/voice_bench/patches/blue-dots-local.yaml").read_text())


def _ag(host, n_base=3):
    lines = [f'    base_url: "{host}"' for _ in range(n_base)]
    return "\n".join(["# header mentions https://dev-signals.serveirc.com in a comment", *lines,
                      f'    base_url: "{host}/signals-search"', f'            value: "{host}"', ""])


@pytest.mark.parametrize("host", ["https://dev-signals.serveirc.com", "https://signals.bluedotseconomy.org"])
def test_apply_patch_both_hosts(host):
    out = apply_patch(_ag(host), PATCH, "http://host.docker.internal:18742", "http://signals-api:2742")
    assert out.count('base_url: "http://host.docker.internal:18742"') == 3
    assert 'base_url: "http://host.docker.internal:18742/signals-search"' in out
    assert 'value: "http://signals-api:2742"' in out and f'base_url: "{host}' not in out


def test_apply_patch_count_mismatch_is_reported():
    with pytest.raises(PatchMismatch, match="expected 3, found 2"):
        apply_patch(_ag("https://signals.bluedotseconomy.org", n_base=2), PATCH, "http://t", "http://i")


def test_apply_patch_rejects_no_or_two_hosts():
    with pytest.raises(PatchMismatch, match="single upstream host"):
        apply_patch('base_url: "https://example.org"', PATCH, "http://t", "http://i")


TAP = "http://host.docker.internal:18742"
INST = "http://signals-api:2742"


def test_compose_override_points_action_gateway_at_local_signals(tmp_path):
    y_str, _ = compose_override(18008, tmp_path / "bd.env", TAP, INST)
    env = yaml.safe_load(y_str)["services"]["action_gateway"]["environment"]
    assert f"SIGNALS_BASE_URL={TAP}" in env
    assert f"SIGNALS_SEARCH_URL={TAP}/signals-search" in env
    assert f"SIGNALS_INSTANCE_URL={INST}" in env


def test_compose_override_shape(tmp_path):
    y_str, secret = compose_override(18008, tmp_path / "bd.env", TAP, INST)
    y = yaml.safe_load(y_str)
    assert y["services"]["reach_layer_bridge"]["ports"] == ["127.0.0.1:18008:8008"]
    assert y["services"]["reach_layer_bridge"]["volumes"] == ["../../dev-kit/dpg/reach_layer.yaml:/app/reach_layer/bridge/config/dpg.yaml:ro"]
    assert y["services"]["action_gateway"]["env_file"] == [str(tmp_path / "bd.env")]
    assert "host.docker.internal:host-gateway" in y["services"]["action_gateway"]["extra_hosts"]
    assert y["services"]["memgraph"]["image"] == "memgraph/memgraph:2.17.0"


def test_compose_override_includes_memory_layer_secret(tmp_path):
    """Compose override must set TOOL_RESULT_KEY_SECRET on memory_layer."""
    y_str, secret = compose_override(18008, tmp_path / "bd.env", TAP, INST)
    y = yaml.safe_load(y_str)
    env = y["services"]["memory_layer"]["environment"]
    assert any(e.startswith("TOOL_RESULT_KEY_SECRET=") for e in env)
    secret_entry = next(e for e in env if e.startswith("TOOL_RESULT_KEY_SECRET="))
    secret_from_yaml = secret_entry.split("=", 1)[1]
    assert len(secret_from_yaml) == 64 and all(c in "0123456789abcdef" for c in secret_from_yaml)
    assert secret_from_yaml == secret


def test_compose_override_generates_different_secrets(tmp_path):
    """Two calls to compose_override must generate different secrets."""
    y_str1, secret1 = compose_override(18008, tmp_path / "bd.env", TAP, INST)
    y_str2, secret2 = compose_override(18008, tmp_path / "bd.env", TAP, INST)
    y1 = yaml.safe_load(y_str1)
    y2 = yaml.safe_load(y_str2)
    secret1_from_yaml = next(e.split("=", 1)[1] for e in y1["services"]["memory_layer"]["environment"]
                   if e.startswith("TOOL_RESULT_KEY_SECRET="))
    secret2_from_yaml = next(e.split("=", 1)[1] for e in y2["services"]["memory_layer"]["environment"]
                   if e.startswith("TOOL_RESULT_KEY_SECRET="))
    assert secret1 != secret2
    assert secret1_from_yaml == secret1
    assert secret2_from_yaml == secret2


def test_tool_result_secret_redacted_in_compose_failure(tmp_path, monkeypatch):
    """When docker compose fails, the tool_result_secret is redacted in the error."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-key-12345")
    (tmp_path / "bd.env").write_text("BLUE_DOTS_API_KEY=blue-api-secret-xyz\n")

    class FailOnComposeRun(FakeRun):
        def __call__(self, argv, **kw):
            if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
                return SimpleNamespace(returncode=0, stdout="abc1234\n", stderr="")
            if "worktree" in argv and "add" in argv:
                wt = Path(argv[argv.index("--detach") + 1])
                (wt / "dev-kit/configs/blue-dots").mkdir(parents=True)
                (wt / "dev-kit/configs/blue-dots/action_gateway.yaml").write_text(_ag("https://signals.bluedotseconomy.org"))
                (wt / "automation/docker").mkdir(parents=True)
            # Fail compose up with a message containing secrets from env
            if argv[0] == "docker" and "compose" in argv and "up" in argv:
                self.stderr = "docker error: auth failed with blue-api-secret-xyz and sk-secret-key-12345"
                return SimpleNamespace(returncode=1, stdout="", stderr=self.stderr)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    run = FailOnComposeRun(tmp_path)
    s = _stack(tmp_path, run)
    with pytest.raises(StackError) as ei:
        s.up()

    msg = str(ei.value)
    # The error message should redact secrets from OPENAI_API_KEY and env_file
    assert "***" in msg
    # The original secrets should not appear in the error message
    assert "blue-api-secret-xyz" not in msg
    assert "sk-secret-key-12345" not in msg


def test_patch_applies_cleanly_to_every_milestone_ref():
    """Review Focus #1 against real history: each milestone's action_gateway.yaml patches with the expected counts."""
    root = Path(__file__).resolve().parents[4]
    for ref in ("edf7ec8", "6b38e48", "cf794ef"):
        r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:dev-kit/configs/blue-dots/action_gateway.yaml"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"{ref} not in this clone")
        apply_patch(r.stdout, PATCH, "http://t", "http://i")


# ---- TargetStack lifecycle (fake run, fake health transport) ----

COMPOSE = "automation/docker/docker-compose.yml"


class FakeRun:
    def __init__(self, tmp_path, fail_on=None, stderr="boom"):
        self.calls, self.envs, self.tmp, self.fail_on, self.stderr = [], [], tmp_path, fail_on, stderr

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.envs.append(kw.get("env"))
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return SimpleNamespace(returncode=0, stdout="abc1234\n", stderr="")
        if "worktree" in argv and "add" in argv:  # emulate the checkout
            wt = Path(argv[argv.index("--detach") + 1])
            (wt / "dev-kit/configs/blue-dots").mkdir(parents=True)
            (wt / "dev-kit/configs/blue-dots/action_gateway.yaml").write_text(_ag("https://signals.bluedotseconomy.org"))
            (wt / "automation/docker").mkdir(parents=True)
        if self.fail_on and self.fail_on in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr=self.stderr)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _client(status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status)))


def _stack(tmp_path, run, target=None, client=None, **kw):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return TargetStack(target or TargetCfg(name="M1", git_ref="6b38e48"), repo, tmp_path / "work",
                       "http://host.docker.internal:18742", "http://signals-api:2742", tmp_path / "bd.env",
                       run=run, client=client or _client(), sleep=lambda s: None, **kw)


def test_up_creates_worktree_patches_and_composes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    run = FakeRun(tmp_path)
    s = _stack(tmp_path, run)
    assert s.up() == "http://127.0.0.1:18008"
    wt = tmp_path / "work" / "M1-abc1234"
    assert s.worktree == wt and s.commit == "abc1234"
    assert ["git", "-C", str(tmp_path / "repo"), "worktree", "add", "--detach", str(wt), "6b38e48"] in run.calls
    up = next(c for c in run.calls if "up" in c)
    assert up[:3] == ["docker", "compose", "-p"] and up[3] == "vb"
    assert up.count("-f") == 2 and str(wt / COMPOSE) in up
    assert str(wt / "automation/docker/voice-bench.override.yml") in up
    assert up[-4:] == ["up", "-d", "--build", "reach_layer_bridge"]
    env = run.envs[run.calls.index(up)]
    assert env["DOMAIN"] == "blue-dots" and env["GIT_SHA"] == "abc1234" and env["OPENAI_API_KEY"] == "sk-secret"
    patched = (wt / "dev-kit/configs/blue-dots/action_gateway.yaml").read_text()
    assert 'base_url: "http://host.docker.internal:18742"' in patched
    ov = yaml.safe_load((wt / "automation/docker/voice-bench.override.yml").read_text())
    assert ov["services"]["action_gateway"]["env_file"] == [str((tmp_path / "bd.env").resolve())]


def test_up_skips_patch_when_ref_has_env_expand(tmp_path):
    class ExpandRun(FakeRun):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "worktree" in argv and "add" in argv:
                wt = Path(argv[argv.index("--detach") + 1])
                (wt / "action_gateway/src/config").mkdir(parents=True)
                (wt / "action_gateway/src/config/env_expand.py").write_text("")
            return r
    run = ExpandRun(tmp_path)
    _stack(tmp_path, run).up()
    wt = tmp_path / "work" / "M1-abc1234"
    # untouched: still the upstream URL, not the tap URL
    assert 'base_url: "https://signals.bluedotseconomy.org"' in (
        wt / "dev-kit/configs/blue-dots/action_gateway.yaml").read_text()
    ov = yaml.safe_load((wt / "automation/docker/voice-bench.override.yml").read_text())
    assert "SIGNALS_BASE_URL=http://host.docker.internal:18742" in ov["services"]["action_gateway"]["environment"]


def test_compose_failure_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    (tmp_path / "bd.env").write_text("BLUE_DOTS_API_KEY=key-12345\nBLUE_DOTS_ORG_ID=org-9\n")
    run = FakeRun(tmp_path, fail_on="up", stderr="bad line BLUE_DOTS_API_KEY=key-12345 sk-secret")
    s = _stack(tmp_path, run)
    with pytest.raises(StackError) as ei:
        s.up()
    msg = str(ei.value)
    assert "***" in msg and "key-12345" not in msg and "sk-secret" not in msg
    assert any("worktree" in c and "remove" in c for c in run.calls)


def test_patch_mismatch_up_no_compose_and_cleaned(tmp_path):
    class BadRun(FakeRun):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "worktree" in argv and "add" in argv:
                wt = Path(argv[argv.index("--detach") + 1])
                (wt / "dev-kit/configs/blue-dots/action_gateway.yaml").write_text('base_url: "https://example.org"')
            return r
    run = BadRun(tmp_path)
    with pytest.raises(StackError, match="patch does not apply"):
        _stack(tmp_path, run).up()
    assert not any("compose" in c for c in run.calls if c[0] == "docker" and "up" in c)
    assert any("worktree" in c and "remove" in c for c in run.calls)


def test_down_is_best_effort(tmp_path):
    def run(argv, **kw):
        if argv[0] == "docker":
            raise OSError("no docker")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    s = _stack(tmp_path, FakeRun(tmp_path))
    s.up()
    s._run = run
    assert s.down() == ["compose down"]


def test_invalid_target_name_rejected(tmp_path):
    s = _stack(tmp_path, FakeRun(tmp_path), target=TargetCfg(name="../x", git_ref="abc"))
    with pytest.raises(StackError, match="invalid target name"):
        s.up()


def test_health_timeout_raises(tmp_path):
    s = _stack(tmp_path, FakeRun(tmp_path), client=_client(503), health_timeout_s=9, poll_interval_s=3)
    with pytest.raises(StackError, match="not healthy"):
        s.up()


def test_down_removes_only_the_worktree(tmp_path):
    run = FakeRun(tmp_path)
    s = _stack(tmp_path, run)
    s.up()
    run.calls.clear()
    s.down()
    assert "down" in run.calls[0] and "-v" in run.calls[0] and run.calls[0][:4] == ["docker", "compose", "-p", "vb"]
    wt = str(tmp_path / "work" / "M1-abc1234")
    assert run.calls[1] == ["git", "-C", str(tmp_path / "repo"), "worktree", "remove", "--force", wt]


def test_bridge_url_target_only_health_checks(tmp_path):
    url = "http://vm.example:8008"
    run = FakeRun(tmp_path)
    s = _stack(tmp_path, run, target=TargetCfg(name="VM", bridge_url=url))
    assert s.commit == "external-" + hashlib.sha1(url.encode()).hexdigest()[:7]
    assert s.up() == url
    s.down()
    assert run.calls == []


def test_apply_patch_rejects_a_leftover_upstream_host():
    """M4: a non-comment line still naming an upstream host after patching (a 6th site) is a mismatch."""
    host = "https://signals.bluedotseconomy.org"
    extra = _ag(host) + f'    docs_url: "{host}/docs"\n'
    with pytest.raises(PatchMismatch, match="upstream host still present"):
        apply_patch(extra, PATCH, "http://t", "http://i")
    other = _ag(host) + '    fallback: "https://dev-signals.serveirc.com"\n'      # the other known host
    with pytest.raises(PatchMismatch, match="upstream host still present"):
        apply_patch(other, PATCH, "http://t", "http://i")
    commented = _ag(host) + f'    x: 1   # was {host}\n'
    assert host not in apply_patch(commented, PATCH, "http://t", "http://i").split("#")[0]
