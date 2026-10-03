"""权限服务：ZCode PermissionService 的复刻（docs/ARCHITECTURE.md §4.2）。

概念对照：
- 协作模式 mode：build(变更前确认)/edit(自动编辑)/yolo(完全访问)；planEnabled 是
  独立标志不是第四种 mode——plan 开启时 yolo 直通失效，写入类一律拒绝（不弹窗）。
- 工具能力 TOOL_SPECS：每工具静态声明（ZCode ToolPermissionSpec 精简版），
  bash 只读命令运行时降级为低风险免批（ZCode resolveBashPermissionCapability 同款）。
- 规则 PermissionRuleset：{version, allow:[{tool, content?}], deny:[...]}；
  匹配 `cmd:*` 前缀（词边界）/* 通配/精确；subject 从 input 依次取
  command/url/file_path/path/pattern 第一个 string 字段。
- 评估顺序（deny 恒压 allow，照抄 ZCode checkPermission）：
  硬拒(deny-list+路径越界) → plan 进出特判 → yolo 直通(plan 失效) → deny 规则
  → ask 规则 → plan 检查(readOnly allow/其余 DENY) → allow 规则 → edit 检查 → build 检查。

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

# 删除类命令根词（PowerShell 与 Unix 风格）：既用于硬拒词表外的审批推导，
# 也用于"高危根命令禁止前缀规则"的判定
_DELETE_ROOTS = ("remove-item", "del", "erase", "rd", "rmdir", "rm", "unlink")

_HIGH_RISK_RE = re.compile(
    r"(?i)(chmod\s+777|\|\s*bash|\|\s*sh\b|invoke-expression|\biex\s|"
    "reg\\s+add|hkey_local_machine|net\\s+user\\s+.*\\s+/add|"
    "set-executionpolicy\\s+unrestricted)"
)

# 高危根命令："总是允许"对它们只生成整条精确规则，绝不生成前缀规则
_HIGH_RISK_ROOTS = frozenset(
    {"rm", "del", "erase", "rd", "rmdir", "remove-item", "unlink", "sudo",
     "chmod", "format", "diskpart", "iex", "invoke-expression", "reg",
     "net", "set-executionpolicy", "shutdown"}
)

# bash 只读命令根词（命中且无管道/重定向/命令链 → 运行时降级低风险免批）
_READONLY_BASH_ROOTS = (
    "ls", "dir", "cat", "type", "echo", "pwd", "head", "tail", "wc",
    "which", "where", "get-childitem", "get-content", "select-string",
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "python --version", "node --version",
)

# ---------- 工具能力声明（ZCode ToolPermissionSpec 精简版） ----------
# permission: 能力组（edit 类在 edit 模式免确认）；readOnly: 计划模式放行依据

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
    "enter_plan_mode": {"permission": "plan.enter", "riskLevel": "low", "sideEffectScope": "session",  "needsApproval": False, "destructive": False, "readOnly": True},
    "exit_plan_mode":  {"permission": "plan.exit",  "riskLevel": "low", "sideEffectScope": "session",  "needsApproval": True,  "destructive": False, "readOnly": True},
}

_DEFAULT_SPEC = {"permission": "unknown", "riskLevel": "medium", "sideEffectScope": "workspace",
                 "needsApproval": True, "destructive": False, "readOnly": False}


# ---------- 能力解析 ----------

def _is_readonly_bash(command: str) -> bool:
    """bash 只读命令判定：白名单根词开头，且不含管道/重定向/命令链。"""
    cmd = command.strip().lower()
    if not cmd or any(ch in cmd for ch in "|>&;"):
        return False
    return cmd in _READONLY_BASH_ROOTS or any(
        cmd.startswith(root + " ") for root in _READONLY_BASH_ROOTS
    )


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
        if rule.get("tool") != tool_name:
            continue
        content = rule.get("content")
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
        return {"tool": tool_name, "content": subject}
    return {"tool": tool_name, "content": root + ":*"}


# ---------- 评估 ----------

def _deny(rule_id: str, reason: str) -> dict:
    return {"decision": "deny", "rule_id": rule_id, "reason": reason}


def _ask(rule_id: str, reason: str) -> dict:
    return {"decision": "ask", "rule_id": rule_id, "reason": reason}


def _allow(rule_id: str, reason: str) -> dict:
    return {"decision": "allow", "rule_id": rule_id, "reason": reason}


def check_permission(
    mode: str, plan_enabled: bool, rules: dict, tool_name: str, tool_input: dict,
) -> dict:
    """按 ZCode checkPermission 顺序评估，返回 {decision: allow|deny|ask, rule_id, reason}。"""
    cap = _capability(tool_name, tool_input)

    # 0) 硬拒：deny-list + 路径越界（最前，任何模式/规则不可越过）
    hard = hard_deny_reason(tool_name, tool_input)
    if hard:
        return _deny("hard.deny", hard)

    # 1) plan 进出工具特判（优先于一切规则与模式）
    if tool_name == "enter_plan_mode":
        return _allow("tool.plan.enter", "进入计划模式无需确认")
    if tool_name == "exit_plan_mode":
        if not plan_enabled:
            return _deny("tool.plan.exitOnly", "只能在计划模式中使用 ExitPlanMode")
        return _ask("plan.exit", "计划审批：批准后退出计划模式并开始实现")

    # 2) yolo 直通（planEnabled 时失效）
    if mode == "yolo" and not plan_enabled:
        return _allow("mode.yolo", "完全访问模式放行")

    # 3) deny 规则
    rule = match_rule(rules, "deny", tool_name, tool_input)
    if rule:
        return _deny("rule.deny", f"命中拒绝规则：{rule.get('content') or rule.get('tool')}")

    # 4) ask 规则
    rule = match_rule(rules, "ask", tool_name, tool_input)
    if rule:
        return _ask("rule.ask", "命中确认规则")

    # 5) plan 检查：只读且非破坏放行，其余一律拒绝（不弹窗）
    if plan_enabled:
        if cap["readOnly"] and not cap["destructive"]:
            return _allow("mode.plan.readOnly", "计划模式放行只读工具")
        return _deny("mode.plan.nonReadOnly", "计划模式只允许只读、非破坏性工具")

    # 6) allow 规则
    rule = match_rule(rules, "allow", tool_name, tool_input)
    if rule:
        return _allow("rule.allow", f"命中允许规则：{rule.get('content') or rule.get('tool')}")

    # 7) edit 检查：文件编辑类 + workspace 范围免确认，其余落 build
    if mode == "edit":
        if cap["permission"] == "edit" and cap["sideEffectScope"] == "workspace":
            return _allow("mode.edit.fileEdit", "自动编辑模式放行文件编辑工具")

    # 8) build 检查
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
    return check_permission(state["mode"], state["plan_enabled"], rules, tool_name, tool_input)
