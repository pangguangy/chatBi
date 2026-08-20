# -*- coding: utf-8 -*-
"""
db_init.py —— 读取指定目录下全部 CSV，导入 SQLite 数据库。

功能：
  1. 递归扫描数据目录下所有 .csv 文件；
  2. 自动跳过 __MACOSX 目录、隐藏文件（.DS_Store / Thumbs.db 等）；
  3. 自动处理 CSV 编码异常（utf-8-sig / utf-8 / gb18030 / gbk / latin-1 依次尝试）；
  4. 去掉列名首部的 BOM 字符（\\ufeff），保证列名干净；
  5. 每个 csv 以「文件名（去掉 .csv）」作为 SQLite 表名写入；
  6. 数值列自动推断为 INTEGER/REAL，日期列保留为 TEXT（YYYY-MM-DD，便于范围比较）。

用法：
  python db_init.py                   # 使用 config 中的默认数据目录
  python db_init.py --dir <目录>       # 指定数据目录
  python db_init.py --db <db路径>      # 指定输出数据库路径
"""
import argparse
import os
import sqlite3

import pandas as pd

import config

# 需要跳过的目录片段（__MACOSX 为 mac 系统解压产生的附属目录）
SKIP_DIR_MARKERS = ("__MACOSX",)
SKIP_FILE_MARKERS = (".DS_Store", "Thumbs.db")

# 编码探测顺序：优先 utf-8-sig（处理带 BOM 的中文 csv），再 gb18030（兼容 GBK/GB2312）
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")


def detect_encoding(filepath: str) -> str:
    """依次尝试多种编码，返回首个能完整解码的编码名。"""
    for enc in ENCODINGS:
        try:
            with open(filepath, "r", encoding=enc) as f:
                f.read()
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "utf-8-sig"  # 兜底


def iter_csv_files(data_dir: str):
    """递归产出数据目录下所有 csv 文件的绝对路径，跳过 __MACOSX 与隐藏文件。"""
    for root, dirs, files in os.walk(data_dir):
        # 就地过滤目录：跳过 __MACOSX 及所有隐藏目录（以 . 开头）
        dirs[:] = [
            d for d in dirs
            if not any(m in d for m in SKIP_DIR_MARKERS) and not d.startswith(".")
        ]
        for fname in files:
            if fname.startswith(".") or any(m in fname for m in SKIP_FILE_MARKERS):
                continue
            if fname.lower().endswith(".csv"):
                yield os.path.join(root, fname)


def table_name_of(filepath: str) -> str:
    return os.path.splitext(os.path.basename(filepath))[0]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """清理列名，并把「全整数的浮点列」转成整数，避免 delay_days 显示为 2.0。"""
    # 去掉列名首部 BOM 与首尾空格
    df.columns = [str(c).lstrip("﻿").strip() for c in df.columns]
    # 删除完全为空的列
    df = df.dropna(axis=1, how="all")
    # 全整数的浮点列（如 delay_days）转为整数，保留 None 表示 NULL
    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            non_null = df[col].dropna()
            if len(non_null) and (non_null == non_null.round()).all():
                df[col] = df[col].apply(
                    lambda v: int(v) if pd.notnull(v) else None
                )
    return df


def init_db(data_dir: str, db_path: str) -> dict:
    """把数据目录下全部 csv 导入 SQLite，返回 {表名: 行数} 汇总。"""
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(
            f"数据目录不存在：{data_dir}\n"
            f"请确认路径，或通过 --dir 参数 / 环境变量 CHATBI_DATA_DIR 指定。"
        )

    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    summary = {}
    csv_files = list(iter_csv_files(data_dir))
    if not csv_files:
        raise RuntimeError(
            f"目录 {data_dir} 下未找到任何 csv 文件（已跳过 __MACOSX 目录）。"
        )

    for fpath in sorted(csv_files):
        table = table_name_of(fpath)
        enc = detect_encoding(fpath)
        try:
            df = pd.read_csv(fpath, encoding=enc)
        except Exception:
            # 兜底：即使探测成功仍可能因个别坏字符失败，用 errors=replace 强制读入
            df = pd.read_csv(fpath, encoding="utf-8", errors="replace")

        df = _normalize_columns(df)
        df.to_sql(table, conn, if_exists="replace", index=False)
        summary[table] = len(df)
        print(f"[OK] 已导入 {table:<28} {len(df):>6} 行  (编码: {enc})")

    conn.commit()
    conn.close()
    print(f"\n数据库生成完成：{db_path}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="CSV -> SQLite 初始化脚本")
    parser.add_argument("--dir", default=None, help="数据目录（默认使用 config.DATA_DIR）")
    parser.add_argument("--db", default=None, help="输出数据库路径（默认使用 config.DB_PATH）")
    args = parser.parse_args()

    data_dir = args.dir or config.resolve_data_dir()
    db_path = args.db or config.DB_PATH

    try:
        summary = init_db(data_dir, db_path)
        print(f"\n共导入 {len(summary)} 张表。")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"[ERROR] {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
