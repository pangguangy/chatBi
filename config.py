# -*- coding: utf-8 -*-
"""
全局配置模块（被 db_init.py / agent_graph.py / app.py 共同引用）。

⚠️ Claude API 密钥配置位置 —— 二选一：
    1) 环境变量：export ANTHROPIC_API_KEY="sk-ant-..."
    2) 直接修改下方 CLAUDE_API_KEY 的默认值（仅本地调试用，切勿提交到 Git）
"""
import os

# ==================== Claude API 配置 ====================
# 方式一：从环境变量读取（推荐；HuggingFace Spaces 上通过 Settings -> Secrets 配置）
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 方式二：本地调试时直接在此填入密钥（调试完请改回空字符串，避免泄露）
# CLAUDE_API_KEY = "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 使用的 Claude 模型（可按需切换为 claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5-20251001）
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

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
    """是否配置了有效的 API Key（排除占位符）。"""
    k = (CLAUDE_API_KEY or "").strip()
    return bool(k) and not k.startswith("sk-ant-xxx")
