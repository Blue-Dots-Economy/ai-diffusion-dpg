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


def test_compose_override_shape(tmp_path):
    y = yaml.safe_load(compose_override(18008, tmp_path / "bd.env"))
    assert y["services"]["reach_layer_bridge"]["ports"] == ["127.0.0.1:18008:8008"]
    assert y["services"]["action_gateway"]["env_file"] == [str(tmp_path / "bd.env")]
    assert "host.docker.internal:host-gateway" in y["services"]["action_gateway"]["extra_hosts"]


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


def test_compose_failure_raises_stack_error_without_env_values(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    run = FakeRun(tmp_path, fail_on="up", stderr="build error: no space left")
    s = _stack(tmp_path, run)
    with pytest.raises(StackError, match="no space left") as ei:
        s.up()
    assert "sk-secret" not in str(ei.value)
    assert any("worktree" in c and "remove" in c for c in run.calls)  # cleaned up after failed up


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
