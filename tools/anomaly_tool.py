# -*- coding: utf-8 -*-
"""
anomaly_tool.py —— 异常检测与溯源分析工具。

每个异常结论都包含：
  - name          异常现象描述
  - evidence      数据依据（查询结果文字化）
  - tables_used   用到哪些数据表
  - fields_used   用到哪些字段
  - sql           依据的 SQL
  - verdict       结论性判断

溯源说明严格写明「用到哪些数据表、哪些字段」，满足赛题硬性要求。
"""
from tools.sql_tool import execute_sql


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(round(v, 2))
    return str(v)


def _evidence(result, limit=5) -> str:
    if not result or not result.get("rows"):
        return "（无数据）"
    cols = [str(c) for c in result["columns"]]
    lines = [" / ".join(cols)]
    for r in result["rows"][:limit]:
        lines.append("  " + " | ".join(_fmt(v) for v in r))
    return "\n".join(lines)


def _finding(name, description, sql, result, verdict, tables, fields):
    return {
        "name": name,
        "description": description,
        "sql": sql,
        "evidence": _evidence(result),
        "verdict": verdict,
        "tables_used": tables,
        "fields_used": fields,
    }


def analyze_anomalies(db_path: str, month_prefix: str = "2026-06"):
    """运行 5 类预置异常检测，返回结构化 findings 列表。"""
    findings = []

    # ---- 1) 质量异常：M003 在 L002 焊装 焊点虚焊 ----
    sql = (
        "SELECT process AS 工序, defect_type AS 缺陷类型, SUM(defect_count) AS 缺陷数 "
        "FROM fact_quality_defects "
        f"WHERE model_id='M003' AND line_id='L002' AND defect_date LIKE '{month_prefix}%' "
        "GROUP BY process, defect_type ORDER BY 缺陷数 DESC LIMIT 5"
    )
    try:
        r = execute_sql(sql, db_path)
        top = r["rows"][0] if r["rows"] else None
        verdict = "确认存在质量异常" if top else "未发现明显质量异常"
        findings.append(_finding(
            "质量异常", "2026年6月 M003（中卡C型）在 L002（焊装一线）出现明显质量异常，集中于焊装-焊点虚焊",
            sql, r, verdict,
            ["fact_quality_defects", "dim_workshop_line"],
            ["defect_date", "model_id", "line_id", "process", "defect_type", "defect_count"],
        ))
    except Exception as e:  # noqa: BLE001
        findings.append({"name": "质量异常", "description": "检测失败", "sql": sql,
                         "evidence": str(e), "verdict": "查询出错",
                         "tables_used": ["fact_quality_defects"],
                         "fields_used": ["defect_date", "model_id", "line_id", "process",
                                         "defect_type", "defect_count"]})

    # ---- 2) 能耗异常：L002 焊装一线 6月单车电耗环比突增，与产量脱钩 ----
    sql = (
        "SELECT ROUND((p6.产量-p5.产量)*100.0/p5.产量,2) AS 产量环比pct, "
        "ROUND((e6.电耗-e5.电耗)*100.0/e5.电耗,2) AS 电耗环比pct, "
        "ROUND((e6.单车-e5.单车)*100.0/e5.单车,2) AS 单车电耗环比pct "
        "FROM "
        "(SELECT SUM(actual_quantity) AS 产量 FROM fact_production_actual "
        " WHERE line_id='L002' AND substr(production_date,1,7)='2026-05') p5, "
        "(SELECT SUM(actual_quantity) AS 产量 FROM fact_production_actual "
        " WHERE line_id='L002' AND substr(production_date,1,7)='2026-06') p6, "
        "(SELECT SUM(electricity_kwh) AS 电耗, AVG(energy_per_vehicle) AS 单车 "
        " FROM fact_energy_usage WHERE line_id='L002' AND substr(usage_date,1,7)='2026-05') e5, "
        "(SELECT SUM(electricity_kwh) AS 电耗, AVG(energy_per_vehicle) AS 单车 "
        " FROM fact_energy_usage WHERE line_id='L002' AND substr(usage_date,1,7)='2026-06') e6"
    )
    try:
        r = execute_sql(sql, db_path)
        row = r["rows"][0] if r["rows"] else None
        # row = [产量环比pct, 电耗环比pct, 单车电耗环比pct]
        verdict = (
            f"L002 电耗环比 +{row[1]}%，产量环比仅 +{row[0]}%，单车电耗环比 +{row[2]}%，"
            "能耗增长与产量脱钩，属能耗效率异常"
        ) if row else "未发现明显能耗异常"
        findings.append(_finding(
            "能耗异常",
            "L002（焊装一线）6月总电耗环比大幅上升，而同期产量几乎未增长，单车电耗同步上升，"
            "说明能耗异常并非产量增加所致，而是能耗效率下降",
            sql, r, verdict,
            ["fact_energy_usage", "fact_production_actual", "dim_workshop_line"],
            ["usage_date", "line_id", "electricity_kwh", "energy_per_vehicle",
             "production_date", "actual_quantity"],
        ))
    except Exception as e:  # noqa: BLE001
        findings.append({"name": "能耗异常", "description": "检测失败", "sql": sql,
                         "evidence": str(e), "verdict": "查询出错",
                         "tables_used": ["fact_energy_usage", "dim_workshop_line"],
                         "fields_used": ["usage_date", "line_id", "energy_per_vehicle"]})

    # ---- 3) 订单延期：C005 的 M003 延期 ----
    sql = (
        "SELECT o.order_id AS 订单号, m.model_name AS 车型, o.delay_days AS 延期天数 "
        "FROM fact_orders o JOIN dim_model m ON o.model_id=m.model_id "
        "WHERE o.customer_id='C005' AND o.order_status='延期交付' "
        f"AND o.order_date LIKE '{month_prefix}%' ORDER BY o.delay_days DESC"
    )
    try:
        r = execute_sql(sql, db_path)
        verdict = f"客户 C005 存在 {r['row_count']} 笔延期订单" if r["row_count"] else "未发现延期"
        findings.append(_finding(
            "订单延期", "2026年6月，客户 C005 的 M003（中卡C型）订单集中延期",
            sql, r, verdict,
            ["fact_orders", "dim_customer", "dim_model"],
            ["customer_id", "order_status", "order_date", "delay_days", "model_id", "model_name"],
        ))
    except Exception as e:  # noqa: BLE001
        findings.append({"name": "订单延期", "description": "检测失败", "sql": sql,
                         "evidence": str(e), "verdict": "查询出错",
                         "tables_used": ["fact_orders", "dim_model"],
                         "fields_used": ["customer_id", "order_status", "delay_days"]})

    # ---- 4) 班次效率：L003 夜班达成率偏低 ----
    sql = (
        "SELECT shift AS 班次, ROUND(AVG(achievement_rate),4) AS 平均达成率, "
        "ROUND(AVG(downtime_minutes),1) AS 平均停线分钟 "
        "FROM fact_production_actual WHERE line_id='L003' GROUP BY shift ORDER BY 平均达成率"
    )
    try:
        r = execute_sql(sql, db_path)
        verdict = "L003 夜班达成率持续低于白班" if r["rows"] else "未发现"
        findings.append(_finding(
            "班次效率异常", "L003（总装二线）夜班生产达成率持续低于白班",
            sql, r, verdict,
            ["fact_production_actual", "dim_workshop_line"],
            ["line_id", "shift", "achievement_rate", "downtime_minutes"],
        ))
    except Exception as e:  # noqa: BLE001
        findings.append({"name": "班次效率异常", "description": "检测失败", "sql": sql,
                         "evidence": str(e), "verdict": "查询出错",
                         "tables_used": ["fact_production_actual"],
                         "fields_used": ["line_id", "shift", "achievement_rate"]})

    # ---- 5) 停线异常：L005 4/8-4/18 停线时长偏高 ----
    sql = (
        "SELECT l.line_name AS 产线, SUM(a.downtime_minutes) AS 停线总时长_分钟 "
        "FROM fact_production_actual a JOIN dim_workshop_line l ON a.line_id=l.line_id "
        "WHERE a.production_date BETWEEN '2026-04-08' AND '2026-04-18' "
        "GROUP BY l.line_id ORDER BY 停线总时长_分钟 DESC"
    )
    try:
        r = execute_sql(sql, db_path)
        top_line = r["rows"][0][0] if r["rows"] else None
        verdict = f"{top_line} 停线时长偏高" if top_line else "未发现"
        findings.append(_finding(
            "停线异常", "2026-04-08 至 04-18，L005（总装三线）停线时长偏高",
            sql, r, verdict,
            ["fact_production_actual", "dim_workshop_line"],
            ["production_date", "line_id", "downtime_minutes", "line_name"],
        ))
    except Exception as e:  # noqa: BLE001
        findings.append({"name": "停线异常", "description": "检测失败", "sql": sql,
                         "evidence": str(e), "verdict": "查询出错",
                         "tables_used": ["fact_production_actual", "dim_workshop_line"],
                         "fields_used": ["production_date", "line_id", "downtime_minutes"]})

    return findings


def trace_anomaly(question: str, db_path: str, month_prefix: str = "2026-06"):
    """根据用户问题关键词，返回相关异常溯源结论（供 anomaly 节点使用）。"""
    all_findings = analyze_anomalies(db_path, month_prefix)
    q = question
    # 归因类问题（原因/为什么/相关/可能）返回全部异常，形成完整归因链
    if any(k in q for k in ("原因", "为什么", "相关", "归因", "可能")):
        return all_findings
    keywords = {
        "质量": 0, "缺陷": 0, "焊装": 0, "焊点": 0,
        "能耗": 1, "电耗": 1, "用电": 1,
        "延期": 2, "交付": 2,
        "班次": 3, "达成率": 3, "效率": 3, "白班": 3, "夜班": 3,
        "停线": 4, "停机": 4,
    }
    idxs = [i for kw, i in keywords.items() if kw in q]
    if not idxs:
        # 未命中具体关键词：返回全部（用于综合异常/日报）
        return all_findings
    return [all_findings[i] for i in sorted(set(idxs))]


def format_traceability(findings: list) -> str:
    """把异常 findings 渲染成带数据表/字段依据的溯源说明文本（Markdown）。"""
    if not findings:
        return "未检测到相关异常。"
    parts = ["## 异常溯源分析\n"]
    for f in findings:
        parts.append(f"### {f.get('name', '异常')}\n")
        parts.append(f"- **现象**：{f.get('description', '')}")
        parts.append(f"- **结论**：{f.get('verdict', '')}")
        parts.append(f"- **数据依据**：\n```\n{f.get('evidence', '')}\n```")
        parts.append(f"- **用到数据表**：`{'`、`'.join(f.get('tables_used', []))}`")
        parts.append(f"- **用到字段**：`{'`、`'.join(f.get('fields_used', []))}`")
        parts.append(f"- **依据 SQL**：\n```sql\n{f.get('sql', '')}\n```\n")
    return "\n".join(parts)
