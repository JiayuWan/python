from pathlib import Path
from functools import lru_cache
from pathlib import Path


# ==========================
# 1. 读取关键词规则
# ==========================

def load_rules():
    # 规则文件与 keyword_filter.py 同目录，用 __file__ 定位，
    # 项目拷到哪、换到哪台电脑都能找到。
    rule_path = Path(__file__).resolve().parent / "keyword_rules.text"

    if not rule_path.exists():
        raise FileNotFoundError(f"找不到关键词规则文件：{rule_path}")

    content = rule_path.read_text(encoding="utf-8")

    if "[TITLE]" not in content or "[CONTENT]" not in content:
        raise ValueError("关键词规则文件缺少 TITLE 或 CONTENT 标记")

    title_part, content_part = content.split("[CONTENT]", 1)
    title_expression = title_part.split("[TITLE]", 1)[1].strip()
    content_expression = content_part.strip()

    return title_expression, content_expression


TITLE_KEYWORDS, CONTENT_KEYWORDS = load_rules()


# ==========================
# 2. 处理规则中的转义符
# ==========================

def normalize_expression(expression):
    expression = expression.replace("\\|", "|")
    expression = expression.replace("\\~", "~")
    expression = expression.replace("\\&", "&")
    return expression.strip()


# ==========================
# 3. 拆分关键词表达式
# ==========================

def tokenize(expression):
    expression = normalize_expression(expression)

    tokens = []
    current = ""

    for char in expression:

        if char in "|~&()":

            if current.strip():
                tokens.append(current.strip())
                current = ""

            tokens.append(char)

        else:
            current += char

    if current.strip():
        tokens.append(current.strip())

    return tokens


# ==========================
# 4. 解析关键词表达式
# ==========================

def parse_expression(tokens):

    position = 0

    def parse_or():
        nonlocal position

        node = parse_and()

        while position < len(tokens) and tokens[position] in ("|", "~"):

            operator = tokens[position]
            position += 1

            right = parse_and()

            node = (operator, node, right)

        return node

    def parse_and():
        nonlocal position

        node = parse_atom()

        while position < len(tokens) and tokens[position] == "&":

            position += 1

            right = parse_atom()

            node = ("&", node, right)

        return node

    def parse_atom():
        nonlocal position

        if position >= len(tokens):
            raise ValueError("关键词表达式不完整")

        token = tokens[position]

        if token == "(":

            position += 1

            node = parse_or()

            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("关键词表达式括号不匹配")

            position += 1

            return node

        if token in ("|", "~", "&", ")"):
            raise ValueError(f"关键词表达式格式错误：{token}")

        position += 1

        return ("keyword", token)

    result = parse_or()

    if position != len(tokens):
        raise ValueError("关键词表达式存在多余内容")

    return result


# ==========================
# 5. 执行关键词匹配
# ==========================


def match_node(text, node):
    """
    执行关键词匹配，同时返回命中的关键词
    返回：(是否命中, 命中的关键词列表)
    """

    operator = node[0]

    # 单个关键词
    if operator == "keyword":

        keyword = node[1]

        if keyword in text:
            return True, [keyword]

        return False, []

    # 或运算（|）：任意一边命中即可
    if operator == "|":

        left_matched, left_keywords = match_node(text, node[1])
        right_matched, right_keywords = match_node(text, node[2])

        matched = left_matched or right_matched

        keywords = []

        if left_matched:
            keywords.extend(left_keywords)

        if right_matched:
            keywords.extend(right_keywords)

        # 去重，保留原来的顺序
        keywords = list(dict.fromkeys(keywords))

        return matched, keywords

    # 排除运算（~）：A~B  =  A 匹配 且 B 不匹配
    # 例：传输网~传输网管 = 文本里出现了"传输网"但没有"传输网管"才算命中
    #     可以用来排除同名但不同类的公告（如排除"网管软件"只留"网络建设"）
    if operator == "~":

        left_matched, left_keywords = match_node(text, node[1])
        right_matched, _ = match_node(text, node[2])

        if left_matched and not right_matched:
            # 排除运算只暴露左侧（命中侧）的关键词
            return True, list(dict.fromkeys(left_keywords))

        return False, []

    # 与运算：两边都命中才算命中
    if operator == "&":

        left_matched, left_keywords = match_node(text, node[1])
        right_matched, right_keywords = match_node(text, node[2])

        if left_matched and right_matched:

            keywords = left_keywords + right_keywords

            keywords = list(dict.fromkeys(keywords))

            return True, keywords

        return False, []

    return False, []


@lru_cache(maxsize=8)
def _parse_expression(expression):
    """规则表达式只解析一次：几千个关键词的 OR 表达式，解析比匹配贵得多。"""
    return parse_expression(tokenize(expression))


def match_keywords(text, expression):
    """
    判断文本是否命中关键词规则
    同时返回命中的关键词
    """

    if not text or not expression:
        return False, []

    return match_node(text, _parse_expression(expression))

# ==========================
# 6. 标题和正文分别匹配
# ==========================

def filter_announcement(title, content):
    """
    分别匹配标题和正文关键词
    任意一处命中，就保留公告
    """

    title_matched, title_keywords = match_keywords(
        title,
        TITLE_KEYWORDS
    )

    content_matched, content_keywords = match_keywords(
        content,
        CONTENT_KEYWORDS
    )

    return {
        "title_matched": title_matched,
        "content_matched": content_matched,

        "title_matched_keywords": title_keywords,
        "content_matched_keywords": content_keywords,

        "matched_keywords": list(dict.fromkeys(
            title_keywords + content_keywords
        )),

        "matched": title_matched or content_matched
    }
