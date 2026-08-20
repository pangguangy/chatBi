# -*- coding: utf-8 -*-
"""
tools 包：Agent 所需的全部工具模块。

  - schema.py        数据库 schema 描述、Prompt 构造、溯源字段抽取
  - llm.py           Claude API 调用封装（Anthropic 官方 SDK）
  - sql_tool.py      SQL 执行工具 + 启发式 SQL 兜底库 + 日报查询
  - chart_tool.py    图表生成工具（柱状图 / 折线图）+ 表格渲染
  - anomaly_tool.py  异常检测与溯源分析工具（含数据表 / 字段依据）
"""
