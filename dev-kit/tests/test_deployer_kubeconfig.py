import pytest
import yaml
from dev_kit.agent.deployer.kubeconfig import parse_kubeconfig


VALID_KUBECONFIG = yaml.dump({
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [{"name": "test-cluster", "cluster": {"server": "https://127.0.0.1:6443"}}],
    "contexts": [{"name": "test-ctx", "context": {"cluster": "test-cluster", "user": "test-user"}}],
    "current-context": "test-ctx",
    "users": [{"name": "test-user", "user": {"token": "fake-token"}}],
})


def test_parse_valid_kubeconfig():
    result = parse_kubeconfig(VALID_KUBECONFIG)
    assert result["cluster_name"] == "test-cluster"
    assert result["server"] == "https://127.0.0.1:6443"
    assert result["current_context"] == "test-ctx"


def test_parse_invalid_yaml():
    with pytest.raises(ValueError, match="Invalid"):
        parse_kubeconfig("not: valid: yaml: {{")


def test_parse_missing_clusters():
    bad = yaml.dump({"apiVersion": "v1", "kind": "Config"})
    with pytest.raises(ValueError, match="clusters"):
        parse_kubeconfig(bad)


def test_parse_wrong_kind():
    bad = yaml.dump({"apiVersion": "v1", "kind": "Secret"})
    with pytest.raises(ValueError, match="kind"):
        parse_kubeconfig(bad)
