"""
MyBatis + MyBatis-Spring 中文文档爬虫（纯标准库版本）
"""
import urllib.request
import re
import time
from pathlib import Path
from html.parser import HTMLParser

OUTPUT_DIR = Path(__file__).parent / "data" / "raw"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 多数据源：{ 前缀: { base_url, pages } }
SOURCES = {
    "mb": {
        "base": "https://mybatis.org/mybatis-3/zh_CN/",
        "pages": [
            "index.html",
            "getting-started.html",
            "configuration.html",
            "java-api.html",
            "sqlmap-xml.html",
            "dynamic-sql.html",
            "logging.html",
            "statement-builders.html",
        ],
    },
    "mbs": {
        "base": "https://mybatis.org/spring/zh_CN/",
        "pages": [
            "index.html",
            "getting-started.html",
            "factorybean.html",
            "transactions.html",
            "sqlsession.html",
            "mappers.html",
            "using-api.html",
        ],
    },
    "mp": {
        "base": "https://baomidou.com/",
        "pages": [
            "introduce/",
            "getting-started/",
            "guides/data-interface/",
            "guides/wrapper/",
            "reference/",
            "reference/annotation/",
            "reference/code-generator-configuration/",
            "plugins/",
            "reference/question/",
        ],
    },
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


def crawl():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total_pages = sum(len(v["pages"]) for v in SOURCES.values())
    print(f"开始爬取 MyBatis + MyBatis-Spring 中文文档 (共 {total_pages} 页)\n")

    total_chars = 0
    idx = 0
    for prefix, cfg in SOURCES.items():
        base = cfg["base"]
        pages = cfg["pages"]
        for path in pages:
            idx += 1
            clean = path.rstrip("/").replace(".html", "").replace("/", "_")
            fname = f"{prefix}_{clean}"
            print(f"[{idx}/{total_pages}] {fname} ...", end=" ", flush=True)

            html = fetch_page(base + path)
            if html is None:
                continue

            parser = TextExtractor()
            parser.feed(html)
            text = parser.get_text()

            if len(text) < 100:
                print(f"⚠ 内容过短 ({len(text)} 字符)，跳过")
                continue

            out_path = OUTPUT_DIR / f"{fname}.txt"
            out_path.write_text(text, encoding="utf-8")

            chars = len(text)
            total_chars += chars
            print(f"✓ {chars} 字符")
            time.sleep(0.5)

    print(f"\n完成！共爬取 {total_chars} 字符，保存至 {OUTPUT_DIR}")


if __name__ == "__main__":
    crawl()
