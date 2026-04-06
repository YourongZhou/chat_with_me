import os

import pytest

from social_persona_skill.models import Platform
from social_persona_skill.workflow import PersonaWorkflow


X_URL = "https://x.com/karpathy"
XIAOHONGSHU_URL = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"


@pytest.mark.live
def test_x_live_create(tmp_path) -> None:
    workflow = PersonaWorkflow(storage_dir=tmp_path / "personas", runtime_root=".runtime")
    if not workflow.layout.backend_python(Platform.X).exists():
        pytest.skip("bootstrap the X backend before running live tests")
    result, saved_dir = workflow.create_persona([X_URL])

    assert len(result.person.accounts) == 1
    assert result.person.accounts[0].platform is Platform.X
    assert (saved_dir / "corpora" / "x").exists()
    assert any(item.item_type == "post" for item in result.corpora[X_URL])


@pytest.mark.live
def test_xiaohongshu_live_create(tmp_path) -> None:
    workflow = PersonaWorkflow(storage_dir=tmp_path / "personas", runtime_root=".runtime")
    if not workflow.layout.backend_python(Platform.XIAOHONGSHU).exists():
        pytest.skip("bootstrap the Xiaohongshu backend before running live tests")
    if not workflow.layout.has_xiaohongshu_login_state():
        pytest.skip("Xiaohongshu login state is missing")
    result, saved_dir = workflow.create_persona([XIAOHONGSHU_URL])

    assert len(result.person.accounts) == 1
    assert result.person.accounts[0].platform is Platform.XIAOHONGSHU
    assert (saved_dir / "corpora" / "xiaohongshu").exists()
    assert any(item.item_type in {"bio", "post"} for item in result.corpora[XIAOHONGSHU_URL])
