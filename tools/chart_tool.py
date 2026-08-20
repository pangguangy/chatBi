# -*- coding: utf-8 -*-
"""
chart_tool.py —— 图表生成工具（柱状图 / 折线图）+ 表格渲染。

采用 matplotlib 后端 Agg 无界面渲染，输出 base64 PNG 供 Streamlit 展示。
配色采用一套已验证的、色盲友好的分类色板（蓝/橙/青/黄/品红/绿/紫/红），
顺序固定、不做彩虹色、单图不做双坐标轴。
"""
import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# 分类色板（固定顺序，色盲友好）
CATEGORICAL_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
# 图表墨色
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"

# 中文字体候选（本地 Windows 用 SimHei/微软雅黑；HuggingFace Spaces 用 Noto CJK）
_FONT_CANDIDATES = [
    "Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC", "WenQuanYi Zen Hei",
    "SimHei", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
    "Droid Sans Fallback", "Arial Unicode MS",
]


def setup_chinese_font():
    """配置 matplotlib 中文字体，返回选中的字体名（无则 None）。"""
    try:
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:
        available = set()
    for name in _FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name] + _FONT_CANDIDATES
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _clean(v):
    return None if v is None else (int(v) if isinstance(v, float) and v == int(v) else v)


def _num_or_nan(v):
    """把数值列取值转为 float，None/非数值 -> NaN（matplotlib 会安全跳过，避免带 NULL 列绘图崩溃）。"""
    v = _clean(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return float("nan")


def generate_chart(result: dict, chart_type: str = "bar", title: str = "",
                   max_bars: int = 20):
    """根据查询结果生成图表，返回 base64 PNG 字符串；不适合/无数据返回 None。

    chart_type: bar(柱状图) / line(折线图)；table/text 不生成图片。
    """
    if not result or not result.get("rows"):
        return None
    if chart_type in ("table", "text", "report", "auto"):
        return None

    columns = [str(c) for c in result["columns"]]
    rows = result["rows"]
    if len(columns) < 2 or not rows:
        return None

    setup_chinese_font()

    x_vals = [str(r[0]) if r[0] is not None else "" for r in rows]
    # 找出数值列
    num_idx = []
    for i in range(1, len(columns)):
        vals = [r[i] for r in rows if r[i] is not None]
        if vals and all(_is_number(v) for v in vals):
            num_idx.append(i)
    if not num_idx:
        return None

    # 折线图可容纳更多 x，柱状图限制数量避免拥挤
    if chart_type == "bar" and len(x_vals) > max_bars:
        x_vals = x_vals[:max_bars]
        rows = rows[:max_bars]

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=130)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    x_pos = list(range(len(x_vals)))
    n_series = len(num_idx)

    if chart_type == "line":
        for j, ci in enumerate(num_idx):
            y = [_num_or_nan(r[ci]) for r in rows]
            color = CATEGORICAL_COLORS[j % len(CATEGORICAL_COLORS)]
            ax.plot(x_pos, y, color=color, linewidth=2.5, marker="o",
                    markersize=6, label=columns[ci], zorder=3)
        ax.set_xlabel("")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_vals, rotation=30, ha="right", fontsize=9, color=INK_SECONDARY)
    else:  # bar
        width = 0.72 if n_series == 1 else 0.8 / n_series
        for j, ci in enumerate(num_idx):
            y = [_num_or_nan(r[ci]) for r in rows]
            color = CATEGORICAL_COLORS[j % len(CATEGORICAL_COLORS)]
            offset = (j - (n_series - 1) / 2) * width if n_series > 1 else 0
            xs = [p + offset for p in x_pos]
            ax.bar(xs, y, width=width * (0.9 if n_series > 1 else 1),
                   color=color, label=columns[ci], zorder=3)
            # 少量柱子时直接标注数值
            if len(x_vals) <= 10 and n_series == 1:
                for px, py in zip(xs, y):
                    if py == py:
                        ax.text(px, py, f"{py:,.0f}" if float(py) == int(py) else f"{py:,.2f}",
                                ha="center", va="bottom", fontsize=8, color=INK_SECONDARY)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_vals, rotation=30, ha="right", fontsize=9, color=INK_SECONDARY)

    # 图表修饰：细网格、弱化坐标轴
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors=INK_MUTED, labelsize=9)

    if title:
        ax.set_title(title, fontsize=13, color=INK_PRIMARY, pad=12, loc="left")
    if n_series > 1:
        ax.legend(frameon=False, fontsize=9, loc="best")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#fcfcfb")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def result_to_records(result: dict) -> list:
    """把 {columns, rows} 转成 [{列名: 值}, ...]，便于展示与导出。"""
    if not result:
        return []
    cols = [str(c) for c in result["columns"]]
    return [dict(zip(cols, [_clean(v) for v in row])) for row in result["rows"]]


def render_markdown_table(result: dict, max_rows: int = 20) -> str:
    """把查询结果渲染成 Markdown 表格字符串。"""
    if not result:
        return "（无数据）"
    cols = [str(c) for c in result["columns"]]
    rows = result["rows"][:max_rows]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join("" if v is None else str(_clean(v)) for v in r) + " |")
    if result["row_count"] > max_rows:
        lines.append(f"\n*（仅展示前 {max_rows} 行，共 {result['row_count']} 行）*")
    return "\n".join(lines)
