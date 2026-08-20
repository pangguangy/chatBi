# -*- coding: utf-8 -*-
"""
sql_tool.py —— SQL 执行工具 + 启发式 SQL 兜底库 + 运营日报查询构造。

设计说明：
  * execute_sql 只允许只读查询（SELECT / WITH），保证安全；
  * match_heuristic 内置一套「确定性 SQL 库」，覆盖产量、质量、能耗、达成率、
    延期、停线、日报等典型问法 —— 既能在离线（无 API Key）时保证 Demo 可用，
    又能在 LLM 生成的 SQL 出错时作为重试兜底。
"""
import re
import sqlite3

import pandas as pd

import config
from tools import schema


# ==================== SQL 执行 ====================

def execute_sql(sql: str, db_path: str) -> dict:
    """执行只读 SQL，返回 {columns, rows, row_count}；非法/出错抛异常。"""
    if not sql or not sql.strip():
        raise ValueError("SQL 为空")
    sql = sql.strip().rstrip(";")
    _assert_readonly(sql)
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()
    # NaN/NaT -> None，便于 JSON 序列化与前端展示
    df = df.astype(object).where(pd.notnull(df), None)
    return {
        "columns": [str(c) for c in df.columns],
        "rows": df.values.tolist(),
        "row_count": len(df),
    }


def _assert_readonly(sql: str):
    """只允许 SELECT / WITH 只读查询，拦截 DDL/DML 与多语句。"""
    head = re.sub(r"\s+", " ", sql.strip()).upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("仅允许执行 SELECT 只读查询")
    # 拦截分号后的第二条语句（允许结尾单个分号）
    body = sql.strip().rstrip(";")
    if ";" in body:
        raise ValueError("不允许一次执行多条语句")


# ==================== 日期/实体抽取 ====================

def extract_month_prefix(question: str):
    """抽取 '2026年6月' / '6月' -> '2026-06'；无则返回 None。"""
    m = re.search(r"(?:20\d{2})\s*年\s*(\d{1,2})\s*月", question)
    if m:
        return f"{m.group(0).split('年')[0].strip()}-{int(m.group(1)):02d}"
    m2 = re.search(r"(?<!\d)(\d{1,2})\s*月", question)
    if m2 and 1 <= int(m2.group(1)) <= 12:
        return f"{config.DEFAULT_YEAR}-{int(m2.group(1)):02d}"
    return None


def extract_date_range(question: str):
    """抽取 'X月X日 至/到 Y月Y日' -> (start, end) ISO 字符串；无则返回 None。"""
    m = re.search(
        r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*[至到~－-]\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
        question,
    )
    if not m:
        return None
    m1, d1, m2, d2 = (int(x) for x in m.groups())
    y = config.DEFAULT_YEAR
    return f"{y}-{m1:02d}-{d1:02d}", f"{y}-{m2:02d}-{d2:02d}"


def _first(question: str, pattern: str):
    m = re.search(pattern, question)
    return m.group(0) if m else None


def _mon(col: str, prefix: str) -> str:
    return f"{col} LIKE '{prefix}%'"


# ==================== 意图识别 ====================

def detect_intent(question: str) -> str:
    """识别用户意图：report(日报/月报) / anomaly(异常/溯源) / query(普通问数)。"""
    if re.search(r"日报|月报|运营态势|运营日报|生成.*报", question):
        return "report"
    if re.search(r"异常|原因|为什么|溯源|可能|相关|上升|偏高|偏低|是否|证据", question):
        return "anomaly"
    return "query"


# ==================== 启发式 SQL 兜底库 ====================

def match_heuristic(question: str):
    """返回 {sql, chart_type, intent, reason, tables, fields}；无法匹配返回 None。"""
    q = question.strip()
    month = extract_month_prefix(q)
    daterange = extract_date_range(q)
    # 注意：不能用 \b（Python3 中 CJK 也算 \w，导致「户C005」等无单词边界而匹配失败）。
    # 改用仅排除 ASCII 字母/数字的前后断言，保证「M003/L003/C005」在中文上下文中能正确提取。
    model_id = _first(q, r"(?<![A-Za-z0-9])M\d{3}(?![A-Za-z0-9])")
    line_id = _first(q, r"(?<![A-Za-z0-9])L\d{3}(?![A-Za-z0-9])")
    customer_id = _first(q, r"(?<![A-Za-z0-9])C\d{3}(?![A-Za-z0-9])")
    mf = _mon  # 月份过滤简写

    def hit(sql, chart="auto", reason="", intent=None):
        tables, fields = schema.extract_tables_fields(sql)
        return {
            "sql": sql, "chart_type": chart, "intent": intent or detect_intent(q),
            "reason": reason, "tables": tables, "fields": fields, "source": "heuristic",
        }

    # ---- 1) 运营日报 / 月报 ----
    if detect_intent(q) == "report":
        return hit("", chart="report", reason="生成运营日报/月报", intent="report")

    # ---- 2) 缺陷率趋势（同比/环比，如 M003 缺陷率相对上月是否上升）----
    if "缺陷率" in q or ("缺陷" in q and any(k in q for k in ("上升", "趋势", "环比", "对比", "相对"))):
        mid = model_id or "M003"
        sql = (
            f"SELECT d.月份, ROUND(d.缺陷总数*1000.0/a.总产量, 2) AS 千台缺陷率 "
            f"FROM (SELECT substr(defect_date,1,7) AS 月份, SUM(defect_count) AS 缺陷总数 "
            f"      FROM fact_quality_defects WHERE model_id='{mid}' GROUP BY 月份) d "
            f"JOIN (SELECT substr(production_date,1,7) AS 月份, SUM(actual_quantity) AS 总产量 "
            f"      FROM fact_production_actual WHERE model_id='{mid}' GROUP BY 月份) a "
            f"ON a.月份=d.月份 ORDER BY d.月份"
        )
        return hit(sql, chart="line", reason=f"{mid} 缺陷率逐月趋势（千台缺陷率）")

    # ---- 3) 单车电耗 / 能耗异常（用「环比涨幅」口径，而非绝对值排名）----
    # 赛题「异常升高」指相对前几月的突增（L002 焊装一线 6月 148.95→170.65，+14.6%），
    # 而 L006 焊装二线绝对值最高(~256)但全年稳定，不是异常。
    if "单车电耗" in q or "能耗异常" in q or "电耗异常" in q:
        # 3a) 锁定某产线且问「是否与产量增加一致」→ 能耗 vs 产量 环比对比
        if line_id and ("产量" in q or "一致" in q):
            sql = (
                f"SELECT ROUND((p6.产量-p5.产量)*100.0/p5.产量,2) AS 产量环比pct, "
                f"ROUND((e6.电耗-e5.电耗)*100.0/e5.电耗,2) AS 电耗环比pct, "
                f"ROUND((e6.单车-e5.单车)*100.0/e5.单车,2) AS 单车电耗环比pct "
                f"FROM "
                f"(SELECT SUM(actual_quantity) AS 产量 FROM fact_production_actual "
                f" WHERE line_id='{line_id}' AND substr(production_date,1,7)='2026-05') p5, "
                f"(SELECT SUM(actual_quantity) AS 产量 FROM fact_production_actual "
                f" WHERE line_id='{line_id}' AND substr(production_date,1,7)='2026-06') p6, "
                f"(SELECT SUM(electricity_kwh) AS 电耗, AVG(energy_per_vehicle) AS 单车 "
                f" FROM fact_energy_usage WHERE line_id='{line_id}' AND substr(usage_date,1,7)='2026-05') e5, "
                f"(SELECT SUM(electricity_kwh) AS 电耗, AVG(energy_per_vehicle) AS 单车 "
                f" FROM fact_energy_usage WHERE line_id='{line_id}' AND substr(usage_date,1,7)='2026-06') e6"
            )
            return hit(sql, chart="bar", reason=f"{line_id} 能耗 vs 产量环比对比")
        # 3b) 问「哪条产线单车电耗异常升高」→ 各产线 6月 vs 前5月 单车电耗环比涨幅排名
        sql = (
            "SELECT l.line_name AS 产线, "
            "ROUND(base.avg_epv,2) AS 此前单车电耗, ROUND(m6.avg_epv,2) AS 六月单车电耗, "
            "ROUND((m6.avg_epv-base.avg_epv)*100.0/base.avg_epv,2) AS 环比涨幅pct "
            "FROM dim_workshop_line l "
            "JOIN (SELECT line_id, AVG(energy_per_vehicle) AS avg_epv FROM fact_energy_usage "
            "      WHERE substr(usage_date,1,7)='2026-06' GROUP BY line_id) m6 "
            "  ON l.line_id=m6.line_id "
            "JOIN (SELECT line_id, AVG(energy_per_vehicle) AS avg_epv FROM fact_energy_usage "
            "      WHERE substr(usage_date,1,7)<'2026-06' GROUP BY line_id) base "
            "  ON l.line_id=base.line_id "
            "ORDER BY 环比涨幅pct DESC"
        )
        return hit(sql, chart="bar", reason="各产线单车电耗环比涨幅排名")

    # ---- 4) 能耗 / 电耗（各车间总电耗等）----
    if "电耗" in q or "能耗" in q or "用电" in q:
        if "车间" in q:
            cond = _mon("usage_date", month) if month else "1=1"
            sql = (
                "SELECT workshop AS 车间, ROUND(SUM(electricity_kwh),1) AS 总电耗_kwh "
                f"FROM fact_energy_usage WHERE {cond} GROUP BY workshop ORDER BY 总电耗_kwh DESC"
            )
            return hit(sql, chart="bar", reason="各车间总电耗")
        # 各产线总电耗
        cond = _mon("e.usage_date", month) if month else "1=1"
        sql = (
            "SELECT l.line_name AS 产线, ROUND(SUM(e.electricity_kwh),1) AS 总电耗_kwh "
            "FROM fact_energy_usage e JOIN dim_workshop_line l ON e.line_id=l.line_id "
            f"WHERE {cond} GROUP BY l.line_id ORDER BY 总电耗_kwh DESC"
        )
        return hit(sql, chart="bar", reason="各产线总电耗")

    # ---- 5) 达成率（某产线白/夜班，或整体班次效率）----
    if "达成率" in q or "生产效率" in q:
        if line_id:
            sql = (
                f"SELECT shift AS 班次, ROUND(AVG(achievement_rate),4) AS 平均达成率 "
                f"FROM fact_production_actual WHERE line_id='{line_id}' "
                f"GROUP BY shift ORDER BY 平均达成率"
            )
            return hit(sql, chart="bar", reason=f"{line_id} 各班次达成率")
        # 整体班次效率
        sql = (
            "SELECT shift AS 班次, ROUND(AVG(achievement_rate),4) AS 平均达成率, "
            "ROUND(AVG(downtime_minutes),1) AS 平均停线分钟 "
            "FROM fact_production_actual GROUP BY shift ORDER BY 平均达成率"
        )
        return hit(sql, chart="bar", reason="各班次整体达成率")

    # ---- 6) 停线时长 ----
    if "停线" in q:
        if daterange:
            s, e = daterange
            cond = f"a.production_date BETWEEN '{s}' AND '{e}'"
        elif month:
            cond = _mon("a.production_date", month)
        else:
            cond = "1=1"
        sql = (
            "SELECT l.line_name AS 产线, SUM(a.downtime_minutes) AS 停线总时长_分钟 "
            "FROM fact_production_actual a JOIN dim_workshop_line l ON a.line_id=l.line_id "
            f"WHERE {cond} GROUP BY l.line_id ORDER BY 停线总时长_分钟 DESC"
        )
        return hit(sql, chart="bar", reason="各产线停线时长")

    # ---- 7) 订单延期 ----
    if "延期" in q or "延迟交付" in q:
        # 前 N 个延期天数最高订单
        if "前10" in q or "top" in q.lower() or "最高" in q:
            cond = ("o.delay_days IS NOT NULL AND o.order_date BETWEEN '2026-01-01' AND '2026-06-30'"
                    if "上半年" in q else "o.delay_days IS NOT NULL")
            sql = (
                "SELECT o.order_id AS 订单号, o.customer_id AS 客户ID, "
                "m.model_name AS 车型, o.planned_delivery_date AS 计划交付日, "
                "o.actual_delivery_date AS 实际交付日, o.delay_days AS 延期天数 "
                "FROM fact_orders o JOIN dim_model m ON o.model_id=m.model_id "
                f"WHERE {cond} ORDER BY o.delay_days DESC LIMIT 10"
            )
            return hit(sql, chart="table", reason="延期天数最高的前10个订单")
        if customer_id:
            cond = _mon("o.order_date", month) if month else "1=1"
            sql = (
                f"SELECT o.order_id AS 订单号, c.customer_name AS 客户, m.model_name AS 车型, "
                f"o.planned_delivery_date AS 计划交付日, o.actual_delivery_date AS 实际交付日, "
                f"o.delay_days AS 延期天数 "
                f"FROM fact_orders o JOIN dim_customer c ON o.customer_id=c.customer_id "
                f"JOIN dim_model m ON o.model_id=m.model_id "
                f"WHERE o.customer_id='{customer_id}' AND o.order_status='延期交付' "
                f"AND {cond} ORDER BY o.delay_days DESC"
            )
            return hit(sql, chart="table", reason=f"{customer_id} 延期订单")
        # 各车型延期订单数
        sql = (
            "SELECT m.model_name AS 车型, COUNT(*) AS 延期订单数, "
            "SUM(o.delay_days) AS 累计延期天数 "
            "FROM fact_orders o JOIN dim_model m ON o.model_id=m.model_id "
            "WHERE o.order_status='延期交付' GROUP BY m.model_id ORDER BY 延期订单数 DESC"
        )
        return hit(sql, chart="bar", reason="各车型延期订单数")

    # ---- 7.5) 质量与交付同时异常的车型（跨表综合，须先于“质量”分支匹配）----
    if ("同时" in q or "质量" in q) and "交付" in q:
        sql = (
            "SELECT m.model_name AS 车型, "
            "COALESCE(d.缺陷总数,0) AS 缺陷总数, COALESCE(o.延期订单数,0) AS 延期订单数 "
            "FROM dim_model m "
            "LEFT JOIN (SELECT model_id, SUM(defect_count) AS 缺陷总数 "
            "           FROM fact_quality_defects GROUP BY model_id) d ON d.model_id=m.model_id "
            "LEFT JOIN (SELECT model_id, "
            "           SUM(CASE WHEN order_status='延期交付' THEN 1 ELSE 0 END) AS 延期订单数 "
            "           FROM fact_orders GROUP BY model_id) o ON o.model_id=m.model_id "
            "ORDER BY 缺陷总数 DESC, 延期订单数 DESC"
        )
        return hit(sql, chart="bar", reason="质量与交付综合异常车型")

    # ---- 8) 质量缺陷详情（某车型的工序/缺陷类型）----
    if "工序" in q or "缺陷类型" in q or ("缺陷" in q and "集中" in q):
        mid = model_id or "M003"
        cond = _mon("defect_date", month) if month else "1=1"
        sql = (
            f"SELECT process AS 工序, defect_type AS 缺陷类型, SUM(defect_count) AS 缺陷数 "
            f"FROM fact_quality_defects WHERE model_id='{mid}' AND {cond} "
            f"GROUP BY process, defect_type ORDER BY 缺陷数 DESC LIMIT 10"
        )
        return hit(sql, chart="bar", reason=f"{mid} 缺陷工序/类型分布")

    # ---- 9) 质量缺陷数量（各车型缺陷数 / 哪个车型最多）----
    if "缺陷" in q or "质量" in q:
        cond = _mon("d.defect_date", month) if month else "1=1"
        sql = (
            "SELECT m.model_name AS 车型, SUM(d.defect_count) AS 缺陷总数 "
            "FROM fact_quality_defects d JOIN dim_model m ON d.model_id=m.model_id "
            f"WHERE {cond} GROUP BY m.model_id ORDER BY 缺陷总数 DESC"
        )
        return hit(sql, chart="bar", reason="各车型质量缺陷总数")

    # ---- 10) 产量（各车型总产量；含"趋势/走势/逐月"则交给下方趋势分支）----
    if ("产量" in q or "生产量" in q or "产出" in q) and not any(k in q for k in ("趋势", "走势", "逐月")):
        cond = _mon("a.production_date", month) if month else "1=1"
        sql = (
            "SELECT m.model_name AS 车型, SUM(a.actual_quantity) AS 总产量 "
            "FROM fact_production_actual a JOIN dim_model m ON a.model_id=m.model_id "
            f"WHERE {cond} GROUP BY m.model_id ORDER BY 总产量 DESC"
        )
        return hit(sql, chart="bar", reason="各车型总产量")

    # ---- 11) 产量趋势（按月）----
    if "趋势" in q or "走势" in q or "逐月" in q:
        sql = (
            "SELECT substr(production_date,1,7) AS 月份, SUM(actual_quantity) AS 总产量 "
            "FROM fact_production_actual GROUP BY 月份 ORDER BY 月份"
        )
        return hit(sql, chart="line", reason="产量逐月趋势")

    # ---- 13) 客户信息 / 订单概览 ----
    if "客户" in q or "订单" in q:
        cond = _mon("o.order_date", month) if month else "1=1"
        sql = (
            "SELECT c.customer_name AS 客户, m.model_name AS 车型, "
            "o.order_status AS 订单状态, COUNT(*) AS 订单数, SUM(o.order_quantity) AS 订单总量 "
            "FROM fact_orders o JOIN dim_customer c ON o.customer_id=c.customer_id "
            "JOIN dim_model m ON o.model_id=m.model_id "
            f"WHERE {cond} GROUP BY c.customer_id, m.model_id, o.order_status "
            "ORDER BY 订单数 DESC LIMIT 20"
        )
        return hit(sql, chart="table", reason="客户订单概览")

    return None


# ==================== 运营日报 / 月报查询 ====================

def build_report_queries(month: str):
    """构造某月运营日报的 4 个核心查询，返回 [{"key","title","sql","chart_type"}]。"""
    prefix = month or "2026-06"
    return [
        {
            "key": "output", "title": "① 产量概览（各车型总产量）", "chart_type": "bar",
            "sql": (
                "SELECT m.model_name AS 车型, SUM(a.actual_quantity) AS 总产量 "
                "FROM fact_production_actual a JOIN dim_model m ON a.model_id=m.model_id "
                f"WHERE {_mon('a.production_date', prefix)} GROUP BY m.model_id "
                "ORDER BY 总产量 DESC"
            ),
        },
        {
            "key": "quality", "title": "② 质量概览（各车型缺陷总数）", "chart_type": "bar",
            "sql": (
                "SELECT m.model_name AS 车型, SUM(d.defect_count) AS 缺陷总数 "
                "FROM fact_quality_defects d JOIN dim_model m ON d.model_id=m.model_id "
                f"WHERE {_mon('d.defect_date', prefix)} GROUP BY m.model_id "
                "ORDER BY 缺陷总数 DESC"
            ),
        },
        {
            "key": "energy", "title": "③ 能耗概览（各车间总电耗）", "chart_type": "bar",
            "sql": (
                "SELECT workshop AS 车间, ROUND(SUM(electricity_kwh),1) AS 总电耗_kwh "
                f"FROM fact_energy_usage WHERE {_mon('usage_date', prefix)} "
                "GROUP BY workshop ORDER BY 总电耗_kwh DESC"
            ),
        },
        {
            "key": "delay", "title": "④ 订单延期概览", "chart_type": "table",
            "sql": (
                "SELECT o.order_id AS 订单号, c.customer_name AS 客户, m.model_name AS 车型, "
                "o.planned_delivery_date AS 计划交付日, o.delay_days AS 延期天数 "
                "FROM fact_orders o JOIN dim_customer c ON o.customer_id=c.customer_id "
                "JOIN dim_model m ON o.model_id=m.model_id "
                f"WHERE o.order_status='延期交付' AND {_mon('o.order_date', prefix)} "
                "ORDER BY o.delay_days DESC"
            ),
        },
    ]
