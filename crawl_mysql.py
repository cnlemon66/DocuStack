"""
MySQL 8.0 中文参考手册爬虫（纯标准库版本）
选爬章节：8 优化 / 11 数据类型 / 13 SQL语句 / 15 InnoDB
         + 7 备份恢复 / 12 函数 / 25 存储对象 / 5 服务器管理(选)
"""
import urllib.request
import re
import time
from pathlib import Path
from html.parser import HTMLParser

OUTPUT_DIR = Path(__file__).parent / "data" / "raw"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

BASE_URL = "https://mysql.net.cn/doc/refman/8.0/en/"

# 选爬章节（章节号 → 章节描述）
SELECTED_CHAPTERS = {
    5: "MySQL服务器管理",
    7: "备份与恢复",
    8: "优化",
    11: "数据类型",
    12: "函数和运算符",
    13: "SQL语句",
    15: "InnoDB存储引擎",
    25: "存储对象",
}


class TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "iframe"}
    BLOCK_TAGS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
                  "section", "article", "pre", "br", "hr", "table", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self.skip_depth += 1
        if t in self.BLOCK_TAGS and self.skip_depth == 0:
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if t in self.BLOCK_TAGS and self.skip_depth == 0:
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        text = data.strip()
        if text:
            self.text_parts.append(text)
            self.text_parts.append(" ")

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        raw = re.sub(r" {2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r" \n", "\n", raw)
        raw = re.sub(r"\n ", "\n", raw)
        return raw.strip()


def fetch_page(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ✗ 请求失败: {e}")
        return None


def get_sections_from_toc() -> list[dict]:
    """解析首页 TOC，提取所有章节链接并按章节分组"""
    html = fetch_page(BASE_URL)
    if not html:
        return []

    # TOC 结构中，chapter 和 section 交替出现：
    # <span class="chapter"><a href="xxx.html">N 标题</a>
    # <span class="section"><a href="yyy.html">N.M 子标题</a>
    sections = []

    # 提取所有 chapter 标题
    chapters = {}
    for m in re.finditer(r'<span class="chapter"><a href="([^"]+)">([^<]+)</a></span>', html):
        href = m.group(1)
        title = m.group(2).strip()
        num_m = re.search(r"(\d+)", title)
        if num_m:
            ch_num = int(num_m.group(1))
            chapters[ch_num] = {"href": href, "title": title}

    # 提取所有 section 链接
    for m in re.finditer(r'<span class="section"><a href="([^"]+)">([^<]+)</a></span>', html):
        href = m.group(1)
        title = m.group(2).strip()
        # 提取章节号（第一个数字）
        num_m = re.search(r"(\d+)", title)
        if not num_m:
            continue
        ch_num = int(num_m.group(1))
        if ch_num in SELECTED_CHAPTERS:
            sections.append({"ch": ch_num, "href": href, "title": title})

    print(f"  TOC 解析: 选中 {len(SELECTED_CHAPTERS)} 章, 共 {len(sections)} 小节")
    return sections


def crawl():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 50)
    print("MySQL 8.0 中文参考手册 - 选爬")
    print(f"选中章节: {', '.join(f'{k} {v}' for k, v in SELECTED_CHAPTERS.items())}")
    print("=" * 50)

    sections = get_sections_from_toc()
    if not sections:
        print("✗ TOC 解析失败")
        return

    total = len(sections)
    total_chars = 0
    idx = 0

    for sec in sections:
        idx += 1
        ch_num = sec["ch"]
        ch_name = SELECTED_CHAPTERS[ch_num]
        fname = f"mysql_{ch_num:02d}_{sec['href'].replace('.html', '')}"
        url = BASE_URL + sec["href"]
        title = sec["title"]

        print(f"[{idx:3d}/{total}] [{ch_name}] {title} ...", end=" ", flush=True)

        html = fetch_page(url)
        if html is None:
            continue

        parser = TextExtractor()
        parser.feed(html)
        text = parser.get_text()

        if len(text) < 100:
            print(f"⚠ 过短 ({len(text)} 字符)")
            continue

        out_path = OUTPUT_DIR / f"{fname}.txt"
        out_path.write_text(text, encoding="utf-8")

        chars = len(text)
        total_chars += chars
        print(f"✓ {chars} 字符")
        time.sleep(0.3)

    # 如果没有爬到章节页本身（比如章节页也被列在 sections 里了）
    # 补爬章节总览页
    crawled_hrefs = {s["href"] for s in sections}
    for ch_num, ch_info in [(5, "server-administration"), (7, "backup-and-recovery"),
                             (8, "optimization"), (11, "data-types"),
                             (12, "functions"), (13, "sql-statements"),
                             (15, "innodb-storage-engine"), (25, "stored-objects")]:
        href = ch_info + ".html"
        if href in crawled_hrefs:
            continue
        idx += 1
        fname = f"mysql_{ch_num:02d}_{ch_info}"
        url = BASE_URL + href
        print(f"[{idx:3d}/{total}] [{SELECTED_CHAPTERS[ch_num]}] 章总览 ...", end=" ", flush=True)

        html = fetch_page(url)
        if html is None:
            continue

        parser = TextExtractor()
        parser.feed(html)
        text = parser.get_text()

        if len(text) < 100:
            print(f"⚠ 过短 ({len(text)} 字符)")
            continue

        out_path = OUTPUT_DIR / f"{fname}.txt"
        out_path.write_text(text, encoding="utf-8")
        chars = len(text)
        total_chars += chars
        print(f"✓ {chars} 字符")
        time.sleep(0.3)

    print(f"\n完成！共 {idx} 页, {total_chars} 字符, 保存至 {OUTPUT_DIR}")


if __name__ == "__main__":
    crawl()
