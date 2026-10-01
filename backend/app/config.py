"""配置加载模块。

做法与 mini_harness 一致：python-dotenv 加载 .env，全部配置收拢到一个
Settings 数据类，进程内共享一份实例。为什么这么做：
1. 配置项集中可查，.env.example 里逐项有中文说明与之对应；
2. 测试只需在导入本模块前设置环境变量（见 tests/conftest.py 的 DATA_DIR），
   即可把数据目录指到测试沙箱，符合 docs/RELIABILITY.md 的测试约束。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录（config.py 位于 backend/app/ 下，parents[1] 即 backend）
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 加载 backend/.env；override=True 保证 .env 优先于已有环境变量（与 mini_harness 一致）
load_dotenv(BACKEND_ROOT / ".env", override=True)


def _env_str(name: str, default: str = "") -> str:
    """读取字符串配置，空值回退默认。"""
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    """读取整数配置，解析失败回退默认。"""
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔配置，接受 true/false/1/0/yes/no。"""
    raw = _env_str(name, str(default)).lower()
    return raw in ("true", "1", "yes", "on")


@dataclass
class Settings:
    """全部运行配置。字段含义见 backend/.env.example 的逐项中文注释。"""

    # --- LLM 接入（发起对话时才强制校验，健康检查不依赖它，方便无钥测试） ---
    anthropic_api_key: str = field(default="")
    anthropic_base_url: str = field(default="")
    model_id: str = field(default="")

    # --- 模型与运行参数 ---
    max_tokens: int = 10000
    bash_timeout: int = 120
    context_window: int = 1_000_000
    compact_output_reserve: int = 32000
    compact_buffer: int = 13000
    microcompact_keep: int = 5

    # --- 服务 ---
    host: str = "127.0.0.1"
    # 默认 8100：本机 8000 常被其他服务（如 Godot MCP）占用
    port: int = 8100
    data_dir: Path = field(default=BACKEND_ROOT / "data")

    # --- 日志 ---
    log_level: str = "INFO"

    # --- model-io JSONL（逐调用 LLM 快照，ZCode 同思想；默认项目根 log/） ---
    modelio_dir: Path = field(default_factory=lambda: BACKEND_ROOT.parent / "log")


def load_settings() -> Settings:
    """从环境变量构造 Settings。"""
    data_dir = Path(_env_str("DATA_DIR", "data"))
    # 相对路径一律基于 backend/ 解析，避免受进程启动目录影响
    if not data_dir.is_absolute():
        data_dir = BACKEND_ROOT / data_dir

    # model-io 目录：默认项目根 log/（BACKEND_ROOT.parent）；相对路径同 data_dir 基于 backend/ 解析
    modelio_dir = Path(_env_str("MODELIO_DIR", str(BACKEND_ROOT.parent / "log")))
    if not modelio_dir.is_absolute():
        modelio_dir = BACKEND_ROOT / modelio_dir

    return Settings(
        anthropic_api_key=_env_str("ANTHROPIC_API_KEY"),
        anthropic_base_url=_env_str("ANTHROPIC_BASE_URL"),
        model_id=_env_str("MODEL_ID"),
        max_tokens=_env_int("MAX_TOKENS", 10000),
        bash_timeout=_env_int("BASH_TIMEOUT", 120),
        context_window=_env_int("CONTEXT_WINDOW", 1_000_000),
        compact_output_reserve=_env_int("COMPACT_OUTPUT_RESERVE", 32000),
        compact_buffer=_env_int("COMPACT_BUFFER", 13000),
        microcompact_keep=_env_int("MICROCOMPACT_KEEP", 5),
        host=_env_str("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000),
        data_dir=data_dir,
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        modelio_dir=modelio_dir,
    )


def validate_llm_config(s: Settings) -> list[str]:
    """校验发起对话必需的 LLM 配置，返回缺失项列表。

    为什么不在启动时硬失败：健康检查与纯存储测试不应依赖真实密钥
    （docs/RELIABILITY.md：测试一律假 LLM），因此在真正发起对话前校验。
    """
    missing: list[str] = []
    if not s.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if not s.model_id:
        missing.append("MODEL_ID")
    return missing


# 进程内共享的配置实例
settings = load_settings()
