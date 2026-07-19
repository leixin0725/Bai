"""[2026-07-19] 模板变量必须与职责清单完全一致，不可信数据只能进入数据插槽。"""

from pathlib import Path

import pytest

from bai_agent.config.validation import resolve_inside, validate_template
from bai_agent.domain.errors import BaiError


def test_strict_template_requires_exact_allowed_variables() -> None:
    template = "$trusted_personas\n$memory_overview\n$current_input"
    validate_template(
        template,
        allowed_variables=("trusted_personas", "memory_overview", "current_input"),
        untrusted_variables=("memory_overview", "current_input"),
    )
    with pytest.raises(BaiError):
        validate_template(template, allowed_variables=("trusted_personas", "current_input"), untrusted_variables=("current_input",))
    with pytest.raises(BaiError):
        validate_template("$missing", allowed_variables=("current_input",), untrusted_variables=("current_input",))


def test_curation_template_has_candidates_and_overview_in_one_response() -> None:
    template = Path("config/prompts/memory_curation.md").read_text(encoding="utf-8")
    validate_template(
        template,
        allowed_variables=(
            "curator_persona", "untrusted_boundary", "batch_metadata", "batch_records",
            "existing_memories", "current_overview", "output_schema",
        ),
        untrusted_variables=("batch_metadata", "batch_records", "existing_memories", "current_overview"),
    )
    assert "memory_candidates" in template
    assert "overview_update" in template


def test_absolute_parent_and_symlink_escape_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "config"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("外部", encoding="utf-8")
    for reference in (str(outside.resolve()), "../outside.md"):
        with pytest.raises(BaiError):
            resolve_inside(root, reference)
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        # [2026-07-19] 无创建权限的平台用确定性路径替身验证同一拒绝分支。
        original = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == link else original(self),
        )
    with pytest.raises(BaiError):
        resolve_inside(root, "link.md")
