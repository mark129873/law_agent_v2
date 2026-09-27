"""skills：按目录约定加载技能（无管理 UI，产品决策）。

约定：backend/skills/<技能名>/SKILL.md，技能名只允许字母数字下划线连字符。
模型通过 load_skill 工具按需取全文（系统提示词里只放目录，节省上下文）。
"""

import re

from app.config import BACKEND_ROOT

SKILLS_DIR = BACKEND_ROOT / "skills"
_NAME_RE = re.compile(r"^[\w-]+$")


def tool_load_skill(name: str) -> str:
    """读取技能全文；目录穿越与不存在都返回 Error 字符串。"""
    if not _NAME_RE.match(name or ""):
        return f"Error: 非法技能名：{name}"
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())) if SKILLS_DIR.exists() else ""
        return f"Error: 技能不存在：{name}" + (f"（可用：{available}）" if available else "")
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error: 读取技能失败：{exc}"


def list_skill_names() -> list[str]:
    """列出可用技能名（写入系统提示词的技能目录）。"""
    if not SKILLS_DIR.exists():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())
