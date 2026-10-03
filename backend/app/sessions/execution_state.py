"""执行状态（协作模式+计划标志）与权限规则的本地持久化。

为什么用 JSON 文件而非四表：模式/规则是**用户偏好**，不是会话事实——
单项目单用户下文件等价于 ZCode 的 local_setting(scope=project)，
且天然不触及"不做 schema 迁移"的产品决策（那只约束 models.py 四表）。

纪律：损坏/缺字段一律回落默认值（观测与偏好永不挡主流程）；
每次读写直落磁盘（文件极小、频率低，不做内存缓存以免测试隔离问题）。
"""

import json
from pathlib import Path

_MODES = ("build", "edit", "yolo")  # plan 不是 mode，是 planEnabled 标志
_DEFAULT_RULES = {"version": 1, "allow": [], "deny": []}


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None  # 不存在/损坏：回落默认


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_execution_state(data_dir) -> dict:
    """读执行状态；mode 只认 build/edit/yolo，plan_enabled 独立布尔。"""
    data = _read_json(Path(data_dir) / "execution_state.json") or {}
    mode = data.get("mode")
    return {
        "mode": mode if mode in _MODES else "build",
        "plan_enabled": bool(data.get("plan_enabled", False)),
    }


def save_execution_state(data_dir, mode: str, plan_enabled: bool) -> dict:
    """写执行状态（mode 必须是三值之一；plan_enabled 独立）。返回落盘后的状态。"""
    if mode not in _MODES:
        raise ValueError(f"非法协作模式：{mode}")
    state = {"mode": mode, "plan_enabled": bool(plan_enabled)}
    _write_json(Path(data_dir) / "execution_state.json", state)
    return state


def load_permission_rules(data_dir) -> dict:
    """读权限规则集；缺桶/损坏回落空规则集。"""
    data = _read_json(Path(data_dir) / "permission_rules.json") or {}
    return {
        "version": 1,
        "allow": [r for r in data.get("allow", []) if isinstance(r, dict) and r.get("tool")],
        "deny": [r for r in data.get("deny", []) if isinstance(r, dict) and r.get("tool")],
    }


def save_permission_rules(data_dir, ruleset: dict) -> None:
    """整份覆盖写规则集（调用方负责去重）。"""
    _write_json(Path(data_dir) / "permission_rules.json", ruleset)


def add_permission_rule(data_dir, behavior: str, tool: str, content: str | None) -> dict:
    """追加一条规则（同 tool+content 去重，ZCode applyPermissionUpdates 同款键）。"""
    if behavior not in ("allow", "deny"):
        raise ValueError(f"非法规则行为：{behavior}")
    ruleset = load_permission_rules(data_dir)
    key_new = f"{tool}\u0000{content or ''}"
    existing = {f"{r['tool']}\u0000{r.get('content') or ''}" for r in ruleset[behavior]}
    if key_new not in existing:
        rule = {"tool": tool}
        if content:
            rule["content"] = content
        ruleset[behavior].append(rule)
        save_permission_rules(data_dir, ruleset)
    return ruleset
