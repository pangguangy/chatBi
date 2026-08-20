# 制造业运营态势 Chat-BI Agent

基于 **Python + LangGraph + Streamlit + SQLite + Claude API** 的制造业经营数据分析 Agent。
管理者用自然语言直接问数，Agent 自动完成「任务规划 → 生成 SQL → 执行查询 → 生成图表 →
异常溯源 → 自然语言总结」的完整闭环，支持运营日报/月报一键生成。

> 赛题 3：制造业运营态势 Chat-BI Agent · 代码开发模式

---

## 一、功能特性

| 必选能力 | 实现说明 |
|---|---|
| 自然语言问数 | 覆盖产量、订单、质量缺陷、能耗、达成率、延期等业务问题 |
| 多表关联分析 | dim 维表 + fact 事实表多表 JOIN（车型/产线/客户/计划/实绩/缺陷/能耗） |
| 图表生成 | 根据问题自动生成柱状图 / 折线图 / 表格 / 文字摘要 |
| 运营日报/月报 | 输入月份自动生成含产量、质量、能耗、延期概览的运营态势日报 |
| 异常解释 | 对质量、能耗、延期、班次效率、停线等异常给出数据依据 |
| 溯源说明 | 明确写出结论用到哪些数据表、哪些字段 |
| 任务规划 + 工具调用 | LangGraph 状态机：规划节点 + SQL/图表/异常/报告工具节点 |
| SQL 自动修正重试 | SQL 执行出错时，Agent 根据报错自动修正 SQL 并重试（上限 3 次） |

---

## 二、项目结构

```
制造业运营态势ChatBI-Agent/
├── config.py              # 全局配置（Claude API 密钥位置、数据/数据库路径、重试次数）
├── db_init.py             # 读取全部 CSV -> 导入 SQLite（跳过 __MACOSX、处理编码）
├── agent_graph.py         # LangGraph Agent 核心（规划/执行/修正/图表/报告/异常/汇总）
├── app.py                 # Streamlit 交互页面
├── requirements.txt       # 依赖清单
├── packages.txt           # HuggingFace Spaces 中文字体依赖
├── .streamlit/config.toml # Streamlit 服务器配置（HF Spaces headless）
├── README.md              # 本文件
└── tools/
    ├── __init__.py
    ├── schema.py          # 数据库 schema 描述 + Prompt 构造 + 溯源字段抽取
    ├── llm.py             # Claude API 调用封装（Anthropic 官方 SDK）
    ├── sql_tool.py        # SQL 执行工具 + 启发式 SQL 兜底库 + 日报查询
    ├── chart_tool.py      # 图表生成工具（柱状图/折线图）+ 表格渲染
    └── anomaly_tool.py    # 异常检测与溯源分析工具（含数据表/字段依据）
```

---

## 三、本地运行步骤

### 1. 环境要求
- Python 3.9+
- 可访问 Anthropic Claude API 的密钥（离线模式也可运行，见下文）

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置 Claude API 密钥（可选）
```bash
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY="sk-ant-..."

# Linux / macOS
export ANTHROPIC_API_KEY="sk-ant-..."
```
或直接修改 `config.py` 中的 `CLAUDE_API_KEY` 变量（本地调试用）。

> 不配置密钥也可运行：系统自动进入**离线模式**，使用内置的启发式 SQL 库 +
> 确定性总结，完整演示图表生成、运营日报、异常溯源等全流程。

### 4. 初始化数据库（读取 CSV -> SQLite）
```bash
python db_init.py
```
程序会自动读取下方数据目录下**全部 csv** 文件导入 SQLite，并：
- **忽略 `__MACOSX` 文件夹**（mac 系统解压产生的附属目录）
- 忽略 `.DS_Store`、`Thumbs.db` 等隐藏文件
- 自动处理 CSV 编码异常（utf-8-sig / utf-8 / gb18030 / gbk / latin-1）

### 5. 启动 Web 页面
```bash
streamlit run app.py
```
浏览器打开 `http://localhost:8501`，输入自然语言问题即可。

---

## 四、数据集路径说明

**本地数据主目录（默认）：**

```
D:\桌面\长洙\三命题\制造业运营态势ChatBI-Agent-赛题说明\赛题-制造业运营态势ChatBI
```

> 程序只读取上述目录下（含子目录，例如其中的 `制造业运营态势ChatBI模拟数据集` 子文件夹）
> 的 **csv 文件**，**忽略 `__MACOSX` 文件夹**。
>
> 若你的数据集放在别处，可二选一：
> - 修改 `config.py` 中 `DATA_DIR`；
> - 或设置环境变量 `CHATBI_DATA_DIR`（启动时指定）。

数据表一览（导入 SQLite 后即表名）：

| 类型 | 表名 | 说明 |
|---|---|---|
| 维表 | `dim_model` | 车型维表（M001~M008） |
| 维表 | `dim_workshop_line` | 产线维表（L001~L008） |
| 维表 | `dim_customer` | 客户维表（C001~C060） |
| 事实表 | `fact_orders` | 订单事实表 |
| 事实表 | `fact_production_plan` | 生产计划事实表 |
| 事实表 | `fact_production_actual` | 生产实绩事实表 |
| 事实表 | `fact_quality_defects` | 质量缺陷事实表 |
| 事实表 | `fact_energy_usage` | 能耗事实表 |
| 元数据 | `schema_relationships` / `question_public` / `question_hidden` | 关系/问题集 |

---

## 五、HuggingFace Spaces 部署说明

1. 新建 Space，选择 **Streamlit** SDK；
2. 将本项目文件上传到仓库（`app.py` 必须位于仓库根目录）；
3. 把数据集 csv 文件放到仓库根目录 `data/` 文件夹（程序会自动回退读取 `./data`）；
   或直接提交本地已生成的 `chatbi.db` 到仓库根目录，跳过在线初始化；
4. 在 Space 的 **Settings -> Secrets** 中添加 `ANTHROPIC_API_KEY`；
5. `requirements.txt`、`packages.txt`（自动安装中文字体 `fonts-noto-cjk`）、
   `.streamlit/config.toml`（headless）均已就绪，Space 会自动安装并启动；
6. 等待构建完成后，即可获得公开访问链接（`https://<user>-<space>.hf.space`）。

---

## 六、Agent 工作原理

```
用户问题
   │
   ▼
[planning]  意图识别(query/report/anomaly) + Claude Text-to-SQL + 图表类型决策
   │
   ├─ 日报/月报 ──► [report] 运行产量/质量/能耗/延期 4 类查询 + 异常检测
   │
   └─ 普通/异常问数
        │
        ▼
   [execute_sql]  执行 SQL（只读校验）
        │ 失败
        ├────► [fix_sql] Claude 依据报错自动修正 SQL ──► 回到 execute_sql（≤3 次重试）
        │ 成功
        ▼
   [chart]       生成柱状图/折线图（或表格/文字）
        │
   [anomaly]     异常溯源（数据表 + 字段依据）
        ▼
   [finalize]   自然语言总结 + 溯源说明
```

---

## 七、测试

可在页面点击示例问题按钮，或命令行快速验证：

```bash
python agent_graph.py "2026年6月各车型的总产量是多少？按产量从高到低排序。"
```

完整测试用例清单见 `docs/测试用例清单.md`（覆盖全部必选能力）。

---

## 八、竞赛提交信息（对照）

| 表单项 | 内容 |
|---|---|
| 作品实现方式 | 代码开发 |
| 推荐平台/工具 | 未使用推荐平台或工具 |
| 其他平台/模型 | LangGraph+Streamlit，调用 Claude API |
| 作品访问方式 | 本地运行（附录屏演示视频） |
| 代码提交 | https://github.com/pangguangy/chatbi-manufacturing-agent |
