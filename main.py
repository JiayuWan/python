import time
import threading
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from list_spider import get_announcement_list
from detail_spider import get_announcement_detail
from keyword_filter import filter_announcement
from excel_export import export_to_excel


def main():

    # 1. 获取公告列表（列表只有几页，串行足够快；传 Session 只为复用 TCP 连接）
    session = requests.Session()
    announcement_list = get_announcement_list(session=session)
    session.close()

    print(f"共获取到 {len(announcement_list)} 条公告")

    test_list = announcement_list[:-1]  # 调试残留，稍后处理
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
        s = requests.Session()

        try:
            detail = get_announcement_detail(item["detail_url"], session=s)

            if not detail:
                print(f"  [{index}/{total}] ✕ {item['title'][:50]} → 详情页无内容")
                return

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

            # 线程内随机 0.3~0.8 秒延迟，5 个线程合计约 6~10 条/秒
            time.sleep(random.uniform(0.3, 0.8))

        except Exception as e:
            print(f"  [{index}/{total}] ✕ {item['title'][:40]} → {e}")
        finally:
            s.close()
            with lock:
                done[0] += 1

    # 3. 线程池并发取详情
    print(f"\n按「{total} 条 × 5 线程」并发处理……\n")

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(process_item, item, idx)
            for idx, item in enumerate(test_list, start=1)
        ]

        for future in as_completed(futures):
            future.result()  # 确保 process_item 的异常被吃掉

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
