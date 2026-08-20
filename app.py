# -*- coding: utf-8 -*-
"""
app.py —— Streamlit 交互页面（制造业运营态势 Chat-BI Agent）。

启动：streamlit run app.py

页面展示：
  - 用户提问
  - Agent 执行过程（规划 / 执行 SQL / 自动修正 / 生成图表 / 异常溯源）
  - 执行的 SQL 语句
  - 查询结果表格
  - 生成图表（柱状图 / 折线图）
  - 运营日报 / 月报内容
  - 异常溯源文本（含数据表、字段依据）
"""
import base64
import os
import sqlite3

import streamlit as st

import config
from agent_graph import run_agent
from tools import chart_tool

st.set_page_config(page_title="制造业运营态势 Chat-BI Agent", layout="wide",
                   page_icon="🏭")

EXAMPLE_QUESTIONS = [
    "2026年6月各车型的总产量是多少？按产量从高到低排序。",
    "2026年6月哪个车型质量缺陷数量最多？",
    "M003车型在2026年6月的主要缺陷集中在哪个工序和缺陷类型？",
    "2026年6月10日至6月24日，哪条产线单车电耗异常升高？",
    "L003产线白班和夜班的平均达成率分别是多少？",
    "客户C005在6月是否存在订单延期？主要涉及哪个车型？",
    "2026年4月8日至4月18日哪条产线停线时长偏高？",
    "各车间2026年6月总电耗是多少？",
    "2026年上半年订单延期天数最高的前10个订单是哪些？",
    "2026年6月M003车型缺陷率相对5月是否明显上升？",
    "哪个班次整体生产效率偏低？请给出证据。",
    "生成2026年6月运营日报，包含产量、质量、能耗和订单延期概览。",
]


# ==================== 数据库初始化 ====================

@st.cache_data(show_spinner=False)
def _init_db(data_dir: str, db_path: str):
    import db_init
    return db_init.init_db(data_dir, db_path)


def ensure_db(data_dir: str, db_path: str):
    """确保数据库存在且已导入数据，返回 (ok, message, summary)。"""
    if not os.path.isdir(data_dir):
        return False, f"数据目录不存在：{data_dir}", {}
    if os.path.exists(db_path):
        # 已有库：快速校验是否有核心表
        try:
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            conn.close()
            if "fact_production_actual" in tables and "dim_model" in tables:
                return True, "数据库已就绪", _table_stats(db_path)
        except Exception:
            pass
    try:
        summary = _init_db(data_dir, db_path)
        return True, "数据库初始化完成", summary
    except Exception as e:  # noqa: BLE001
        return False, f"初始化失败：{e}", {}


def _table_stats(db_path: str) -> dict:
    stats = {}
    try:
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        for t in tables:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                stats[t] = cnt
            except Exception:
                stats[t] = None
        conn.close()
    except Exception:
        pass
    return stats


# ==================== 渲染辅助 ====================

def _show_chart(b64: str, caption: str = ""):
    if not b64:
        return
    st.image(base64.b64decode(b64), width="stretch", caption=caption)


# ==================== 侧边栏 ====================

with st.sidebar:
    st.title("⚙️ 配置")
    data_dir = st.text_input("数据目录（CSV）", value=config.resolve_data_dir())
    db_path = st.text_input("SQLite 数据库路径", value=config.DB_PATH)

    st.markdown("---")
    st.subheader("Claude API")
    if config.has_api_key():
        st.success(f"已配置 API Key（模型：{config.CLAUDE_MODEL}）")
        llm_mode = "LLM 驱动（Text-to-SQL）"
    else:
        st.info("未配置 API Key，运行于**离线模式**（内置启发式 SQL + 确定性总结）")
        st.caption("配置方式：环境变量 ANTHROPIC_API_KEY，或修改 config.py 中的 CLAUDE_API_KEY")
        llm_mode = "离线模式（启发式 SQL）"

    st.markdown("---")
    if st.button("🔄 重新初始化数据库", width="stretch"):
        with st.spinner("正在读取 CSV 并导入 SQLite..."):
            ok, msg, summary = ensure_db(data_dir, db_path)
            if ok:
                st.session_state["db_ready"] = True
                st.session_state["db_summary"] = summary
                st.success(msg)
            else:
                st.error(msg)

    # 数据库状态
    st.subheader("数据库状态")
    if os.path.exists(db_path):
        stats = _table_stats(db_path)
        if stats:
            for t, c in stats.items():
                st.text(f"{t}: {c} 行")
    else:
        st.caption("数据库尚未初始化")


# ==================== 主页面 ====================

st.title("🏭 制造业运营态势 Chat-BI Agent")
st.caption("用自然语言直接问数 · 自动生成图表与运营日报 · 异常可解释溯源")
st.caption(f"当前模式：**{llm_mode}**")

# 启动时确保数据库就绪
if "db_ready" not in st.session_state:
    with st.spinner("首次启动，正在初始化数据库（读取 CSV -> SQLite）..."):
        ok, msg, summary = ensure_db(data_dir, db_path)
        st.session_state["db_ready"] = ok
        st.session_state["db_summary"] = summary
        if not ok:
            st.error(msg)

st.markdown("---")
st.subheader("💬 输入你的问题")
question = st.text_input("自然语言问数（覆盖产量/订单/质量/能耗/达成率/延期等）",
                         placeholder="例如：2026年6月各车型的总产量是多少？")

st.markdown("**或点击示例问题：**")
cols = st.columns(3)
for i, q in enumerate(EXAMPLE_QUESTIONS):
    if cols[i % 3].button(q[:22] + ("…" if len(q) > 22 else ""), key=f"ex{i}",
                          width="stretch"):
        question = q
        st.session_state["run_q"] = q

if st.button("🚀 开始分析", type="primary", width="stretch") and question.strip():
    st.session_state["run_q"] = question.strip()

# ==================== 执行与结果展示 ====================

if st.session_state.get("run_q"):
    q = st.session_state["run_q"]
    if "result_state" not in st.session_state or st.session_state.get("last_q") != q:
        with st.spinner("Agent 规划 → 生成 SQL → 执行 → 生成图表 → 异常溯源 → 汇总..."):
            try:
                state = run_agent(q, db_path)
            except Exception as e:  # noqa: BLE001
                st.error(f"Agent 执行异常：{e}")
                st.stop()
            st.session_state["result_state"] = state
            st.session_state["last_q"] = q

    state = st.session_state["result_state"]

    st.markdown("---")
    st.subheader("🧭 Agent 执行过程")
    trace = state.get("trace") or []
    if trace:
        with st.expander("查看执行步骤", expanded=False):
            for step in trace:
                st.markdown(f"- **{step['step']}**：{step['detail']}")

    if state.get("intent") == "report":
        # ===== 日报/月报展示 =====
        report = state.get("report") or {}
        st.subheader(f"📊 运营态势日报（{report.get('month', '')}）")
        for sec in report.get("sections", []):
            st.markdown(f"**{sec['title']}**")
            if sec.get("chart_b64"):
                _show_chart(sec["chart_b64"], sec["title"])
            with st.expander("查看该板块 SQL", expanded=False):
                st.code(sec.get("sql", ""), language="sql")
        st.markdown(state.get("answer", ""))
    else:
        # ===== 普通/异常问数展示 =====
        st.subheader("📝 执行的 SQL")
        st.code(state.get("sql", "") or "（未生成 SQL）", language="sql")
        if state.get("sql_source") == "llm-fix" or state.get("sql_source") == "heuristic-fallback":
            st.caption(f"⚠️ SQL 经过自动修正重试（来源：{state['sql_source']}）")

        result = state.get("query_result")
        if result and result.get("rows"):
            st.subheader("📋 查询结果")
            st.dataframe(chart_tool.result_to_records(result), width="stretch")

        if state.get("chart_b64"):
            st.subheader("📈 图表")
            _show_chart(state["chart_b64"], state.get("chart_title", ""))

        st.subheader("💡 回答")
        st.markdown(state.get("answer", ""))

    if state.get("traceability"):
        st.subheader("🔍 异常溯源")
        st.markdown(state["traceability"])

    st.markdown("---")
    st.caption("提示：数据来自官方模拟制造业经营数据集（dim 维表 + fact 事实表），时间范围 2026-01-01 ~ 2026-06-30。")
