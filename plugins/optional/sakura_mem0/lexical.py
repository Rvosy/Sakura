"""中文分词用于 SQLite FTS5，查询与入库使用同一套词表。"""

from __future__ import annotations

import re


_STOPWORDS = frozenset(
    "的 了 是 在 和 与 或 就 都 也 又 还 很 把 被 给 对 将 着 过 呢 吗 啊 吧 "
    "我 你 他 她 它 我们 你们 他们 这个 那个 这些 那些 一下 什么 怎么 为什么 "
    "可以 能 需要 进行 一个 一些 已经 现在 还是 但是 然后 因为 所以 自己 用户 "
    "之前 以前 上次 平时 一般 什么时候 哪里 哪家 哪些 哪条 怎么样 希望 用 做 请 帮 "
    "好 好的 嗯 哦 谢谢".split()
)


def tokens(text: str) -> list[str]:
    import jieba

    jieba.setLogLevel(40)
    return [word for word in jieba.cut_for_search(text.casefold())
            if re.search(r"[\w]", word) and word not in _STOPWORDS]


def index_text(text: str) -> str:
    return " ".join(tokens(text))


def match_query(text: str) -> tuple[str, set[str]]:
    words = set(tokens(text))
    words = {word for word in words if not any(word != other and word in other for other in words)}
    return " OR ".join('"' + word.replace('"', '""') + '"' for word in sorted(words)), words
