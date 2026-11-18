"""
全局配置：所有「调一下就能变」的参数都放这里。

以前这些数字散在 main.py、list_spider.py、detail_spider.py、api.py 四个文件里，
换个站点想调慢一点、或者想加线程，得满项目找常量。现在只改这个文件。

改完要重启服务（uvicorn 不是 --reload 的话）：
    uvicorn api:app --host 127.0.0.1 --port 8000
"""

# ======================== 采集强度 ========================
WORKERS = 8                 # 详情抓取的并发线程数；太少跑得慢，太多容易被站点限流
REQUESTS_PER_SEC = 12       # 全局请求速率上限（所有线程合计，含翻页与详情）
MIN_INTERVAL = 1.0 / REQUESTS_PER_SEC

# ======================== 网络超时 ========================
# requests 的 timeout 用 (连接超时, 读取超时) 两段，比单个数字更能定位问题：
# 连不上是网络/域名问题，连上了读不动是站点慢
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

# ======================== 失败重试 ========================
# 网络抖动、超时、429、5xx 会按 1s、2s、4s 退避重试；4xx（403/404）不重试
RETRY_TIMES = 3
RETRY_BASE_DELAY = 1.0

# ======================== 翻页 ========================
MAX_PAGES = None            # None = 一直翻到没有下一页为止（不设条数/页数上限）
PAGE_SLEEP = 1.0            # 翻下一页前的间隔，别把站点打疼

# ======================== 页面识别阈值 ========================
MIN_LINK_TITLE = 8         # 链接文字短于这个长度，基本是导航/栏目名，不算公告
MIN_CLUSTER = 3            # 同一链接形状至少几条，才认定这一页是列表页
MIN_CONTENT_LEN = 100      # 详情正文短于这个长度，认为没抓到内容，换下一个容器

# ======================== 网址表上传 ========================
MAX_UPLOAD_BYTES = 10 * 1024 * 1024    # 网址表没必要超过 10MB

# 上传时要顺带探测每个网址是列表页还是详情页，这里限制探测本身的开销
PROBE_WORKERS = 8
PROBE_CONNECT_TIMEOUT = 3
PROBE_READ_TIMEOUT = 8
PROBE_BUDGET_SECONDS = 12   # 整批探测最多等这么久，超时的网址标 unknown 先返回

# ======================== 请求头 ========================
# 完整浏览器头。精简 UA 会被不少政府站/招标站直接 403，别轻易改。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ======================== 服务 ========================
PAGE_SIZE_DEFAULT = 10      # 页面每次拉多少条
PAGE_SIZE_MAX = 50          # 上限，防止一次把整表传给浏览器
