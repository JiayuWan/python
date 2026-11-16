"""
网址来源：从 Excel 里读公告详情链接。

原来的流程是 list_spider 翻列表页拿链接，如果链接清单已经在表里，
这一步可以直接跳过：读表 → 拿 URL 列表 → 后面照旧走详情抓取。

识别规则：
1. 前 10 行里如果有单元格包含「链接 / 网址 / 地址 / url / link / href / 详情」，
   就认定它所在的列是「网址列」，只从这些列里取；
2. 一个表头都没识别到（比如第一行直接就是网址），就全表扫描，把所有 http(s) 开头的串捞出来；
3. 单元格里如果是 =HYPERLINK("http://...","点我") 这种公式文本，也能把里面的网址取出来；
4. 结果去重、去掉尾部的中文标点，只保留 http/https。

单独测试：
    python url_source.py 网址表.xlsx
"""

import re
import sys
from pathlib import Path

import pandas as pd

# 网址允许的字符：遇到空白、引号、尖括号、中英文逗号分号顿号括号就截断
URL_PATTERN = re.compile(r'https?://[^\s"\'<>，,；;、）)】\]}]+', re.IGNORECASE)

# 表头里出现这些字样，就认为这一列是网址列
HEADER_HINTS = ("链接", "网址", "地址", "url", "link", "href", "详情")

# 表头最多往下找几行（有些表前面有标题行、说明行）
HEADER_SCAN_ROWS = 10


def extract_urls(value) -> list[str]:
    """从一个单元格的文本里取出所有网址。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []

    urls = []
    for match in URL_PATTERN.findall(text):
        cleaned = match.rstrip("。.…·")
        if cleaned:
            urls.append(cleaned)
    return urls


def read_urls(path) -> list[str]:
    """读 Excel（支持多个 sheet），返回去重后的网址列表。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"找不到网址表：{file_path}")

    sheets = pd.read_excel(file_path, sheet_name=None, dtype=str, header=None)

    collected: list[str] = []

    for sheet_name, df in sheets.items():
        if df.empty:
            continue

        # 1. 找表头里疑似「网址列」的列号
        #    限制长度，避免把「详见网址：http://...」这种正文误当成表头
        url_columns: set[int] = set()
        for row_index in range(min(HEADER_SCAN_ROWS, df.shape[0])):
            for col_index in range(df.shape[1]):
                cell = str(df.iat[row_index, col_index] or "").strip().lower()
                # 本身已经是网址的单元格是数据行，不是表头，跳过
                # 否则「http://xx/备注里的链接」这种短文本会因含「链接」被误判成表头
                if URL_PATTERN.search(cell):
                    continue
                if cell and len(cell) <= 20 and any(hint in cell for hint in HEADER_HINTS):
                    url_columns.add(col_index)

        # 2. 识别到了就只扫这些列，否则全表扫
        target_columns = sorted(url_columns) if url_columns else range(df.shape[1])

        for row_index in range(df.shape[0]):
            for col_index in target_columns:
                collected.extend(extract_urls(df.iat[row_index, col_index]))

    # 3. 补一遍公式单元格。
    #    =HYPERLINK("http://...","点我") 这种，pandas 读到的只是缓存下来的显示文字「点我」，
    #    公式原文拿不到，网址就漏了。所以用 openpyxl 以「不取缓存值」的方式再读一遍。
    if file_path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(file_path, data_only=False, read_only=True)
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if isinstance(cell, str) and "HYPERLINK" in cell.upper():
                            collected.extend(extract_urls(cell))
            workbook.close()
        except Exception:
            # 公式这层是加分项，读不到就算了，不影响正常表
            pass

    # 4. 去重，保留第一次出现的顺序
    unique_urls = list(dict.fromkeys(collected))
    return unique_urls


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python url_source.py 网址表.xlsx")
        raise SystemExit(1)

    urls = read_urls(sys.argv[1])
    print(f"共读取 {len(urls)} 个网址（已去重）")
    for index, url in enumerate(urls[:10], start=1):
        print(f"  {index}. {url}")
    if len(urls) > 10:
        print(f"  ……还有 {len(urls) - 10} 个")
