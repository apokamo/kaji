"""Tests for kaji_harness.providers.normalize_id and ResolvedId.

phase3-design.md § Small / normalize_id 全パターン に対応。
"""

from __future__ import annotations

import pytest

from kaji_harness.providers import ResolvedId, normalize_id

pytestmark = pytest.mark.small


class TestGitHubProvider:
    def test_numeric_input(self) -> None:
        rid = normalize_id("153", provider_name="github", machine_id=None)
        assert rid == ResolvedId(kind="github", value="153", raw="153")

    def test_gh_prefix_input(self) -> None:
        rid = normalize_id("gh:153", provider_name="github", machine_id=None)
        assert rid.kind == "github"
        assert rid.value == "153"

    def test_local_form_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires provider.type='local'"):
            normalize_id("local-pc1-3", provider_name="github", machine_id=None)


class TestLocalProvider:
    def test_local_full_form(self) -> None:
        rid = normalize_id("local-pc1-3", provider_name="local", machine_id="pc1")
        assert rid == ResolvedId(kind="local", value="local-pc1-3", raw="local-pc1-3")

    def test_local_short_form_expands(self) -> None:
        rid = normalize_id("pc2-7", provider_name="local", machine_id="pc1")
        assert rid.kind == "local"
        assert rid.value == "local-pc2-7"

    def test_numeric_uses_local_machine_id(self) -> None:
        rid = normalize_id("5", provider_name="local", machine_id="pc1")
        assert rid.kind == "local"
        assert rid.value == "local-pc1-5"

    def test_numeric_without_machine_id_errors(self) -> None:
        with pytest.raises(ValueError, match="provider.local.machine_id"):
            normalize_id("5", provider_name="local", machine_id=None)

    def test_numeric_with_invalid_machine_id_errors(self) -> None:
        with pytest.raises(ValueError, match="invalid machine_id"):
            normalize_id("5", provider_name="local", machine_id="PC-1")

    def test_gh_prefix_returns_remote_cache(self) -> None:
        rid = normalize_id("gh:42", provider_name="local", machine_id="pc1")
        assert rid.kind == "remote_cache"
        assert rid.value == "42"


class TestErrors:
    def test_empty_input(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            normalize_id("", provider_name="github", machine_id=None)

    def test_unknown_provider(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            normalize_id("153", provider_name="bitbucket", machine_id=None)

    def test_invalid_form(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("foo bar", provider_name="local", machine_id="pc1")

    def test_uppercase_machine_segment_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("PC1-3", provider_name="local", machine_id="pc1")


class TestGitLabRejected:
    """GitLab forge 撤去後の bridging test: ``gl:N`` / ``provider_name='gitlab'`` は reject。"""

    def test_gitlab_provider_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            normalize_id("42", provider_name="gitlab", machine_id=None)

    def test_gl_prefix_rejected_under_github(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("gl:42", provider_name="github", machine_id=None)

    def test_gl_prefix_rejected_under_local(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("gl:42", provider_name="local", machine_id="pc1")


class TestPositiveIntGrammar:
    """Issue 番号は 1 始まり整数のみ。0 / leading zero / gh:0 を拒否する。"""

    def test_zero_rejected_github(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("0", provider_name="github", machine_id=None)

    def test_gh_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("gh:0", provider_name="local", machine_id="pc1")

    def test_leading_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("007", provider_name="github", machine_id=None)

    def test_gh_leading_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("gh:01", provider_name="local", machine_id="pc1")

    def test_local_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("local-pc1-0", provider_name="local", machine_id="pc1")

    def test_local_leading_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid issue id"):
            normalize_id("local-pc1-007", provider_name="local", machine_id="pc1")
