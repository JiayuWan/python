"""
招标公告看板 — FastAPI 后端

启动：uvicorn api:app --host 0.0.0.0 --port 8000
页面：http://本机IP:8000

和原 Streamlit 版的关系：
- 爬虫（detail_spider）、关键词规则（keyword_filter）、导出（excel_export）沿用原代码；
- 站点列表页那条路径已摘除：只从导入的网址表采集，不碰 list_spider 里写死的地址；
- 只把 app.py 的看板部分换成 JSON 接口 + static/index.html，页面自己渲染，手机端能控样式。
"""

import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from list_spider import probe_urls
from url_source import read_urls

BASE_DIR = Path(__file__).resolve().parent
EXCEL_PATH = BASE_DIR / "公告筛选结果.xlsx"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 网址表没必要超过 10MB

app = FastAPI(title="招标公告看板")

# 本机自用先放开，部署到服务器时务必改成本单位的具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================== Excel 读取与缓存 ========================
# 用文件修改时间做缓存键：Excel 被爬虫重写后，下一次请求自动重新加载
_df_cache = {"mtime": None, "df": None}
_df_lock = threading.Lock()

COL_TITLE = "公告标题"
COL_DATE = "发布日期"
COL_PROJECT = "项目名称"
COL_PURCHASER = "采购人/招标人"
COL_AGENCY = "采购代理机构/招标代理机构"
COL_METHOD = "采购方式"
COL_NO = "项目编号"
COL_BUDGET = "预算金额"
COL_DEADLINE = "投标/响应文件截止时间"
COL_SCOPE = "项目内容/采购范围"
COL_URL = "公告详情链接"
COL_KW_TITLE = "标题命中关键词"
COL_KW_CONTENT = "正文命中关键词"


def load_df() -> pd.DataFrame:
    """读取 Excel，按修改时间缓存，避免每次请求都解析文件。"""
    if not EXCEL_PATH.exists():
        return pd.DataFrame()

    mtime = EXCEL_PATH.stat().st_mtime
    with _df_lock:
        if _df_cache["mtime"] == mtime and _df_cache["df"] is not None:
            return _df_cache["df"]

        df = pd.read_excel(EXCEL_PATH, dtype=str).fillna("")
        df["_date"] = pd.to_datetime(df.get(COL_DATE, ""), errors="coerce")
        df = df.sort_values("_date", ascending=False, na_position="last")

        _df_cache["mtime"] = mtime
        _df_cache["df"] = df
        return df


def split_keywords(value: str) -> list[str]:
    return [k.strip() for k in str(value).split("、") if k.strip()]


def row_to_item(row) -> dict:
    return {
        "title": row.get(COL_TITLE, ""),
        "date": row.get(COL_DATE, ""),
        "project": row.get(COL_PROJECT, ""),
        "purchaser": row.get(COL_PURCHASER, ""),
        "agency": row.get(COL_AGENCY, ""),
        "method": row.get(COL_METHOD, ""),
        "no": row.get(COL_NO, ""),
        "budget": row.get(COL_BUDGET, ""),
        "deadline": row.get(COL_DEADLINE, ""),
        "scope": row.get(COL_SCOPE, ""),
        "url": row.get(COL_URL, ""),
        "title_kw": split_keywords(row.get(COL_KW_TITLE, "")),
        "content_kw": split_keywords(row.get(COL_KW_CONTENT, "")),
    }


# ======================== 概览（只做统计） ========================
@app.get("/api/overview")
def overview():
    df = load_df()
    if df.empty:
        return {
            "has_data": False,
            "total": 0,
            "methods": [],
            "latest_date": "",
            "updated_at": "",
        }

    method_col = df[COL_METHOD] if COL_METHOD in df.columns else pd.Series(dtype=str)
    latest = df["_date"].max()

    return {
        "has_data": True,
        "total": int(len(df)),
        "methods": sorted({m for m in method_col if m}),
        "latest_date": latest.strftime("%Y-%m-%d") if pd.notna(latest) else "",
        "updated_at": datetime.fromtimestamp(EXCEL_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


# ======================== 关键词选项 ========================
def keyword_stats(column: str) -> list[dict]:
    """统计某一列里各关键词命中的条数；列不存在时返回空，避免 KeyError。"""
    df = load_df()
    if df.empty or column not in df.columns:
        return []

    counter: dict[str, int] = {}
    for value in df[column]:
        for kw in split_keywords(value):
            counter[kw] = counter.get(kw, 0) + 1

    return [
        {"name": k, "count": c}
        for k, c in sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    ]


@app.get("/api/keywords/title")
def keywords_title():
    """标题命中过的关键词，按条数从多到少。"""
    return {"keywords": keyword_stats(COL_KW_TITLE)}


@app.get("/api/keywords/content")
def keywords_content():
    """正文命中过的关键词，按条数从多到少。"""
    return {"keywords": keyword_stats(COL_KW_CONTENT)}


# ======================== 列表 ========================
@app.get("/api/announcements")
def announcements(
    q: str = "",
    method: str = "",
    kws: str = Query("", description="兼容旧参数：标题或正文命中任意一个即可"),
    kws_title: str = Query("", description="标题关键词，逗号分隔，命中任意一个即可"),
    kws_content: str = Query("", description="正文关键词，逗号分隔，命中任意一个即可"),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
):
    df = load_df()
    if df.empty:
        return {"total": 0, "filtered": 0, "page": page, "pages": 0, "items": []}

    filtered = df

    def split_list(value: str) -> list[str]:
        return [k.strip() for k in value.split(",") if k.strip()]

    def any_word_hit(columns: list[str], words: list[str]) -> pd.Series:
        """在这些列里找这些词，命中任意一个即为 True。"""
        mask = pd.Series(False, index=filtered.index)
        for col in columns:
            if col not in filtered.columns:
                continue
            for word in words:
                mask |= filtered[col].str.contains(word, na=False, regex=False)
        return mask

    if q.strip():
        keyword = q.strip()
        hits = [
            filtered[col].str.contains(keyword, na=False, case=False)
            for col in (COL_TITLE, COL_PROJECT, COL_PURCHASER, COL_NO)
            if col in filtered.columns
        ]
        if hits:
            mask = hits[0]
            for extra in hits[1:]:
                mask = mask | extra
            filtered = filtered[mask]

    if method.strip() and COL_METHOD in filtered.columns:
        filtered = filtered[filtered[COL_METHOD] == method.strip()]

    # 标题关键词、正文关键词各自独立过滤；两个都选时取交集
    title_words = split_list(kws_title)
    if title_words:
        filtered = filtered[any_word_hit([COL_KW_TITLE], title_words)]

    content_words = split_list(kws_content)
    if content_words:
        filtered = filtered[any_word_hit([COL_KW_CONTENT], content_words)]

    # 旧参数 kws：标题或正文任一列命中即可
    legacy_words = split_list(kws)
    if legacy_words:
        filtered = filtered[any_word_hit([COL_KW_TITLE, COL_KW_CONTENT], legacy_words)]

    total_filtered = int(len(filtered))
    pages = (total_filtered + size - 1) // size
    start = (page - 1) * size
    page_df = filtered.iloc[start:start + size]

    return {
        "total": int(len(df)),
        "filtered": total_filtered,
        "page": page,
        "pages": pages,
        "items": [row_to_item(row) for _, row in page_df.iterrows()],
    }


# ======================== 手动刷新（触发爬虫） ========================
# 爬虫是重活，绝不能在请求线程里跑，否则整个页面卡死。
# 用后台线程起子进程跑 main.py，前端轮询 /api/crawl/status 拿进度。
_crawl = {
    "running": False,
    "total": 0,
    "done": 0,
    "matched": 0,
    "percent": 0,
    "last_line": "",
    "started_at": "",
    "finished_at": "",
    "error": "",
    "source": "list",     # list = 翻列表页；excel = 读上传的网址表
    "file": "",
}
_log = deque(maxlen=200)
_crawl_lock = threading.Lock()

_RE_TOTAL = re.compile(r"共获取到\s*(\d+)\s*条公告")
_RE_TOTAL_EXCEL = re.compile(r"从 Excel 读取到\s*(\d+)\s*个网址")
# 网址表里如果是列表页，main.py 会先展开成详情链接，总数以这个为准
_RE_TOTAL_LIST = re.compile(r"展开后得到\s*(\d+)\s*条公告")
_RE_PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")


def _watch_crawl(proc: subprocess.Popen):
    """逐行读子进程输出，解析进度，进程结束后复位状态。"""
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue

            _log.append(line)
            _crawl["last_line"] = line

            m = _RE_TOTAL.search(line) or _RE_TOTAL_EXCEL.search(line) or _RE_TOTAL_LIST.search(line)
            if m:
                _crawl["total"] = int(m.group(1))

            m = _RE_PROGRESS.search(line)
            if m:
                # 5 个线程乱序打印，取最大值，避免 done 倒退
                _crawl["done"] = max(_crawl["done"], int(m.group(1)))
                # 进度行里的分母是真正要处理的条数，比前面「读取到 N 个网址」准
                _crawl["total"] = int(m.group(2))

            if "■" in line:
                _crawl["matched"] += 1

            if _crawl["total"]:
                _crawl["percent"] = round(min(_crawl["done"] / _crawl["total"] * 100, 99), 1)

        code = proc.wait()
        if code != 0:
            _crawl["error"] = f"爬虫退出码 {code}，详情见日志"
        else:
            _crawl["percent"] = 100
    except Exception as exc:  # 阅读输出时进程被强杀等情况
        _crawl["error"] = f"监控线程异常：{exc}"
    finally:
        _crawl["running"] = False
        _crawl["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _df_lock:
            _df_cache["mtime"] = None   # 强制下次请求重新读 Excel
            _df_cache["df"] = None


@app.post("/api/crawl")
def start_crawl(source: str = "", file: str = ""):
    """只支持从已导入的网址表采集，不再默认去翻列表页。"""
    with _crawl_lock:
        if _crawl["running"]:
            return {"ok": False, "message": "正在刷新中，请等待本次完成"}

        if not (BASE_DIR / "main.py").exists():
            raise HTTPException(status_code=500, detail="找不到 main.py")

        # 没有网址表就不开始：避免误点后跑去抓整站列表
        if source != "excel" or not file:
            raise HTTPException(status_code=400, detail="请先导入网址表，再点开始采集")

        # 只取文件名，挡掉 ../ 这类目录穿越
        target = UPLOAD_DIR / Path(file).name
        if not target.exists():
            raise HTTPException(status_code=404, detail="找不到上传的网址表，请重新上传")

        cmd = [sys.executable, "-u", "main.py", str(target)]

        _crawl.update({
            "running": True,
            "total": 0,
            "done": 0,
            "matched": 0,
            "percent": 0,
            "last_line": "启动中…",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "error": "",
            "source": source,
            "file": file,
        })
        _log.clear()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        _crawl["running"] = False
        _crawl["error"] = f"启动失败：{exc}"
        raise HTTPException(status_code=500, detail=str(exc))

    threading.Thread(target=_watch_crawl, args=(proc,), daemon=True).start()
    return {"ok": True, "message": "已开始刷新"}


@app.get("/api/crawl/status")
def crawl_status():
    return {**_crawl, "log": list(_log)[-20:]}


# ======================== 上传网址表 ========================
@app.post("/api/upload")
async def upload_urls(file: UploadFile = File(...)):
    """收一张 Excel，读出里面的网址，返回条数；真正的抓取由 /api/crawl 启动。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="只支持 .xlsx / .xls 文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 10MB，请先精简网址表")

    # 不用原始文件名，避免中文/特殊字符和覆盖，统一加上时间戳
    safe_name = f"urls_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
    target = UPLOAD_DIR / safe_name

    try:
        target.write_bytes(content)
        urls = read_urls(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"读取失败：{exc}")

    if not urls:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="表里没找到 http/https 网址，检查一下列内容")

    # 光数网址条数会误导：表里可能只有 1 条列表页地址，底下是上百条公告。
    # 这里对每个网址抓一次首页，判出「列表页」还是「详情页」，列表页报本页条数。
    probes = await run_in_threadpool(probe_urls, urls)

    details = [p for p in probes if p["type"] == "detail"]
    lists = [p for p in probes if p["type"] == "list"]
    unknown = [p for p in probes if p["type"] == "unknown"]

    return {
        "ok": True,
        "file": safe_name,
        "count": len(urls),          # 表里几条网址
        "detail": len(details),      # 其中详情页几条
        "lists": lists,              # 其中列表页几条、本页各多少条
        "items": sum(p["items"] for p in probes if p["type"] != "unknown"),
        "unknown": len(unknown),     # 打不开的网址条数
        "sample": urls[:3],
    }


# ======================== 导出 ========================
@app.get("/api/export")
def export():
    if not EXCEL_PATH.exists():
        raise HTTPException(status_code=404, detail="还没有生成 Excel，请先刷新数据")
    return FileResponse(
        path=EXCEL_PATH,
        filename="公告筛选结果.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ======================== 静态页面 ========================
# 放在最后：先注册 API 路由，再把根路径交给前端页面
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="web")
