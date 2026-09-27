"""权限闸门：mini_harness §3 三道闸的 Web 版移植。

三档决定（由主循环消费）：
- ("allow", None)：自动执行；
- ("deny", reason)：安全策略硬拒，理由作为 Error 结果喂回模型；
- ("approve", reason)：高危操作，交给审批回调（BE-6 的交互审批；测试里可注入假回调）。

规则与 mini_harness 保持一致（产品决策）：
① DENY_LIST 硬拒；② shell 删除类命令 → 审批；③ 高危词 → 审批；
文件路径越界在 safe_path 里硬拒（写类工具先在这里预检，避免进执行层才报错）。
"""

import re

# ① 绝对禁止（大小写不敏感子串匹配）
_DENY_PATTERNS: list[str] = [
    "rm -rf /",
    "remove-item -recurse -force c:\\",
    "format ",
    "diskpart",
    "shutdown /s",
    "shutdown -s",
    "rd /s /q c:\\",
]

# ② 删除类命令（PowerShell 与 Unix 风格都查——脚本里可能混用）
_DELETE_RE = re.compile(
    r"(?i)\b(remove-item|del|erase|rd|rmdir|rm|unlink)\b"
)

# ③ 高危词（出现即要求人工确认）
_HIGH_RISK_RE = re.compile(
    r"(?i)(chmod\s+777|\|\s*bash|\|\s*sh\b|invoke-expression|\biex\s|"
    "reg\\s+add|hkey_local_machine|net\\s+user\\s+.*\\s+/add|"
    "set-executionpolicy\\s+unrestricted)"
)

# 只读类工具（前端聚合"探索"组也用它）
READONLY_TOOLS = {"read_file", "glob"}


def check(tool_name: str, tool_input: dict) -> tuple[str, str | None]:
    """返回 (decision, reason)，decision ∈ allow / deny / approve。"""
    if tool_name == "bash":
        command = str(tool_input.get("command", "")).lower()
        for pattern in _DENY_PATTERNS:
            if pattern in command:
                return "deny", f"命令命中绝对禁止清单：{pattern.strip()}"
        if _DELETE_RE.search(command):
            return "approve", "包含删除类命令"
        if _HIGH_RISK_RE.search(command):
            return "approve", "包含高危操作"
        return "allow", None

    if tool_name in ("write_file", "edit_file", "delete_file", "read_file"):
        # 路径越界预检：safe_path 会抛 ValueError；这里转成 deny 理由
        from app.agent.tools import safe_path

        path = str(tool_input.get("path", ""))
        try:
            safe_path(path)
        except ValueError as exc:
            return "deny", str(exc)
        return "allow", None

    # 未知工具：放行到执行层，执行层会返回"未知工具"错误喂回模型
    return "allow", None
