# -*- coding: utf-8 -*-
"""
llm.py —— DeepSeek API 调用封装（OpenAI 兼容接口，标准库 urllib 实现，无额外依赖）。

使用 LangGraph 做任务规划/工具调用，使用 DeepSeek 做自然语言理解、Text-to-SQL、
SQL 错误自动修正与最终自然语言总结。
"""
import json
import re
import urllib.error
import urllib.request

import config


def chat(system: str, user: str, max_tokens: int = 2000, temperature: float = 0.0) -> str:
    """单轮对话：调用 DeepSeek chat completions，返回文本；未配置密钥或失败返回 None。"""
    if not config.has_api_key():
        return None
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        return (choices[0].get("message") or {}).get("content", "") if choices else ""
    except Exception as e:  # noqa: BLE001 —— 网络/鉴权/限流异常都静默降级到启发式
        print(f"[llm] DeepSeek 调用失败：{e}")
        return None


def parse_json_block(text: str) -> dict:
    """从 LLM 文本回答中稳健地提取第一个 JSON 对象。"""
    if not text:
        return {}
    text = text.strip()
    # 去除 markdown 代码围栏
    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        # 退而求其次：尝试修复常见问题（尾逗号）
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", text[start:end + 1]))
        except Exception:
            return {}


def generate_sql(question: str, schema_prompt: str) -> dict:
    """调用 DeepSeek 进行 Text-to-SQL，返回 {sql, chart_type, reasoning}。"""
    system = (
        "你是一名制造业经营数据分析专家。根据用户问题与数据库 schema，"
        "生成一条可执行的 SQLite SELECT 查询。\n"
        "要求：\n"
        "1. 只使用 schema 中给出的表与字段；日期字段为 TEXT 格式 YYYY-MM-DD，"
        "可用 LIKE '2026-06%' 或 BETWEEN 过滤；\n"
        "2. 涉及车型/产线/客户名称时，用 dim 表 JOIN 出中文名称；\n"
        "3. 聚合查询必须带 GROUP BY 与合适的 ORDER BY；\n"
        "4. 只输出一个 JSON 对象，不要输出任何其它文字。\n"
        "JSON 格式：{\"sql\": \"SELECT ...\", \"chart_type\": \"bar|line|table|text\", "
        "\"reasoning\": \"简要说明\"}\n\n"
        + schema_prompt
    )
    user = f"用户问题：{question}\n请给出 JSON。"
    raw = chat(system, user)
    data = parse_json_block(raw)
    data.setdefault("sql", "")
    data.setdefault("chart_type", "auto")
    data.setdefault("reasoning", "")
    data["raw"] = raw
    return data


def fix_sql(question: str, failed_sql: str, error: str, schema_prompt: str) -> str:
    """根据 SQL 报错信息，自动修正并返回新的 SQL（字符串，可能为空）。"""
    system = (
        "你是一名 SQL 修复专家。用户问题、上一条 SQL 与数据库报错信息如下，"
        "请修正 SQL 使其可执行，只输出修正后的 SQL 文本（一条 SELECT，不要代码围栏、"
        "不要多余说明）。\n\n" + schema_prompt
    )
    user = (
        f"用户问题：{question}\n"
        f"错误 SQL：\n{failed_sql}\n"
        f"报错信息：{error}\n"
        f"请输出修正后的 SQL。"
    )
    raw = chat(system, user, max_tokens=1500)
    if not raw:
        return ""
    sql = raw.strip()
    sql = re.sub(r"^```(?:sql|SQL)?\s*", "", sql)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";")


def summarize(question: str, sql: str, result: dict, trace_tables: list,
              trace_fields: list, schema_prompt: str) -> str:
    """用自然语言总结查询结果（数据 + 溯源）。失败则返回 None，由调用方兜底。"""
    if not result or not result.get("rows"):
        return None
    cols = result["columns"]
    rows = result["rows"][:20]
    lines = ["\t".join(str(c) for c in cols)]
    for r in rows:
        lines.append("\t".join("" if v is None else str(v) for v in r))
    table_text = "\n".join(lines)

    system = (
        "你是制造业运营分析助手。请用简洁、专业的中文，针对用户问题给出自然语言结论，"
        "并明确说明结论所依据的数据表与字段（溯源）。\n\n" + schema_prompt
    )
    user = (
        f"用户问题：{question}\n"
        f"执行的 SQL：{sql}\n"
        f"查询结果（前20行）：\n{table_text}\n"
        f"涉及数据表：{', '.join(trace_tables)}\n"
        f"涉及字段：{', '.join(trace_fields)}\n\n"
        f"请用 100~250 字总结，并注明使用了哪些表、哪些字段。"
    )
    return chat(system, user, max_tokens=800)
