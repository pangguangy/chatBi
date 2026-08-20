# -*- coding: utf-8 -*-
"""
schema.py —— 数据库 schema 描述、Prompt 构造、溯源字段抽取。
"""
import re

# 所有数据表的中文说明
TABLE_COMMENTS = {
    "dim_model": "车型维表",
    "dim_workshop_line": "产线维表",
    "dim_customer": "客户维表",
    "fact_orders": "订单事实表",
    "fact_production_plan": "生产计划事实表",
    "fact_production_actual": "生产实绩事实表",
    "fact_quality_defects": "质量缺陷事实表",
    "fact_energy_usage": "能耗事实表",
    "schema_relationships": "表关系元数据",
    "question_public": "公开测试问题集",
    "question_hidden": "隐藏测试问题集",
}

# 事实表 / 维表（用于 SQL 溯源抽取）
DATA_TABLES = [
    "dim_model", "dim_workshop_line", "dim_customer",
    "fact_orders", "fact_production_plan", "fact_production_actual",
    "fact_quality_defects", "fact_energy_usage",
]

# 全部字段名（用于从 SQL 中抽取「用到的字段」）
ALL_COLUMNS = {
    # dim_model
    "model_id", "model_name", "product_series", "vehicle_type",
    "standard_cycle_minutes", "standard_energy_kwh", "launch_year",
    # dim_workshop_line
    "line_id", "line_name", "workshop", "shift_type",
    "designed_daily_capacity", "main_model_series",
    # dim_customer
    "customer_id", "customer_name", "customer_type", "region", "priority_level",
    # fact_orders
    "order_id", "order_date", "planned_delivery_date", "actual_delivery_date",
    "order_quantity", "delivered_quantity", "order_status", "delay_days",
    # fact_production_plan
    "plan_id", "production_date", "shift", "planned_quantity",
    # fact_production_actual
    "actual_id", "actual_quantity", "working_hours", "downtime_minutes",
    "achievement_rate",
    # fact_quality_defects
    "defect_id", "defect_date", "process", "defect_type",
    "defect_count", "severity", "rework_status",
    # fact_energy_usage
    "energy_id", "usage_date", "electricity_kwh", "water_ton", "gas_m3",
    "energy_per_vehicle",
}

# 字段中文说明（供 Prompt 与溯源展示）
FIELD_COMMENTS = {
    "model_id": "车型ID(主键，如 M003)", "model_name": "车型名称(如 中卡C型)",
    "product_series": "产品系列(轻卡/中卡/重卡/专用车)", "vehicle_type": "车辆类型(燃油车/新能源车)",
    "standard_cycle_minutes": "标准单车节拍(分钟)", "standard_energy_kwh": "单车理论电耗(kWh)",
    "launch_year": "上市年份",
    "line_id": "产线ID(主键，如 L002)", "line_name": "产线名称(如 焊装一线)",
    "workshop": "所属车间", "shift_type": "班次类型", "designed_daily_capacity": "设计日产能",
    "main_model_series": "主要生产系列",
    "customer_id": "客户ID(主键，如 C005)", "customer_name": "客户名称(已匿名化)",
    "customer_type": "客户类型", "region": "区域", "priority_level": "客户优先级",
    "order_id": "订单ID(主键)", "order_date": "下单日期", "planned_delivery_date": "计划交付日期",
    "actual_delivery_date": "实际交付日期(未交付为空)", "order_quantity": "订单数量",
    "delivered_quantity": "已交付数量", "order_status": "订单状态(已交付/延期交付/生产中)",
    "delay_days": "延期天数(未交付为空)",
    "plan_id": "生产计划ID(主键)", "production_date": "生产日期", "shift": "班次(白班/夜班)",
    "planned_quantity": "计划产量",
    "actual_id": "生产实绩ID(主键)", "actual_quantity": "实际产量", "working_hours": "工作时长",
    "downtime_minutes": "停线时长(分钟)", "achievement_rate": "生产达成率(实际/计划)",
    "defect_id": "缺陷ID(主键)", "defect_date": "缺陷日期", "process": "工序(如 焊装)",
    "defect_type": "缺陷类型(如 焊点虚焊)", "defect_count": "缺陷数量",
    "severity": "严重程度", "rework_status": "返修状态",
    "energy_id": "能耗ID(主键)", "usage_date": "能耗日期", "electricity_kwh": "电耗(kWh)",
    "water_ton": "水耗(吨)", "gas_m3": "气耗(立方米)",
    "energy_per_vehicle": "单车电耗(kWh/台)",
}

# 表关系描述（用于 Text-to-SQL Prompt）
RELATIONSHIPS = (
    "dim_customer.customer_id -> fact_orders.customer_id (1:N)\n"
    "dim_model.model_id -> fact_orders.model_id (1:N)\n"
    "dim_model.model_id -> fact_production_plan.model_id (1:N)\n"
    "dim_workshop_line.line_id -> fact_production_plan.line_id (1:N)\n"
    "fact_production_plan.plan_id -> fact_production_actual.plan_id (1:1)\n"
    "dim_model.model_id -> fact_production_actual.model_id (1:N)\n"
    "dim_workshop_line.line_id -> fact_production_actual.line_id (1:N)\n"
    "dim_model.model_id -> fact_quality_defects.model_id (1:N)\n"
    "dim_workshop_line.line_id -> fact_quality_defects.line_id (1:N)\n"
    "dim_model.model_id -> fact_energy_usage.model_id (1:N)\n"
    "dim_workshop_line.line_id -> fact_energy_usage.line_id (1:N)\n"
    "业务关联：fact_orders(model_id+planned_delivery_date) ~ fact_production_actual(model_id+production_date)\n"
    "业务关联：fact_orders(model_id+planned_delivery_date) ~ fact_quality_defects(model_id+defect_date)\n"
    "业务关联：fact_production_actual(line_id+model_id+production_date) ~ fact_energy_usage(line_id+model_id+usage_date)"
)

# 已知业务 ID 与名称对照（供启发式匹配与溯源展示）
MODEL_NAMES = {
    "M001": "轻卡A型", "M002": "轻卡B型", "M003": "中卡C型", "M004": "中卡D型",
    "M005": "重卡E型", "M006": "重卡F型", "M007": "专用车G型", "M008": "专用车H型",
}
LINE_NAMES = {
    "L001": "总装一线", "L002": "焊装一线", "L003": "总装二线", "L004": "涂装一线",
    "L005": "总装三线", "L006": "焊装二线", "L007": "总装四线", "L008": "检测一线",
}


def extract_tables_fields(sql: str):
    """从一条 SQL 中抽取「用到的数据表」与「用到的字段」（用于溯源说明）。"""
    if not sql:
        return [], []
    tables = sorted({t for t in DATA_TABLES if re.search(rf"\b{t}\b", sql)})
    fields = sorted({tok for tok in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql)
                     if tok in ALL_COLUMNS})
    return tables, fields


def build_schema_prompt() -> str:
    """构造 Text-to-SQL 的 schema 说明 Prompt。"""
    lines = ["以下是 SQLite 数据库的表结构（表名与字段名均为英文）："]
    for t in DATA_TABLES:
        cols = _COLUMNS_OF(t)
        desc = "、".join(f"{c}({FIELD_COMMENTS.get(c, '')})" for c in cols)
        lines.append(f"- {t}（{TABLE_COMMENTS[t]}）：{desc}")
    lines.append("\n主外键关系：\n" + RELATIONSHIPS)
    return "\n".join(lines)


def _COLUMNS_OF(table: str):
    """返回每张表的字段列表（用于构建 schema 说明）。"""
    cols_map = {
        "dim_model": ["model_id", "model_name", "product_series", "vehicle_type",
                      "standard_cycle_minutes", "standard_energy_kwh", "launch_year"],
        "dim_workshop_line": ["line_id", "line_name", "workshop", "shift_type",
                              "designed_daily_capacity", "main_model_series"],
        "dim_customer": ["customer_id", "customer_name", "customer_type", "region", "priority_level"],
        "fact_orders": ["order_id", "customer_id", "model_id", "order_date",
                        "planned_delivery_date", "actual_delivery_date", "order_quantity",
                        "delivered_quantity", "order_status", "delay_days"],
        "fact_production_plan": ["plan_id", "production_date", "line_id", "model_id",
                                 "shift", "planned_quantity"],
        "fact_production_actual": ["actual_id", "plan_id", "production_date", "line_id",
                                   "model_id", "shift", "actual_quantity", "working_hours",
                                   "downtime_minutes", "achievement_rate"],
        "fact_quality_defects": ["defect_id", "defect_date", "line_id", "model_id",
                                 "process", "defect_type", "defect_count", "severity",
                                 "rework_status"],
        "fact_energy_usage": ["energy_id", "usage_date", "workshop", "line_id", "model_id",
                              "shift", "electricity_kwh", "water_ton", "gas_m3",
                              "energy_per_vehicle"],
    }
    return cols_map.get(table, [])
