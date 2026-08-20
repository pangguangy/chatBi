# -*- coding: utf-8 -*-
"""
agent_graph.py —— 基于 LangGraph 的 Chat-BI Agent 核心。

核心能力：
  1. 任务规划（planning 节点：意图识别 + Text-to-SQL + 图表类型决策）
  2. 工具调用（execute_sql / chart / report / anomaly 节点分别调用对应工具）
  3. SQL 执行失败自动修正重试（execute_sql -> fix_sql -> execute_sql 循环，上限 MAX_SQL_RETRY）

图结构：
  START -> planning ─┬─(普通/异常)──> execute_sql ─┬─成功─> chart ─┬─(需溯源)─> anomaly ─┐
                     └─(日报/月报)──> report ─────────────────────┴───────────────────────┴─> finalize -> END
                         execute_sql ──失败──> fix_sql ──> execute_sql (重试循环)
"""
from typing import Any, Dict, List, Optional, TypedDict

import config
from tools import llm, schema
from tools.anomaly_tool import analyze_anomalies, trace_anomaly, format_traceability
from tools.chart_tool import generate_chart, render_markdown_table
from tools.sql_tool import (
    build_report_queries, detect_intent, execute_sql, extract_month_prefix,
    match_heuristic,
)


class AgentState(TypedDict, total=False):
    question: str
    month: Optional[str]
    db_path: str
    # 规划结果
    intent: str                      # query / report / anomaly
    plan: Dict[str, Any]
    sql: str
    chart_type: str
    sql_source: str                  # llm / heuristic / llm-fix / heuristic-fallback
    fallback_sql: str
    # 执行与重试
    sql_error: str
    retry_count: int
    max_retry: int
    query_result: Optional[Dict[str, Any]]
    # 图表
    chart_b64: Optional[str]
    chart_title: str
    # 报告 / 异常
    report: Optional[Dict[str, Any]]
    anomaly_report: str
    need_anomaly: bool
    # 结果
    answer: str
    traceability: str
    trace: List[Dict[str, str]]


def _append_trace(state: AgentState, step: str, detail: str) -> None:
    state.setdefault("trace", []).append({"step": step, "detail": detail})


# ==================== 节点 ====================

def planning_node(state: AgentState) -> AgentState:
    question = state["question"]
    intent = detect_intent(question)
    state["intent"] = intent
    state["need_anomaly"] = (intent == "anomaly")

    # 1) 始终计算一份启发式 SQL 作为兜底（确定性、零成本）
    heuristic = match_heuristic(question)
    state["fallback_sql"] = (heuristic or {}).get("sql", "")

    # 2) 优先调用 Claude 做 Text-to-SQL（体现 Agent 智能）
    if intent == "report":
        _append_trace(state, "规划", "识别为「运营日报/月报」任务，进入报告生成流程")
        state["sql"] = ""
        state["chart_type"] = "report"
        state["sql_source"] = "report"
        return state

    schema_prompt = schema.build_schema_prompt()
    plan = llm.generate_sql(question, schema_prompt) if config.has_api_key() else {}
    llm_sql = (plan.get("sql") or "").strip()

    if llm_sql:
        state["sql"] = llm_sql
        state["chart_type"] = plan.get("chart_type") or (heuristic or {}).get("chart_type", "auto") or "auto"
        state["sql_source"] = "llm"
        state["plan"] = {"sql": llm_sql, "chart_type": state["chart_type"],
                         "reasoning": plan.get("reasoning", ""), "source": "llm"}
        _append_trace(state, "规划", f"Claude 生成 SQL（图表类型：{state['chart_type']}）")
    elif state["fallback_sql"]:
        state["sql"] = state["fallback_sql"]
        state["chart_type"] = (heuristic or {}).get("chart_type", "auto")
        state["sql_source"] = "heuristic"
        state["plan"] = dict(heuristic or {})
        _append_trace(state, "规划", "未配置 API Key 或 Claude 未返回 SQL，使用内置启发式 SQL")
    else:
        state["sql"] = ""
        state["chart_type"] = "text"
        state["sql_source"] = "none"
        _append_trace(state, "规划", "未生成 SQL（离线且未匹配内置问题）")

    state["retry_count"] = 0
    state["max_retry"] = config.MAX_SQL_RETRY
    state["sql_error"] = ""
    state["query_result"] = None
    return state


def execute_sql_node(state: AgentState) -> AgentState:
    sql = state.get("sql", "")
    if not sql:
        state["sql_error"] = "未生成可执行的 SQL"
        _append_trace(state, "执行 SQL", "SQL 为空，无法执行")
        return state
    try:
        result = execute_sql(sql, state["db_path"])
        state["query_result"] = result
        state["sql_error"] = ""
        _append_trace(state, "执行 SQL", f"查询成功，返回 {result['row_count']} 行")
    except Exception as e:  # noqa: BLE001
        state["query_result"] = None
        state["sql_error"] = str(e)
        _append_trace(state, "执行 SQL", f"SQL 出错：{e}")
    return state


def fix_sql_node(state: AgentState) -> AgentState:
    """SQL 出错后的自动修正节点：优先 Claude 修复，最后切换到启发式兜底。"""
    state["retry_count"] = state.get("retry_count", 0) + 1
    question = state["question"]
    error = state.get("sql_error", "")
    max_retry = state.get("max_retry", config.MAX_SQL_RETRY)

    use_fallback = (state["retry_count"] >= max_retry) or (not config.has_api_key())
    new_sql = ""

    if use_fallback and state.get("fallback_sql"):
        new_sql = state["fallback_sql"]
        state["sql_source"] = "heuristic-fallback"
        _append_trace(state, "SQL 修正", f"第{state['retry_count']}次重试：切换内置启发式 SQL")
    else:
        schema_prompt = schema.build_schema_prompt()
        new_sql = llm.fix_sql(question, state.get("sql", ""), error, schema_prompt)
        state["sql_source"] = "llm-fix"
        if new_sql:
            _append_trace(state, "SQL 修正", f"第{state['retry_count']}次重试：Claude 根据报错自动修正 SQL")
        else:
            _append_trace(state, "SQL 修正", f"第{state['retry_count']}次重试：Claude 未能给出修正")

    if new_sql and new_sql.strip():
        state["sql"] = new_sql.strip()
        state["sql_error"] = ""
    else:
        # 无法修复，直接判失败
        state["retry_count"] = max_retry
    return state


def chart_node(state: AgentState) -> AgentState:
    result = state.get("query_result")
    if result and result.get("rows"):
        chart_type = state.get("chart_type", "auto") or "auto"
        if chart_type in ("auto", ""):
            chart_type = "bar"
        title = state["question"]
        try:
            b64 = generate_chart(result, chart_type, title)
        except Exception:  # noqa: BLE001
            b64 = None
        state["chart_b64"] = b64
        state["chart_title"] = title
        if b64:
            _append_trace(state, "生成图表", f"已生成 {chart_type} 图表")
        else:
            _append_trace(state, "生成图表", "该结果适合以表格/文字呈现")
    return state


def report_node(state: AgentState) -> AgentState:
    question = state["question"]
    prefix = state.get("month") or extract_month_prefix(question) or "2026-06"
    queries = build_report_queries(prefix)

    sections = []
    for q in queries:
        try:
            result = execute_sql(q["sql"], state["db_path"])
            error = ""
        except Exception as e:  # noqa: BLE001
            result = {"columns": [], "rows": [], "row_count": 0}
            error = str(e)
        tables, fields = schema.extract_tables_fields(q["sql"])
        chart_b64 = generate_chart(result, q["chart_type"], q["title"]) if not error else None
        sections.append({
            "title": q["title"], "sql": q["sql"], "result": result,
            "chart_b64": chart_b64, "chart_type": q["chart_type"],
            "tables": tables, "fields": fields, "error": error,
        })

    anomalies = analyze_anomalies(state["db_path"], prefix)
    state["report"] = {"month": prefix, "sections": sections, "anomalies": anomalies}
    state["anomaly_report"] = format_traceability(anomalies)
    _append_trace(state, "生成报告", f"已生成 {prefix} 运营态势日报（{len(sections)} 个板块 + {len(anomalies)} 项异常）")
    return state


def anomaly_node(state: AgentState) -> AgentState:
    question = state["question"]
    prefix = state.get("month") or extract_month_prefix(question) or "2026-06"
    findings = trace_anomaly(question, state["db_path"], prefix)
    state["anomaly_report"] = format_traceability(findings)
    _append_trace(state, "异常溯源", f"对 {len(findings)} 项异常进行溯源分析")
    return state


def finalize_node(state: AgentState) -> AgentState:
    question = state["question"]
    sql = state.get("sql", "")

    if state.get("intent") == "report":
        state["answer"] = _build_report_answer(state)
        state["traceability"] = state.get("anomaly_report", "")
        _append_trace(state, "汇总", "生成运营日报最终回答")
        return state

    # 普通 / 异常问数
    if state.get("sql_error") and not state.get("query_result"):
        state["answer"] = f"未能得到查询结果：{state['sql_error']}\n\n请尝试换一种问法，或检查数据是否已初始化。"
        state["traceability"] = ""
        _append_trace(state, "汇总", "查询失败，输出错误说明")
        return state

    result = state.get("query_result")
    tables, fields = schema.extract_tables_fields(sql)
    base_trace = _build_traceability(tables, fields)
    anomaly_trace = state.get("anomaly_report", "")
    if anomaly_trace:
        state["traceability"] = anomaly_trace + ("\n\n" + base_trace if base_trace else "")
    else:
        state["traceability"] = base_trace

    # 自然语言总结：优先 Claude，失败/无 Key 则确定性兜底
    summary = None
    if config.has_api_key() and result and result.get("rows"):
        summary = llm.summarize(question, sql, result, tables, fields, schema.build_schema_prompt())
    if not summary:
        summary = _deterministic_summary(question, result)

    state["answer"] = summary
    _append_trace(state, "汇总", "生成最终回答")
    return state


# ==================== 条件路由 ====================

def route_after_planning(state: AgentState) -> str:
    return "report" if state.get("intent") == "report" else "execute_sql"


def route_after_execute(state: AgentState) -> str:
    if not state.get("sql_error"):
        return "chart"
    if state.get("retry_count", 0) < state.get("max_retry", config.MAX_SQL_RETRY):
        return "fix_sql"
    return "finalize"


def route_after_chart(state: AgentState) -> str:
    return "anomaly" if state.get("need_anomaly") else "finalize"


# ==================== 回答组装 ====================

def _deterministic_summary(question: str, result: Optional[Dict[str, Any]]) -> str:
    if not result or not result.get("rows"):
        return "查询无结果，请调整问法或时间范围。"
    cols = [str(c) for c in result["columns"]]
    row_count = result["row_count"]
    head = result["rows"][0]
    head_txt = "，".join(f"{cols[i]}={_fmt(head[i])}" for i in range(min(len(cols), len(head))))
    return (
        f"针对问题「{question}」，共查询到 **{row_count}** 行数据。\n\n"
        f"首行结果：{head_txt}。\n\n"
        f"（离线模式：未调用 Claude 生成自然语言总结，仅展示数据结果。）"
    )


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(round(v, 2))
    return str(v)


def _build_traceability(tables: List[str], fields: List[str]) -> str:
    if not tables:
        return ""
    return (
        "## 溯源说明\n"
        f"- 用到数据表：`{'`、`'.join(tables)}`\n"
        f"- 用到字段：`{'`、`'.join(fields)}`"
    )


def _build_report_answer(state: AgentState) -> str:
    report = state.get("report") or {}
    month = report.get("month", "2026-06")
    lines = [f"# {month} 制造业运营态势日报\n"]
    for sec in report.get("sections", []):
        lines.append(f"\n## {sec['title']}\n")
        if sec.get("error"):
            lines.append(f"（查询出错：{sec['error']}）")
        else:
            lines.append(render_markdown_table(sec.get("result"), max_rows=15))
        tables, fields = sec.get("tables", []), sec.get("fields", [])
        if tables:
            lines.append(f"\n> 溯源：表 `{'`、`'.join(tables)}`；字段 `{'`、`'.join(fields)}`")
    return "\n".join(lines)


# ==================== 组装图 ====================

def build_graph():
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(AgentState)
    g.add_node("planning", planning_node)
    g.add_node("execute_sql", execute_sql_node)
    g.add_node("fix_sql", fix_sql_node)
    g.add_node("chart", chart_node)
    g.add_node("report", report_node)
    g.add_node("anomaly", anomaly_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "planning")
    g.add_conditional_edges("planning", route_after_planning,
                            {"report": "report", "execute_sql": "execute_sql"})
    g.add_conditional_edges("execute_sql", route_after_execute,
                            {"chart": "chart", "fix_sql": "fix_sql", "finalize": "finalize"})
    g.add_edge("fix_sql", "execute_sql")
    g.add_conditional_edges("chart", route_after_chart,
                            {"anomaly": "anomaly", "finalize": "finalize"})
    g.add_edge("anomaly", "finalize")
    g.add_edge("report", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def run_agent(question: str, db_path: str, month: Optional[str] = None) -> AgentState:
    """对外统一入口：输入自然语言问题，返回完整 AgentState（含 SQL/表格/图表/回答/溯源）。"""
    graph = get_graph()
    init_state: AgentState = {
        "question": question,
        "month": month,
        "db_path": db_path,
        "intent": "query",
        "retry_count": 0,
        "max_retry": config.MAX_SQL_RETRY,
        "need_anomaly": False,
        "trace": [],
    }
    return graph.invoke(init_state)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Chat-BI Agent 命令行测试")
    parser.add_argument("question", help="自然语言问题")
    parser.add_argument("--db", default=None, help="SQLite 数据库路径")
    args = parser.parse_args()

    db = args.db or config.DB_PATH
    st = run_agent(args.question, db)
    print("=" * 60)
    print("SQL:", st.get("sql"))
    print("=" * 60)
    if st.get("query_result"):
        print(render_markdown_table(st["query_result"]))
    print(st.get("answer", ""))
    if st.get("traceability"):
        print(st["traceability"])
