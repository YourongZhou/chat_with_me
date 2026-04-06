import pytest

from social_persona_skill.models import Platform
from social_persona_skill.workflow import PersonaWorkflow


X_URL = "https://x.com/karpathy"
XIAOHONGSHU_URL = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"


@pytest.mark.live
def test_x_live_skill_build(tmp_path) -> None:
    workflow = PersonaWorkflow(storage_dir=tmp_path / "personas", runtime_root=".runtime")
    if not workflow.layout.backend_python(Platform.X).exists():
        pytest.skip("bootstrap the X backend before running live tests")

    created, saved_dir = workflow.create_persona([X_URL])
    built = workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")

    assert (saved_dir / "skill" / "examples.md").exists()
    assert (tmp_path / ".claude" / "skills" / f"persona-{built.slug}" / "SKILL.md").exists()
    assert any(item.name == f"/persona-{built.slug}" and item.mode == "roleplay" for item in built.commands)
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")
    assert "example-01" in examples_md


@pytest.mark.live
def test_xiaohongshu_live_skill_build(tmp_path) -> None:
    workflow = PersonaWorkflow(storage_dir=tmp_path / "personas", runtime_root=".runtime")
    if not workflow.layout.backend_python(Platform.XIAOHONGSHU).exists():
        pytest.skip("bootstrap the Xiaohongshu backend before running live tests")
    if not workflow.layout.has_xiaohongshu_login_state():
        pytest.skip("Xiaohongshu login state is missing")

    created, saved_dir = workflow.create_persona([XIAOHONGSHU_URL])
    built = workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")

    assert (saved_dir / "skill" / "examples.md").exists()
    assert (tmp_path / ".claude" / "skills" / f"persona-{built.slug}" / "SKILL.md").exists()
    assert any(item.name == f"/persona-{built.slug}" and item.mode == "roleplay" for item in built.commands)
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")
    assert "example-01" in examples_md
