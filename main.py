import sys
import time
import threading
import random  # noqa: F401  已不再按条随机睡眠，限速由 throttle 统一负责
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from detail_spider import get_announcement_detail
from keyword_filter import filter_announcement
from excel_export import export_to_excel
from url_source import read_urls
from list_spider import get_announcement_list

# Windows 控制台默认 GBK，遇到 GBK 编不了的符号（比如 ✕）会直接抛 UnicodeEncodeError，
# 一行日志就能把整个采集带崩。这里兜一下：编不了就用 ? 代替，不让日志杀死任务。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ---------------- 性能参数 ----------------
WORKERS = 8              # 并发线程数；原来 5
REQUESTS_PER_SEC = 12    # 全局请求速率上限（所有线程合计），防被站点限流
MIN_INTERVAL = 1.0 / REQUESTS_PER_SEC

_rate_lock = threading.Lock()
_rate_next = [0.0]


def throttle():
    """
    全局节流：所有线程一起算，保证整体不超过 REQUESTS_PER_SEC。
    原来是在每条公告后面 sleep 0.3~0.8 秒（还占着线程），换成按请求排队，
    线程不再被固定睡眠占住，吞吐能上去，对站点的压力也依旧平滑。
    """
    with _rate_lock:
        now = time.monotonic()
        wait = _rate_next[0] - now
        if wait > 0:
            time.sleep(wait)
            now = _rate_next[0]
        _rate_next[0] = max(now, _rate_next[0]) + MIN_INTERVAL


_local = threading.local()
_all_sessions = []
_all_sessions_lock = threading.Lock()


def get_session():
    """
    每个线程复用一个 Session：原来每条公告都新建连接，
    TLS 握手 + 建连一次要 300 多毫秒，复用之后一条降到 170 毫秒左右。
    """
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
        with _all_sessions_lock:
            _all_sessions.append(session)
    return session


def close_all_sessions():
    with _all_sessions_lock:
        for session in _all_sessions:
            try:
                session.close()
            except Exception:
                pass
        _all_sessions.clear()


def load_announcements(path, max_pages=None):
    """
    把 Excel 里的网址变成公告列表（带标题和日期）。

    表里可能有两种东西：
    - 列表页链接 → 交给 list_spider 展开，连标题、日期一起带回来；
    - 详情页链接 → 直接收下，标题日期留给详情页补。

    哪一种是靠页面结构自动判断的，不认域名，换站也能用。
    """
    urls = read_urls(path)
    print(f"从 Excel 读取到 {len(urls)} 个网址：{path}")

    if not urls:
        return []

    session = requests.Session()
    try:
        print("先判断这些网址是列表页还是详情页……")
        announcements = get_announcement_list(
            urls, max_pages=max_pages, session=session
        )
    finally:
        session.close()

    print(f"展开后得到 {len(announcements)} 条公告")
    return announcements


def main():

    # 1. 只从 Excel 读网址：不再默认去翻写死的列表页。
    #    表里放详情页链接可以直接抓；放列表页链接会交给 list_spider 展开成公告列表。
    #    列表页一直翻到没有下一页为止，不设页数上限。
    #    用法：python main.py 网址表.xlsx
    if len(sys.argv) < 2:
        print("用法：python main.py 网址表.xlsx")
        print("（表里放一列链接就行，表头写「链接」「网址」「url」都可以）")
        sys.exit(1)

    excel_path = sys.argv[1]

    # 默认不限制页数；真需要临时限制，命令行第二个参数写页数即可
    max_pages = None
    if len(sys.argv) >= 3:
        try:
            max_pages = max(1, int(sys.argv[2]))
        except ValueError:
            print(f"第二个参数要是页数（正整数），收到的是：{sys.argv[2]}")
            sys.exit(1)

    announcement_list = load_announcements(excel_path, max_pages=max_pages)

    if not announcement_list:
        print("表里没读到可用网址，检查一下列内容")
        sys.exit(1)

    test_list = announcement_list  # 原代码这里是 [:-1]，会稳定丢掉最后一条公告，已修正
    total = len(test_list)

    # 2. 保存命中的公告（线程安全）
    matched_list = []
    lock = threading.Lock()
    done = [0]

    def process_item(item, index):
        """
        在子线程里跑：取详情 → 做筛选。
        每个线程自己建 Session，不跨线程共享。
        """
        s = get_session()

        try:
            throttle()
            detail = get_announcement_detail(item["detail_url"], session=s)

            if not detail:
                print(f"  [{index}/{total}] x {(item.get('title') or item.get('detail_url', ''))[:50]} → 详情页无内容")
                return

            # 列表页本来就带着标题和日期，详情页没解析出来就用它们兜底，
            # 免得明明是公告，却因为详情页结构认不出来被丢掉标题
            if not detail.get("title"):
                detail["title"] = item.get("title", "")
            if not detail.get("publish_date"):
                detail["publish_date"] = item.get("publish_date", "")

            title = detail.get("title", "")
            content = detail.get("content", "")
            result = filter_announcement(title, content)

            if result["matched"]:
                detail["title_matched_keywords"] = result["title_matched_keywords"]
                detail["content_matched_keywords"] = result["content_matched_keywords"]

                with lock:
                    matched_list.append(detail)

                print(f"  [{index}/{total}] ■ {title[:50]}")
                for kw in result["matched_keywords"][:6]:
                    print(f"        {kw}")
            else:
                print(f"  [{index}/{total}] □ {title[:50]}")

            # 限速交给全局 throttle，这里不再固定睡眠占用线程

        except Exception as e:
            print(f"  [{index}/{total}] x {(item.get('title') or item.get('detail_url', ''))[:40]} → {e}")
        finally:
            with lock:
                done[0] += 1

    # 3. 线程池并发取详情
    print(f"\n按「{total} 条 × {WORKERS} 线程」并发处理（全局限速 {REQUESTS_PER_SEC} 条/秒）……\n")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [
            pool.submit(process_item, item, idx)
            for idx, item in enumerate(test_list, start=1)
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                # 单条线程出问题（含打印编码失败）只跳这一条，别让整轮采集中断
                print(f"  线程异常：{exc}")

    close_all_sessions()   # 把各线程复用的连接收掉

    # 4. 输出结果
    print(f"\n========== 关键词筛选完成 ==========")
    print(f"本次处理公告数：{total}（完成 {done[0]} 条）")
    print(f"命中公告数：{len(matched_list)}")

    for index, item in enumerate(matched_list, start=1):
        print(f"\n========== 命中公告 {index} ==========")
        print("标题：", item.get("title", ""))
        print("项目名称：", item.get("project_name", ""))
        print("采购人：", item.get("purchaser", ""))
        print("采购方式：", item.get("procurement_method", ""))
        print("预算：", item.get("budget", ""))
        print("详情链接：", item.get("detail_url", ""))

    # 5. 导出 Excel
    export_to_excel(matched_list)


if __name__ == "__main__":
    main()
