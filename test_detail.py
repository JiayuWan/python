
import requests
from bs4 import BeautifulSoup
import re

# 公告详情页
url = "http://www.ygcgw.com.cn/news/0004768781272.html"

headers = {
    "User-Agent": "Mozilla/5.0"
}

# 1. 请求详情页
response = requests.get(
    url,
    headers=headers,
    timeout=20
)

print("状态码：", response.status_code)

response.raise_for_status()

response.encoding = response.apparent_encoding

# 2. 解析HTML
soup = BeautifulSoup(response.text, "lxml")


# 1. 提取公告标题
title_tag = soup.find("div", class_="article-title")
title = title_tag.get_text(strip=True) if title_tag else ""

# 2. 提取公告发布时间等信息
author_tag = soup.find("div", class_="article-author")
author_info = author_tag.get_text(" ", strip=True) if author_tag else ""

# 3. 提取公告正文
content_tag = soup.find("div", class_="main-text")
content = content_tag.get_text(separator="\n", strip=True) if content_tag else ""

# 4. 打印提取结果
print("公告标题：")
print(title)

print("\n公告基本信息：")
print(author_info)

print("\n公告正文：")
print(content)

print("\n正文内部的HTML标签：")

if content_tag:
    for child in content_tag.find_all(recursive=False):
        print(
            "标签：", child.name,
            "class：", child.get("class"),
            "id：", child.get("id"),
            "文本：", child.get_text(strip=True)[:100]
        )

# 定位公告内容容器
article_content = soup.find("div", class_="article-content")

if article_content:
    print("\n公告内容：")
    print(article_content.get_text(separator="\n", strip=True))

    print("\n公告内容容器的直接子节点：")
    for child in article_content.find_all(recursive=False):
        print(
            "标签：", child.name,
            "class：", child.get("class"),
            "id：", child.get("id"),
            "文本：", child.get_text(strip=True)[:100]
        )
else:
    print("没有找到公告内容容器")


# 提取正文中的所有段落
paragraphs = content_tag.find_all("p")

# 保存每个段落的文本
paragraph_list = []

# 提取正文中的段落
for p in content_tag.find_all("p"):
    text = p.get_text(" ", strip=True)
    if text:
        paragraph_list.append(text)

# 提取正文中的表格
for tr in content_tag.find_all("tr"):
    cells = [
        td.get_text(" ", strip=True)
        for td in tr.find_all(["td", "th"])
    ]

    # 去掉空单元格
    cells = [cell for cell in cells if cell]

    # 将表格中的字段和值组合起来
    row_text = " ".join(cells)

    if row_text:
        paragraph_list.append(row_text)

content = "\n".join(paragraph_list)
# 逐段打印
print("\n公告正文段落：")

for index, paragraph in enumerate(paragraph_list, start=1):
    print(f"{index}. {paragraph}")

def get_paragraph_value(keyword):
    for paragraph in paragraph_list:
        if keyword in paragraph:
            return paragraph.split("：", 1)[-1].strip()

    return ""


# 提取项目基本信息
project_name = get_paragraph_value("1.1项目名称")
procurement_no = get_paragraph_value("1.2采购代理编号")
procurement_method = get_paragraph_value("1.3采购方式")
budget_text = get_paragraph_value("1.4采购项目预算")
service_period = get_paragraph_value("1.6服务期限")

# 使用正则表达式提取预算数字
budget_match = re.search(r"\d+(?:\.\d+)?", budget_text)
budget = budget_match.group() if budget_match else ""

# 提取采购人、代理机构、投标截止时间、文件获取时间
purchaser = get_paragraph_value("采 购 人")
agency = get_paragraph_value("代理机构：")
deadline = get_paragraph_value("响应文件的提交截止时间")
document_time = get_paragraph_value("3.1获取询比文件的时间")

print("\n补充结构化信息：")
print("采购人：", purchaser)
print("代理机构：", agency)
print("投标截止时间：", deadline)
print("文件获取时间：", document_time)

# 打印结果
print("\n结构化公告信息：")
print("项目名称：", project_name)
print("采购代理编号：", procurement_no)
print("采购方式：", procurement_method)
print("预算：", budget)
print("服务期限：", service_period)


# 公告标题
target_title = "株洲国投产业园智慧园区信息化系统优化改造项目询比公告"

# 查找页面中所有与公告标题完全匹配的文本节点
title_nodes = soup.find_all(
    string=lambda s: s and s.strip() == target_title
)

print("\n标题所在HTML结构：")

for index, node in enumerate(title_nodes, start=1):

    print(f"\n第 {index} 个匹配结果：")

    parent = node.parent

    # 向上查看5层HTML结构
    for level in range(5):

        if parent is None:
            break

        print(
            f"第{level + 1}层：",
            parent.name,
            parent.get("class"),
            parent.get("id")
        )

        parent = parent.parent


# 3. 获取网页标题
print("\n网页标题：")
print(soup.title.get_text(strip=True) if soup.title else "未找到")

# 4. 获取网页正文文本
print("\n网页正文：")

print(soup.get_text(separator="\n", strip=True)[:3000])

# 5. 查看页面中所有可能的附件链接
print("\n页面中的文件链接：")



for a in soup.find_all("a", href=True):

    href = a["href"]
    text = a.get_text(strip=True)

    if any(ext in href.lower() for ext in [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"
    ]):
        print("附件名称：", text)
        print("附件地址：", href)


