"""权限服务：ZCode PermissionService 的复刻（docs/ARCHITECTURE.md §4.2）。

概念对照：
- 协作模式 mode：build(变更前确认)/edit(自动编辑)/yolo(完全访问)。
- 工具能力 TOOL_SPECS：每工具静态声明（ZCode ToolPermissionSpec 精简版），
  bash 只读命令运行时降级为低风险免批（ZCode resolveBashPermissionCapability 同款）。
- 规则 PermissionRuleset：{version, allow:[{tool, content?}], deny:[...]}；
  匹配 `cmd:*` 前缀（词边界）/* 通配/精确；subject 从 input 依次取
  command/url/file_path/path/pattern 第一个 string 字段。
- 评估顺序（deny 恒压 allow，照抄 ZCode checkPermission）：
  硬拒(deny-list+路径越界) → yolo 直通 → deny 规则
  → ask 规则 → allow 规则 → edit 检查 → build 检查。

安全约束（ZCode 同款）：高危根命令（rm/sudo/del…）不允许生成前缀规则，
"总是允许"退化为整条命令精确匹配——否则 `rm:*` 会连 `rm -rf /` 一起放行。
"""

import re

from app.agent.tools import safe_path
from app.config import settings

# ---------- 硬拒清单（原 permissions.py 移植；命中即拒，不弹窗） ----------

_DENY_PATTERNS: list[str] = [
    "rm -rf /",
    "remove-item -recurse -force c:\\",
    "format ",
    "diskpart",
    "shutdown /s",
    "shutdown -s",
    "rd /s /q c:\\",
]

# 高危根命令："总是允许"对它们只生成整条精确规则，绝不生成前缀规则
_HIGH_RISK_ROOTS = frozenset(
    {"rm", "del", "erase", "rd", "rmdir", "remove-item", "unlink", "sudo",
     "chmod", "format", "diskpart", "iex", "invoke-expression", "reg",
     "net", "set-executionpolicy", "shutdown"}
)

# 只接受无需 shell 求值的字面量；复杂语法宁可多问一次，也不猜测只读。
_READONLY_ROOTS = frozenset({
    "ls", "dir", "cat", "type", "echo", "pwd", "head", "tail", "wc",
    "which", "where", "get-childitem", "get-content", "select-string",
})
_GIT_READONLY_OPTIONS = {
    "status": {"--short", "-s", "--branch", "-b", "--porcelain", "--porcelain=v1", "--porcelain=v2"},
    "log": {"--oneline", "--all", "--decorate", "--no-decorate", "--no-patch"},
    "branch": {"--list", "--all", "-a", "--remotes", "-r", "-v", "-vv", "--show-current", "--no-color"},
    "remote": {"-v", "--verbose"},
}

# ---------- 工具能力声明（ZCode ToolPermissionSpec 精简版） ----------
# permission: 能力组（edit 类在 edit 模式免确认）；readOnly: build 模式放行依据

TOOL_SPECS: dict[str, dict] = {
    "bash":        {"permission": "bash",    "riskLevel": "high",   "sideEffectScope": "system",    "needsApproval": True,  "destructive": True,  "readOnly": False},
    "read_file":   {"permission": "read",    "riskLevel": "low",    "sideEffectScope": "none",      "needsApproval": False, "destructive": False, "readOnly": True},
    "glob":        {"permission": "read",    "riskLevel": "low",    "sideEffectScope": "none",      "needsApproval": False, "destructive": False, "readOnly": True},
    "write_file":  {"permission": "edit",    "riskLevel": "medium", "sideEffectScope": "workspace", "needsApproval": True,  "destructive": False, "readOnly": False},
    "edit_file":   {"permission": "edit",    "riskLevel": "medium", "sideEffectScope": "workspace", "needsApproval": True,  "destructive": False, "readOnly": False},
    # delete_file 移入 rubbish 可恢复（机制保留），但仍是删除语义：build/edit 模式需确认
    "delete_file": {"permission": "delete",  "riskLevel": "high",   "sideEffectScope": "workspace", "needsApproval": True,  "destructive": True,  "readOnly": False},
    "todo_write":  {"permission": "todo",    "riskLevel": "low",    "sideEffectScope": "session",   "needsApproval": False, "destructive": False, "readOnly": True},
    "load_skill":  {"permission": "read",    "riskLevel": "low",    "sideEffectScope": "none",      "needsApproval": False, "destructive": False, "readOnly": True},
    "subtask":     {"permission": "subagent","riskLevel": "low",    "sideEffectScope": "session",   "needsApproval": False, "destructive": False, "readOnly": True},
}

_DEFAULT_SPEC = {"permission": "unknown", "riskLevel": "medium", "sideEffectScope": "workspace",
                 "needsApproval": True, "destructive": False, "readOnly": False}


# ---------- 能力解析 ----------

def _is_readonly_bash(command: str) -> bool:
    """仅降级可证明的简单只读调用；不使用与 PowerShell 不同的 shell 解析器。"""
    # 在 strip 前检查控制字符，避免末尾换行或回车被吞掉；禁用引号、转义、
    # 重定向、变量、子表达式、脚本块与命令链。未知结构交给现有审批流程。
    if any(ord(ch) < 32 and ch != "\t" for ch in command):
        return False
    if not re.fullmatch(r"[\w ./\\:*?=,@+\-\t]+", command):
        return False
    words = command.strip().split()
    if not words:
        return False
    root = words[0].lower()
    if root in _READONLY_ROOTS:
        return True
    if root in {"python", "node"}:
        return words[1:] == ["--version"]
    if root != "git" or len(words) < 2:
        return False
    # Git 参数区分大小写：-d/-D、-m/-M 等写操作绝不能被 lower 后误放行。
    # diff/show 可能调用外部 diff/textconv；未列出的子命令和参数都需要审批。
    subcommand = words[1]
    options = _GIT_READONLY_OPTIONS.get(subcommand)
    if options is None:
        return False
    return all(arg in options or (subcommand == "log" and re.fullmatch(r"-\d+", arg))
               for arg in words[2:])


def _capability(tool_name: str, tool_input: dict) -> dict:
    """工具能力 = 静态声明 + 运行时覆盖（bash 只读命令降级，ZCode 同款）。"""
    spec = dict(TOOL_SPECS.get(tool_name, _DEFAULT_SPEC))
    if tool_name == "bash":
        command = str(tool_input.get("command", ""))
        if _is_readonly_bash(command):
            spec.update({"riskLevel": "low", "sideEffectScope": "none",
                         "needsApproval": False, "destructive": False, "readOnly": True})
    return spec


# ---------- 硬拒（deny-list + 路径越界预检） ----------

def hard_deny_reason(tool_name: str, tool_input: dict) -> str | None:
    """返回硬拒理由；None = 不硬拒。子助手子循环也复用此函数。"""
    if tool_name == "bash":
        command = str(tool_input.get("command", "")).lower()
        for pattern in _DENY_PATTERNS:
            if pattern in command:
                return f"命令命中绝对禁止清单：{pattern.strip()}"
        return None
    if tool_name in ("write_file", "edit_file", "delete_file", "read_file"):
        try:
            safe_path(str(tool_input.get("path", "")))
        except ValueError as exc:
            return str(exc)
    return None


# ---------- 规则匹配 ----------

_SUBJECT_KEYS = ("command", "url", "file_path", "path", "pattern")


def _subject(tool_input: dict) -> str:
    """规则匹配主体：input 里第一个字符串型的已知字段（ZCode 同款顺序）。"""
    for key in _SUBJECT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _match_content(content: str, subject: str) -> bool:
    """`cmd:*` 前缀（词边界）/ `*` 通配 / 精确（ZCode matchesRuleContent 简版）。"""
    if content.endswith(":*"):
        prefix = content[:-2]
        return subject == prefix or subject.startswith(prefix + " ") or subject.startswith(prefix + "\t")
    if "*" in content:
        parts = content.split("*")
        if not subject.startswith(parts[0]):
            return False
        cursor = len(parts[0])
        for part in parts[1:]:
            idx = subject.find(part, cursor)
            if idx < 0:
                return False
            cursor = idx + len(part)
        return True
    return subject == content


def match_rule(rules: dict, behavior: str, tool_name: str, tool_input: dict) -> dict | None:
    """在指定行为桶（allow/deny/ask）里找第一条命中规则。"""
    subject = _subject(tool_input)
    for rule in rules.get(behavior, []):
        if rule.get("toolName") != tool_name:
            continue
        content = rule.get("ruleContent")
        if not content or _match_content(content, subject):
            return rule
    return None


def derive_rule(tool_name: str, tool_input: dict) -> dict | None:
    """从本次请求推导"总是允许"规则。

    bash：高危根命令（rm/sudo…）→ 整条命令精确规则（ZCode 安全约束：
    高危根命令绝不生成前缀规则）；其他 → 首词前缀规则 `cmd:*`。
    非 bash 工具返回 None（v1 只有 bash 会触发审批）。
    """
    if tool_name != "bash":
        return None
    subject = _subject(tool_input).strip()
    if not subject:
        return None
    root = subject.split()[0].lower()
    if root in _HIGH_RISK_ROOTS:
        return {"toolName": tool_name, "ruleContent": subject}
    return {"toolName": tool_name, "ruleContent": root + ":*"}


# ---------- 评估 ----------

def _deny(rule_id: str, reason: str) -> dict:
    return {"decision": "deny", "rule_id": rule_id, "reason": reason}


def _ask(rule_id: str, reason: str) -> dict:
    return {"decision": "ask", "rule_id": rule_id, "reason": reason}


def _allow(rule_id: str, reason: str) -> dict:
    return {"decision": "allow", "rule_id": rule_id, "reason": reason}


def check_permission(
    mode: str, rules: dict, tool_name: str, tool_input: dict,
) -> dict:
    """按 ZCode checkPermission 顺序评估，返回 {decision: allow|deny|ask, rule_id, reason}。"""
    cap = _capability(tool_name, tool_input)

    # 0) 硬拒：deny-list + 路径越界（最前，任何模式/规则不可越过）
    hard = hard_deny_reason(tool_name, tool_input)
    if hard:
        return _deny("hard.deny", hard)

    # 1) yolo 直通（仍受前面的硬拒约束）
    if mode == "yolo":
        return _allow("mode.yolo", "完全访问模式放行")

    # 2) deny 规则
    rule = match_rule(rules, "deny", tool_name, tool_input)
    if rule:
        return _deny("rule.deny", f"命中拒绝规则：{rule.get('ruleContent') or rule.get('toolName')}")

    # 3) ask 规则
    rule = match_rule(rules, "ask", tool_name, tool_input)
    if rule:
        return _ask("rule.ask", "命中确认规则")

    # 4) allow 规则
    rule = match_rule(rules, "allow", tool_name, tool_input)
    if rule:
        return _allow("rule.allow", f"命中允许规则：{rule.get('ruleContent') or rule.get('toolName')}")

    # 5) edit 检查：文件编辑类 + workspace 范围免确认，其余落 build
    if mode == "edit":
        if cap["permission"] == "edit" and cap["sideEffectScope"] == "workspace":
            return _allow("mode.edit.fileEdit", "自动编辑模式放行文件编辑工具")

    # 6) build 检查
    if cap["readOnly"] and not cap["destructive"] and not cap["needsApproval"]:
        return _allow("mode.build.readOnly", "只读工具放行")
    if cap["riskLevel"] == "critical":
        return _ask("mode.build.criticalRisk", "极高风险操作需要确认")
    if cap["riskLevel"] == "high":
        return _ask("mode.build.highRisk", "高风险操作需要确认")
    if (cap["sideEffectScope"] == "session" and cap["riskLevel"] == "low"
            and not cap["destructive"] and not cap["needsApproval"]):
        return _allow("mode.build.sessionState", "低风险会话态更新放行")
    if cap["needsApproval"] or cap["destructive"] or cap["sideEffectScope"] != "none":
        return _ask("mode.build.sideEffect", "该操作有副作用，需要确认")
    return _allow("mode.build.lowRisk", "低风险操作放行")


def evaluate(data_dir, tool_name: str, tool_input: dict) -> dict:
    """主循环/子助手入口：加载执行状态与规则后评估（data_dir 缺省用全局配置）。"""
    from app.sessions import execution_state

    state = execution_state.load_execution_state(data_dir or settings.data_dir)
    rules = execution_state.load_permission_rules(data_dir or settings.data_dir)
    return check_permission(state["mode"], rules, tool_name, tool_input)
