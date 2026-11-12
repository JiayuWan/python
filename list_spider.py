import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin


# 网站基本配置
BASE_URL = "http://www.ygcgw.com.cn"
LIST_URL = BASE_URL + "/news/list-22.html"


def get_announcement_list(max_pages=None, session=None):
    """
    获取星河易采网站的多页公告列表
    max_pages：最大采集页数，None表示持续采集直到没有下一页
    session：可传入 requests.Session() 以复用连接
    """

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    _get = session.get if session else requests.get

    announcements = []
    page = 1

    while True:

        # 1. 根据页码生成URL
        if page == 1:
            current_url = LIST_URL
        else:
            current_url = BASE_URL + f"/news/list-22-{page}.html"

        print(f"\n正在采集第 {page} 页：{current_url}")

        try:
            # 2. 请求当前页面
            response = _get(
                current_url,
                headers=headers,
                timeout=20
            )

            response.raise_for_status()
            response.encoding = response.apparent_encoding

            # 3. 解析HTML
            soup = BeautifulSoup(response.text, "lxml")

            # 4. 定位公告列表
            news_list = soup.find("ul", id="list1")

            if not news_list:
                print(f"第 {page} 页没有找到公告列表，停止采集")
                break

            news_items = news_list.find_all(
                "li",
                attrs={"name": "li_name"}
            )

            # 5. 如果没有公告，说明已经到最后一页
            if not news_items:
                print(f"第 {page} 页没有公告，停止采集")
                break

            # 6. 提取当前页公告
            page_count = 0

            for item in news_items:

                a = item.find("a")

                if not a:
                    continue

                title = a.get("title", "").strip()
                detail_url = a.get("href", "").strip()

                date_tag = a.find("em")

                publish_date = (
                    date_tag.get_text(strip=True)
                    if date_tag else ""
                )

                detail_url = urljoin(LIST_URL, detail_url)

                if not title or not detail_url:
                    continue

                announcement = {
                    "title": title,
                    "publish_date": publish_date,
                    "detail_url": detail_url
                }

                announcements.append(announcement)
                page_count += 1

            print(f"第 {page} 页采集完成，获取 {page_count} 条公告")

            # 7. 判断是否达到指定页数
            if max_pages is not None and page >= max_pages:
                break

            # 8. 翻页前暂停一下
            page += 1
            time.sleep(1)

        except requests.RequestException as e:
            print(f"第 {page} 页请求失败：{e}")
            break

    print(f"\n所有页面采集结束，共获取 {len(announcements)} 条公告")

    return announcements


if __name__ == "__main__":

    # 测试：先采集前3页
    data = get_announcement_list()

    for index, item in enumerate(data, start=1):
        print(f"\n第 {index} 条公告")
        print("标题：", item["title"])
        print("日期：", item["publish_date"])
        print("链接：", item["detail_url"])
        print("-" * 60)
