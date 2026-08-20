# -*- coding: utf-8 -*-
"""
全局配置模块（被 db_init.py / agent_graph.py / app.py 共同引用）。

⚠️ DeepSeek API 密钥配置位置（推荐方式一，避免密钥泄露）：
    1) 项目根目录新建 `.env` 文件（复制 `.env.example` 后填入），已被 .gitignore 忽略；
       DEEPSEEK_API_KEY=sk-xxxx
    2) 环境变量：
       Windows PowerShell:  $env:DEEPSEEK_API_KEY="sk-xxxx"
       Linux / macOS:      export DEEPSEEK_API_KEY="sk-xxxx"
    3) 仅本地临时调试：直接改下方 DEEPSEEK_API_KEY 默认值（用完务必改回空，切勿提交）。
"""
import os

# ==================== .env 文件加载（可选） ====================
# 读取项目根目录 .env（若存在），把 KEY=VALUE 注入环境变量（不覆盖已有变量）。
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    try:
        with open(_ENV_FILE, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))
    except Exception:
        pass  # .env 读取失败不影响启动

# ==================== DeepSeek API 配置 ====================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# 模型：deepseek-chat（DeepSeek-V3，适合 Text-to-SQL/总结）；
#       deepseek-reasoner（推理模型，速度较慢）
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# API 地址（默认官方；可用 DEEPSEEK_BASE_URL 覆盖为代理/自建网关）
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ==================== 数据集与数据库路径 ====================
# 本地数据集主目录：程序会递归读取该目录下全部 csv，自动跳过 __MACOSX 与隐藏文件
DATA_DIR = os.environ.get(
    "CHATBI_DATA_DIR",
    r"D:\桌面\长洙\三命题\制造业运营态势ChatBI-Agent-赛题说明\赛题-制造业运营态势ChatBI",
)

# 备用数据目录：HuggingFace Spaces 部署时，把 csv 放到仓库根目录 ./data 即可
DATA_DIR_FALLBACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# SQLite 数据库文件（默认生成在项目目录下）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatbi.db")

# SQL 执行失败后自动修正并重试的次数上限
MAX_SQL_RETRY = 3

# 数据集时间范围（用于解析未带年份的"6月"等说法）
DEFAULT_YEAR = "2026"


def resolve_data_dir() -> str:
    """返回实际存在的数据目录：优先 DATA_DIR，其次项目内 ./data。"""
    if DATA_DIR and os.path.isdir(DATA_DIR):
        return DATA_DIR
    if os.path.isdir(DATA_DIR_FALLBACK):
        return DATA_DIR_FALLBACK
    return DATA_DIR  # 都不存在时原样返回，由 db_init 给出明确报错


def has_api_key() -> bool:
    """是否配置了有效的 API Key（排除空值与占位符）。"""
    k = (DEEPSEEK_API_KEY or "").strip()
    return bool(k) and not k.startswith("sk-xxx") and "你的" not in k
