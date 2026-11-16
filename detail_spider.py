
import requests
import re
from bs4 import BeautifulSoup


def clean_field_value(value):
    """
    清洗正则捕获到的字段值。

    - 去掉首尾空白；
    - 遇到 2 个及以上的连续空白就截断。页面上表格的多个单元格被渲染到同一行时
      （如 "招 标 人：XX有限公司   联系人： 付先生"），这串空白就是字段边界，
      截断后能拿到干净的 "XX有限公司"，而不是拖着 "联系人：…" 的一条尾巴；
    - 如果结果以冒号结尾（例如 "工本费:"），说明这次匹配跨到了
      **下一个字段的标签**上，属于脏数据，直接判为无效返回空字符串，
      避免把别的字段名写进 Excel。

    注意：只截断"2 个以上"的连续空白，单个空格不截断，
    所以截止时间 "2026-11-10 09:00" 这类正常含空格的取值不受影响。
    """
    if not value:
        return ""

    value = re.split(r"\s{2,}", value.strip())[0].strip()

    if value.endswith(("：", ":")):
        return ""

    return value


def extract_table_fields(container):
    """
    通用提取公告中的所有表格。

    不预设任何字段名称，不限制表格列数。
    保留表格、行、单元格的层级结构。
    """
    all_tables = []

    if not container:
        return all_tables

    for table in container.find_all("table"):
        table_data = []

        for tr in table.find_all("tr"):
            # 只获取当前行的直接子单元格
            cells = tr.find_all(["td", "th"], recursive=False)

            row = [
                cell.get_text(" ", strip=True)
                for cell in cells
            ]

            if row:
                table_data.append(row)

        if table_data:
            all_tables.append(table_data)

    return all_tables



def extract_table_field(table_data, patterns):
    """
    从表格中提取字段。

    判断规则：
    1. 单元格包含冒号：
       冒号前后必须都有内容，才允许提取。
    2. 单元格不包含冒号：
       当前单元格作为字段名，下一个单元格作为字段值。
    """

    for table in table_data:
        for row in table:

            for i, cell in enumerate(row):
                cell = cell.strip()

                if not cell:
                    continue

                # 情况一：当前单元格包含冒号
                if "：" in cell or ":" in cell:

                    # 优先按中文冒号或英文冒号切分
                    parts = re.split(r"[：:]", cell, maxsplit=1)

                    if len(parts) != 2:
                        continue

                    field_name = parts[0].strip()
                    field_value = parts[1].strip()

                    # 冒号前后必须都有内容
                    if not field_name or not field_value:
                        continue

                    # 直接使用原来的正则匹配完整单元格
                    for pattern in patterns:
                        match = re.match(pattern, cell)

                        if match:
                            value = clean_field_value(match.group(1))

                            if value:
                                return value

                # 情况二：当前单元格不包含冒号
                else:

                    # 没有下一个单元格，无法组成字段和值
                    if i + 1 >= len(row):
                        continue

                    next_value = row[i + 1].strip()

                    if not next_value:
                        continue

                    # 下一个单元格只有字段标签，没有实际值，跳过
                    if next_value.endswith(("：", ":")):
                        continue

                    # 将相邻单元格视为字段和值
                    combined_text = cell + "：" + next_value

                    for pattern in patterns:
                        match = re.match(pattern, combined_text)

                        if match:
                            value = clean_field_value(match.group(1))

                            if value:
                                return value

    return ""


# ======================== 通用站点兜底 ========================
# 网址表里的链接不会只有星河易采一个站，所以标题、日期、正文都要有通用规则：
# 先按星河易采的固定结构找，找不到再按常见的页面结构去找，最后按"文本最多"兜底。

# 常见的正文容器，按命中概率从高到低排
CONTENT_SELECTORS = [
    "div.main-text",          # 星河易采
    "div#UCAP-CONTENT",       # 中国政府采购网
    "div.TRS_Editor",         # 大量政府网站用的编辑器容器
    "div.article-content",
    "div.article_content",
    "div.news-content",
    "div.detail-content",
    "div.article",
    "div.content",
    "div#content",
    "div#zoom",
    "div.txt",
    "article",
]

# 正文至少要这么多字才算有效，挡掉"内容不存在"“登录后查看”这类空壳
MIN_CONTENT_LEN = 100


def pick_title(soup):
    """标题：先认星河易采的 article-title，再依次试通用结构，最后用 <title> 去掉站点名。"""
    for selector in ("div.article-title", "h1", "div.title", "span.title", "h2"):
        node = soup.select_one(selector)
        if node:
            text = node.get_text(strip=True)
            if text:
                return text

    for attrs in ({"property": "og:title"}, {"name": "ArticleTitle"}, {"name": "title"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content", "").strip():
            return meta["content"].strip()

    if soup.title:
        text = soup.title.get_text(strip=True)
        # "公告标题_XX市公共资源交易网" 这类，取前半段
        return re.split(r"\s*[_\-|｜—]\s*", text)[0].strip()

    return ""


def normalize_date(text):
    """把 2026年11月13日 / 2026/11/13 统一成 2026-11-13。"""
    if not text:
        return ""
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def pick_publish_date(text):
    """发布日期：带字段名的优先，最后退到"页面上第一个日期"。"""
    for pattern in (
        r"发\s*布\s*时\s*间[：:]?\s*([0-9]{4}[-/年][0-9]{2}[-/月][0-9]{1,2})",
        r"发\s*布\s*日\s*期[：:]?\s*([0-9]{4}[-/年][0-9]{2}[-/月][0-9]{1,2})",
        r"公\s*告\s*日\s*期[：:]?\s*([0-9]{4}[-/年][0-9]{2}[-/月][0-9]{1,2})",
        r"([0-9]{4}[-/年][0-9]{2}[-/月][0-9]{1,2})",
    ):
        match = re.search(pattern, text)
        if match:
            value = normalize_date(match.group(1))
            if value:
                return value
    return ""


def pick_content(soup):
    """
    正文容器：先按常见的 class/id 找，找不到再用"文本最多且链接最少"的块兜底。

    兜底打分用 text_len - 3 * link_len，是因为导航栏、友情链接、页脚这些块
    文本也不少，但几乎全是链接，这样能把它们压下去。
    """
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) >= MIN_CONTENT_LEN:
            return node

    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    best_node, best_score = None, 0
    for node in soup.find_all(["div", "article", "section", "td"]):
        text_len = len(node.get_text(strip=True))
        if text_len < MIN_CONTENT_LEN:
            continue
        link_len = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
        score = text_len - 3 * link_len
        if score > best_score:
            best_node, best_score = node, score

    return best_node


def clean_method(value):
    """
    采购方式的收尾清洗。

    加上叙述句正则后，可能抓到「参照 竞争性 磋商」这种带前缀、带空格的结果，
    这里去掉前缀并把中间的空格合上；「公开询比(综合评分)」这类本来就干净的保持原样。
    """
    if not value:
        return ""

    value = re.sub(r"^(?:\s*(?:参照|采用|拟采用|本项目|按照|依据))+", "", value.strip())
    return re.sub(r"\s+", "", value)


def get_announcement_detail(detail_url, session=None):
    """
    根据公告详情页URL，提取统一格式的公告信息。

    session：可传入 requests.Session() 以复用 TCP 连接、加速批量抓取。
             不传则退化为普通的 requests.get()。
    """

    # 1. 初始化统一的数据结构
    announcement = {
        "title": "",
        "announcement_type": "",
        "publish_date": "",
        "project_name": "",
        "purchaser": "",
        "agency": "",
        "region": "",
        "procurement_method": "",
        "procurement_no": "",
        "budget": "",
        "deadline": "",
        "project_scope": "",
        "content": "",
        "detail_url": detail_url
    }

    # 2. 请求详情页
    # UA 只用 "Mozilla/5.0" 会被不少政府站点直接 403，
    # 换成完整的浏览器请求头，跨站也能拿到页面。
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    _get = session.get if session else requests.get
    response = _get(
        detail_url,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()
    response.encoding = response.apparent_encoding

    # 3. 解析HTML
    soup = BeautifulSoup(response.text, "lxml")

    # 4. 提取公告标题
    title = pick_title(soup)

    announcement["title"] = title

    # 5. 根据标题提取项目名称
    project_name = title.strip()

    # 去除标题开头的公告分类标签
    project_name = re.sub(
        r"^[【\[][^】\]]+[】\]]\s*",
        "",
        project_name
    )

    # 只删除公告类型后缀
    suffix_list = [
        "公开招标公告",
        "邀请招标公告",
        "竞争性磋商公告",
        "竞争性谈判公告",
        "询比价采购公告",
        "询比采购公告",
        "询比公告",
        "询价采购公告",
        "询价公告",
        "采购公告",
        "招标公告",
        "中标结果公告",
        "成交结果公告",
        "中标公告",
        "成交公告",
        "采购结果公告",
        "采购结果公示",
        "更正公告",
        "变更公告",
        "废标公告",
        "流标公告"
    ]

    for suffix in suffix_list:
        if project_name.endswith(suffix):
            project_name = project_name[:-len(suffix)].strip()
            break

    announcement["project_name"] = project_name

    # 6. 提取发布时间和地区
    author_tag = soup.find("div", class_="article-author")

    author_info = (
        author_tag.get_text(" ", strip=True)
        if author_tag else ""
    )

    date_match = re.search(
        r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})",
        author_info
    )

    region_match = re.search(
        r"地区[：:]\s*([^\s]+)",
        author_info
    )

    announcement["publish_date"] = (
        date_match.group(1) if date_match else ""
    )

    announcement["region"] = (
        region_match.group(1) if region_match else ""
    )

    # 星河易采没有 article-author，或者别的站点结构不同时，从页面文字里找日期
    if not announcement["publish_date"]:
        announcement["publish_date"] = pick_publish_date(
            soup.get_text(" ", strip=True)[:3000]
        )

    # 7. 提取公告正文
    content_tag = pick_content(soup)

    if not content_tag:
        print("没有找到公告正文：", detail_url)
        return announcement

    # 7.1 提取所有表格
    table_data = extract_table_fields(content_tag)

    # 7.2 提取正文段落
    paragraph_list = []

    for p in content_tag.find_all("p"):
        text = p.get_text(" ", strip=True)

        if text:
            paragraph_list.append(text)

    # 7.3 提取表格文本
    # 每个单元格单独成行。不要用 " ".join(row) 把整行拼成一句，
    # 否则 ["代理机构:", "工本费:"] 会被拼成 "代理机构: 工本费:"，
    # 被正则当成「标签 + 值」，抽出 "工本费:" 这种脏数据。
    for table in table_data:
        for row in table:
            for cell in row:
                if cell:
                    paragraph_list.append(cell)

    # 7.4 合并完整正文
    content = "\n".join(paragraph_list)

    announcement["content"] = content

    # 7.5 判断是否存在表格
    has_table = bool(table_data)

    # 8. 定义字段提取函数

    def extract_field(patterns):
        """
        没有表格时，从完整公告正文中提取字段。

        同一个正则在正文里可能匹配到多处，逐个检查：
        跳过被 clean_field_value() 判为脏的数据（如 "联系人:"），
        取第一个干净的结果。这样单个脏匹配不会把后面正确的匹配一起挡掉。
        """
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                value = clean_field_value(match.group(1))

                if value:
                    return value

        return ""

    def extract_value(patterns):
        """
        统一字段提取入口。

        提取顺序：
        1. 页面有表格时，先尝试从表格里提取；
        2. 表格里没取到（或页面本来就没有表格），再回退到全文正则。

        注意：不能"只要页面有表格就完全不回退"，
        否则表格里没写、只写在正文中的字段（如预算、截止时间）会永远是空值。
        """
        if has_table:
            value = extract_table_field(table_data, patterns)  # 先取值，不急着返回

            if value:  # 只有真取到内容才返回
                return value

        return extract_field(patterns)  # 取不到就往下走，用全文正则兜底

    # 9. 提取采购人
    #
    # 正则里的两条硬性约定，改之前务必看清（踩过坑）：
    #
    #   1. 字段名后的分隔符只用 (?:[：:]|[^\S\n])+，即「冒号 / 水平空白」，
    #      绝对不要用 \s——\s 含换行符，字段名落在行尾时（如独立一行的 "采购人:"）
    #      会跨过换行，把下一行的内容当成它的值抓走。
    #   2. 捕获组 [^，。,；;\n]+ 不排除冒号是允许的（截止时间 "09:00" 要用），
    #      但要靠 clean_field_value() 兜住"以冒号结尾"的脏值，详见该函数注释。
    announcement["purchaser"] = extract_value([
        r"(?:采\s*购\s*人|招\s*标\s*人|采购单位|招标单位)(?:[：:]|[^\S\n])+([^，。,；;\n]+)",
        r"本项目采购人为[^\S\n]*([^，。,；;\n]+)",
        r"本项目招标人为[^\S\n]*([^，。,；;\n]+)"
    ])

    # 10. 提取代理机构
    announcement["agency"] = extract_value([
        r"(?:采购代理机构|招标代理机构|代理机构)(?:[：:]|[^\S\n])+([^，。,；;\n]+)"
    ])

    # 11. 提取采购方式
    announcement["procurement_method"] = clean_method(extract_value([
        r"(?:采购方式|招标方式)[：:][^\S\n]*([^，。,；;\n]+)",

        # 「本项目进行谈判采购」「采用竞争性磋商方式」这类叙述句
        r"进行([^，。,；;\n]{2,8}?(?:谈判|磋商|询比|询价))",
        r"采用([^，。,；;\n]{2,8}?(?:谈判|磋商|询比|询价|公开招标|邀请招标))(?:方式|采购)"
    ]))

    # 如果正文没有明确说明，尝试从标题识别
    if not announcement["procurement_method"]:

        method_list = [
            "竞争性磋商",
            "竞争性谈判",
            "单一来源采购",
            "公开招标",
            "邀请招标",
            "询比价采购",
            "询比采购",
            "询比",
            "询价采购",
            "询价",
            "谈判",
            "磋商"
        ]

        for method in method_list:
            if method in title:
                announcement["procurement_method"] = method
                break

    # 12. 提取项目编号
    announcement["procurement_no"] = extract_value([
        r"(?:项目编号|采购编号|招标编号|采购代理编号|项目标段编号)[：:][^\S\n]*([A-Za-z0-9_\-]+)",

        # 通用规则：字段名称同时包含「项目/采购/招标」和「编号」
        r"(?=[^：:\n]*(?:项目|采购|招标))(?=[^：:\n]*编号)[^：:\n]+[：:][^\S\n]*([A-Za-z0-9_\-]+)"
    ])

    # 13. 提取预算金额
    budget_pattern = (
        r"(?:采购项目预算|项目预算|采购预算|预算金额|最高限价|控制价)"
        r"[^0-9]{0,20}([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:万元|元)?)"
    )

    announcement["budget"] = extract_value([
        budget_pattern
    ])

    if announcement["budget"]:
        announcement["budget"] = announcement["budget"].replace(",", "")

    # 14. 提取截止时间
    announcement["deadline"] = extract_value([
        r"(?:投标截止时间|响应文件提交截止时间|响应文件的提交截止时间|"
        r"投标文件递交截止时间|报价截止时间|开标时间)[：:][^\S\n]*([^，。,；;\n]+)",

        # 别的站点常写成「递交截止时间为 2025 年 11 月 14 日 9 时 30 分」，没有冒号
        r"(?:递交截止时间|提交截止时间|投标截止时间|报价截止时间|开标时间)"
        r"[^0-9]{0,10}([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日[^\n，。,；;]{0,14})"
    ])

    # 15. 提取项目内容或采购范围
    announcement["project_scope"] = extract_value([
        r"(?:服务范围|采购范围|项目内容|采购内容|服务内容|建设内容)[：:][^\S\n]*([^\n]+)",
        r"(?:服务范围|采购范围|项目内容|采购内容|服务内容|建设内容)[：:][^\S\n]*([^。\n]+)"
    ])

    # 16. 判断公告类型
    type_list = [
        ("中标公告", ["中标公告", "中标结果公告"]),
        ("成交公告", ["成交公告", "成交结果公告"]),
        ("采购结果公告", ["采购结果公告", "采购结果公示"]),
        ("招标公告", ["招标公告"]),
        ("询比公告", ["询比公告"]),
        ("采购公告", ["采购公告"]),
        ("更正公告", ["更正公告", "变更公告"]),
        ("废标公告", ["废标公告"]),
        ("流标公告", ["流标公告"])
    ]

    for announcement_type, keywords in type_list:
        if any(keyword in title for keyword in keywords):
            announcement["announcement_type"] = announcement_type
            break

    return announcement


