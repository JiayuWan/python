from openpyxl import Workbook


def export_to_excel(matched_list, filename="公告筛选结果.xlsx"):

    # 1. 创建工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "公告筛选结果"

    # 2. 写入表头
    headers = [
        "公告标题",
        "发布日期",
        "项目名称",
        "采购人/招标人",
        "采购代理机构/招标代理机构",
        "采购方式",
        "项目编号",
        "预算金额",
        "投标/响应文件截止时间",
        "项目内容/采购范围",
        "公告详情链接",
        "标题命中关键词",
        "正文命中关键词"
    ]

    ws.append(headers)

    # 3. 写入公告数据
    for item in matched_list:

        row = [
            item.get("title", ""),
            item.get("publish_date", ""),
            item.get("project_name", ""),
            item.get("purchaser", ""),
            item.get("agency", ""),
            item.get("procurement_method", ""),
            item.get("procurement_no", ""),
            item.get("budget", ""),
            item.get("deadline", ""),
            item.get("project_scope", ""),
            item.get("detail_url", ""),
            "、".join(item.get("title_matched_keywords", [])),
            "、".join(item.get("content_matched_keywords", []))
        ]

        ws.append(row)

    # 4. 保存 Excel
    wb.save(filename)

    print(f"Excel 导出成功：{filename}")
    print(f"共导出 {len(matched_list)} 条公告")