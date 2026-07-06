"""
Redis 中文文档爬虫（纯标准库版本）
来源：redis.com.cn（社区维护中文站）
爬取：核心主题 + 常用命令
"""
import urllib.request
import re
import time
from pathlib import Path
from html.parser import HTMLParser

OUTPUT_DIR = Path(__file__).parent / "data" / "raw"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

BASE_URL = "https://redis.com.cn"

# 核心主题 URL 路径列表（从 documentation.html 提取）
TOPIC_PATHS = [
    "topics/why-use-redis.html",
    "topics/data-types-intro.html",
    "topics/persistence.html",
    "topics/transactions.html",
    "topics/pubsub.html",
    "topics/pipelining.html",
    "topics/memory-optimization.html",
    "topics/lru-cache.html",
    "topics/distlock.html",
    "topics/notifications.html",
    "topics/redis-install.html",
    "topics/redis-config.html",
    "topics/redis-performance-tuning.html",
    "topics/redis-best-practices.html",
    "topics/redis-troubleshooting.html",
    "topics/redis-cache-problems.html",
    "topics/redis7-new-features.html",
    "topics/rediscli.html",
    "topics/replication.html",
    "topics/sentinel.html",
    "topics/security.html",
    "topics/encryption.html",
    "topics/clients.html",
    "topics/latency-monitor.html",
    "topics/benchmarks.html",
    "topics/cluster-tutorial.html",
    "topics/cluster-spec.html",
    "topics/protocol.html",
    "topics/internals.html",
    "topics/mass-insert.html",
    "topics/partitioning.html",
    "redis-interview-questions.html",
    "tutorial.html",
]

# 常用命令（TOP 50）
COMMON_COMMANDS = [
    # String
    "set", "get", "getset", "incr", "decr", "incrby", "decrby",
    "mget", "mset", "append", "strlen", "setnx", "setex",
    # Hash
    "hset", "hget", "hgetall", "hdel", "hexists", "hkeys", "hvals",
    "hlen", "hincrby", "hmset", "hmget",
    # List
    "lpush", "rpush", "lpop", "rpop", "llen", "lrange", "lindex",
    "lrem", "ltrim", "lset",
    # Set
    "sadd", "srem", "smembers", "sismember", "scard",
    "sinter", "sunion", "sdiff", "srandmember", "spop",
    # Sorted Set
    "zadd", "zrange", "zrevrange", "zrank", "zrevrank",
    "zrem", "zcard", "zcount", "zscore", "zincrby",
    # Key
    "del", "exists", "expire", "ttl", "pttl", "type", "keys",
    "rename", "sort", "scan", "randomkey",
    # HyperLogLog
    "pfadd", "pfcount", "pfmerge",
    # Geo
    "geoadd", "georadius", "geodist",
    # Stream
    "xadd", "xrange", "xread", "xgroup", "xreadgroup",
    # Server
    "info", "config", "client", "slowlog", "monitor", "dbsize",
    "flushall", "flushdb", "save", "bgsave", "lastsave",
    # Bitmap
    "setbit", "getbit", "bitcount", "bitop",
    # Transaction
    "multi", "exec", "discard", "watch", "unwatch",
    # Connection
    "auth", "ping", "echo", "select", "quit",
]


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


def crawl_topics():
    """爬取核心主题"""
    total = len(TOPIC_PATHS)
    chars = 0
    pages_crawled = 0

    print(f"\n--- Redis 核心主题 ({total} 页) ---")
    for i, path in enumerate(TOPIC_PATHS, 1):
        url = BASE_URL + "/" + path
        name = path.replace(".html", "").replace("topics/", "")
        fname = f"redis_{name}"

        print(f"  [{i:2d}/{total}] {name} ...", end=" ", flush=True)

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

        chars += len(text)
        pages_crawled += 1
        print(f"✓ {len(text)} 字符")
        time.sleep(0.3)

    return pages_crawled, chars


def crawl_commands():
    """爬取常用命令"""
    total = len(COMMON_COMMANDS)
    chars = 0
    pages_crawled = 0

    print(f"\n--- Redis 常用命令 ({total} 条) ---")
    for i, cmd in enumerate(COMMON_COMMANDS, 1):
        url = BASE_URL + "/commands/" + cmd + ".html"
        fname = f"redis_cmd_{cmd}"

        print(f"  [{i:2d}/{total}] {cmd} ...", end=" ", flush=True)

        html = fetch_page(url)
        if html is None:
            print("✗ 跳过")
            continue

        parser = TextExtractor()
        parser.feed(html)
        text = parser.get_text()

        if len(text) < 50:
            print("⚠ 内容过短")
            continue

        out_path = OUTPUT_DIR / f"{fname}.txt"
        out_path.write_text(text, encoding="utf-8")

        chars += len(text)
        pages_crawled += 1
        print(f"✓ {len(text)} 字符")
        time.sleep(0.2)

    return pages_crawled, chars


def crawl():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 50)
    print("Redis 中文文档 - 爬虫")
    print("来源: redis.com.cn（社区中文站）")
    print("=" * 50)

    t_pages, t_chars = crawl_topics()
    c_pages, c_chars = crawl_commands()

    total_pages = t_pages + c_pages
    total_chars = t_chars + c_chars
    print(f"\n完成！共 {total_pages} 页, {total_chars} 字符")
    print(f"  核心主题: {t_pages} 页, {t_chars} 字符")
    print(f"  命令参考: {c_pages} 页, {c_chars} 字符")
    print(f"  保存至: {OUTPUT_DIR}")


if __name__ == "__main__":
    crawl()
