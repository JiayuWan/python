"""
列表采集：把网址表里的网址变成公告列表。

和原来那个 list_spider.py 是同一个角色、同一个入口函数名（get_announcement_list），
区别只在于不再写死星河易采一个站：换成别的网站也能用，靠页面结构自己判断。

表里可能有两种网址：
1. 列表页（news/list-22.html 这种，一页十几二十条公告）→ 在这里展开成公告列表；
2. 详情页 → 直接收下，当一条公告。

怎么认列表页：
- 抓页面所有 <a>，只留同域名的 http/https 链接；
- 标题太短的丢掉（导航、栏目名、友情链接）；
- 按「链接形状」聚类：路径里的数字换成 {n}，例如
  /news/0004771181272.html → /news/{n}.html，同一形状条数最多的那一组就是公告；
- 这一组至少 3 条，才认为这是列表页；不够 3 条说明它本身是详情页，原样收下。

翻页：优先找 rel=next、「下一页」这类链接；没有就看数字页码
（星河易采就是只有 2 3 4 … 1043 这种页码，一个字都没有）。

单独测试：
    python list_spider.py http://www.ygcgw.com.cn/news/list-22.html
"""

import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlsplit

import requests
from bs4 import BeautifulSoup

# 和 detail_spider.py 用同一套完整浏览器头，避免被政府站点 403
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

MIN_LINK_TITLE = 8          # 标题短于这个长度，基本是导航或栏目名
MIN_CLUSTER = 3             # 同一形状的链接至少几条，才认为这一页是列表页
SKIP_PREFIXES = ("javascript:", "mailto:", "tel:", "#", "data:")
NEXT_TEXTS = ("下一页", "下页", "后一页", "下一頁", "next", "›", "»", ">")

DATE_PATTERN = re.compile(r"(20\d{2})[-/年]\s*(\d{1,2})[-/月]\s*(\d{1,2})")


def path_pattern(url):
    """把路径里的数字换成 {n}，用来判断一批链接是不是同一个栏目出来的。"""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    shaped = "/".join(re.sub(r"\d+", "{n}", seg) for seg in segments)
    return f"{parts.scheme}://{parts.netloc}/{shaped}"


def normalize_date(text):
    """把「2026年11月13日」「2026/11/13」「11-13」统一成 2026-11-13。"""
    text = text or ""

    match = DATE_PATTERN.search(text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    # 有些站点列表里只写「11-13」，年份用当前年补上（详情页会有完整日期）
    short = re.search(r"(?<!\d)(\d{1,2})[-/月]\s*(\d{1,2})(?!\d)", text)
    if short:
        month, day = short.groups()
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{datetime.now().year}-{int(month):02d}-{int(day):02d}"
    return ""


def collect_links(soup, page_url):
    """收集页面上像公告条目的链接：同域名、标题够长，顺带把日期捡出来。"""
    host = urlparse(page_url).netloc
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.lower().startswith(SKIP_PREFIXES):
            continue

        absolute = urljoin(page_url, href).split("#")[0]
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if urlparse(absolute).netloc != host:
            continue

        # 优先用 a 的 title 属性：有的站把日期放在 <em> 里塞在 <a> 内部，
        # 直接取文本会把「11-13」拼进标题
        text = a.get("title", "").strip()
        if not text:
            text = a.get_text(" ", strip=True)
        text = re.sub(r"\s*\d{1,2}[-/月]\d{1,2}日?\s*$", "", text).strip()
        if len(text) < MIN_LINK_TITLE:
            continue

        # 日期基本都在条目容器里（li / 父节点），链接本身不带
        container = a.find_parent(["li", "tr"]) or a.parent
        date_text = container.get_text(" ", strip=True) if container else text
        if not normalize_date(date_text):
            date_text = text

        items.append({
            "url": absolute,
            "title": text,
            "publish_date": normalize_date(date_text),
        })
    return items


def pick_detail_links(items, page_url):
    """按链接形状取最大的一组，当作这一页的公告；不够 3 条返回空。"""
    groups = {}
    for item in items:
        groups.setdefault(path_pattern(item["url"]), []).append(item)

    best = []
    for group in groups.values():
        group = [x for x in group if x["url"] != page_url.split("#")[0]]
        if len(group) > len(best):
            best = group

    if len(best) < MIN_CLUSTER:
        return []

    # 同一链接只留一条（标题可能重复出现，比如列表和图两块）
    seen, unique = set(), []
    for item in best:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def find_next_page(soup, page_url):
    """找「下一页」。按 rel、aria-label、链接文字三种线索依次试。"""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.lower().startswith(SKIP_PREFIXES):
            continue

        rel = " ".join(a.get("rel") or []).lower()
        text = a.get_text(" ", strip=True).lower()
        label = (a.get("aria-label") or "").lower()

        hit = (
            "next" in rel
            or text in NEXT_TEXTS
            or "下一页" in label
            or "下一页" in text
            or "后一页" in text
        )
        if not hit:
            continue

        nxt = urljoin(page_url, href).split("#")[0]
        if nxt.startswith(("http://", "https://")) and nxt != page_url.split("#")[0]:
            return nxt
    return ""


def alpha_key(path):
    """去掉路径里的所有数字，只留下字母骨架。用来判断前后页是不是同一个栏目。"""
    return re.sub(r"[-_]+", "-", re.sub(r"\d+", "", path))


def find_numbered_next(soup, page_url, current_no, visited):
    """
    没有「下一页」字样时，靠数字页码翻页。

    星河易采这种站就是这样：只有 2 3 4 … 1043 的页码链接。
    做法是找文本是纯数字、且和当前页路径骨架相同的链接，
    取「当前页码 +1」那一页；没有再退到「比当前页码大的最小一页」。
    """
    host = urlparse(page_url).netloc
    key = alpha_key(urlparse(page_url).path)
    current = page_url.split("#")[0]

    candidates = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).replace("\xa0", "").strip()
        if not text.isdigit() or len(text) > 4:
            continue

        nxt = urljoin(page_url, a["href"].strip()).split("#")[0]
        parts = urlparse(nxt)
        if parts.netloc != host or alpha_key(parts.path) != key:
            continue
        if nxt == current or nxt in visited:
            continue
        candidates.append((int(text), nxt))

    for number, nxt in sorted(candidates):
        if number == current_no + 1:
            return nxt
    for number, nxt in sorted(candidates):
        if number > current_no:
            return nxt
    return ""


def crawl_one_source(url, max_pages=None, session=None, log=print):
    """
    处理一个网址：
    - 是列表页 → 展开成公告列表（一直翻到没有下一页为止）；
    - 是详情页 → 返回一条只有链接的公告。
    每条都是 {"title", "publish_date", "detail_url"}，和原来 list_spider 的形状一致。

    max_pages=None 表示不限页数；想临时限制可以传整数。
    """
    _get = session.get if session else requests.get

    found = []
    page_url = url
    pattern = ""
    visited = {url}
    page_no = 1

    while True:
        try:
            resp = _get(page_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
        except requests.RequestException as exc:
            log(f"  * 第 {page_no} 页抓取失败：{page_url} → {exc}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        items = pick_detail_links(collect_links(soup, page_url), page_url)

        if page_no == 1:
            if not items:
                return [{"title": "", "publish_date": "", "detail_url": url}]
            pattern = path_pattern(items[0]["url"])
            log(f"  列表页：{url} → 本页 {len(items)} 条")
        elif not items or path_pattern(items[0]["url"]) != pattern:
            break

        for item in items:
            if item["url"] not in {x["detail_url"] for x in found}:
                found.append({
                    "title": item["title"],
                    "publish_date": item["publish_date"],
                    "detail_url": item["url"],
                })

        # 优先用「下一页」链接，没有就看数字页码
        nxt = find_next_page(soup, page_url) or find_numbered_next(
            soup, page_url, page_no, visited
        )
        if not nxt:
            log(f"  第 {page_no} 页已是最后一页，共 {len(found)} 条")
            break

        if max_pages is not None and page_no >= max_pages:
            log(f"  已到指定页数 {max_pages}，停止翻页")
            break

        page_no += 1
        log(f"  第 {page_no} 页：{nxt}")
        visited.add(nxt)
        page_url = nxt
        time.sleep(1)

    return found


def probe(url, session=None):
    """
    轻量探测：只抓这个网址的第一页，判断它是列表页还是详情页。

    上传网址表时用这个，目的是别把「1 个列表页地址」当成「1 条公告」显示给用户。
    不翻页，所以一个网址只发一次请求，几十个网址也能几秒内出结果。
    """
    _get = session.get if session else requests.get

    try:
        resp = _get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
    except requests.RequestException as exc:
        return {"url": url, "type": "unknown", "items": 0, "note": str(exc)[:60]}

    soup = BeautifulSoup(resp.text, "lxml")
    items = pick_detail_links(collect_links(soup, url), url)

    if items:
        return {"url": url, "type": "list", "items": len(items)}
    return {"url": url, "type": "detail", "items": 1}


def probe_urls(urls, max_workers=5):
    """批量探测，并发跑，顺序按原表顺序返回。"""
    from concurrent.futures import ThreadPoolExecutor

    if not urls:
        return []

    def task(u):
        session = requests.Session()
        try:
            return probe(u, session=session)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(task, urls))


def get_announcement_list(urls, max_pages=None, session=None, log=print):
    """
    入口函数，名字和参数形状延续原来的 list_spider：
    给一批网址，返回公告列表 [{title, publish_date, detail_url}]。

    urls 可以是字符串（单个网址），也可以是列表。
    """
    if isinstance(urls, str):
        urls = [urls]

    announcements = []
    seen = set()

    for url in urls:
        log(f"检查网址：{url}")
        for item in crawl_one_source(url, max_pages=max_pages, session=session, log=log):
            if item["detail_url"] in seen:
                continue
            seen.add(item["detail_url"])
            announcements.append(item)

    return announcements


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python list_spider.py <列表页网址> [翻几页]（不填就一直翻到最后一页）")
        raise SystemExit(1)

    # 默认不限制页数，第二个参数可以临时限定
    pages = int(sys.argv[2]) if len(sys.argv) >= 3 else None

    session = requests.Session()
    try:
        data = get_announcement_list([sys.argv[1]], max_pages=pages, session=session, log=print)
    finally:
        session.close()

    print(f"\n共 {len(data)} 条公告")
    for index, item in enumerate(data[:10], start=1):
        print(f"  {index}. [{item['publish_date'] or '-'}] {item['title'][:40]}")
    if len(data) > 10:
        print(f"  ……还有 {len(data) - 10} 条")
