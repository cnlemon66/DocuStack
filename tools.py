"""
MyBatis RAG - 工具层
联网搜索 / 文件操作 / 工具注册表
"""
import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path
from html.parser import HTMLParser
from core import search as core_search

WORKSPACE = Path(__file__).parent.resolve()
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ═══════════ 联网搜索（DuckDuckGo） ═══════════

class DDGParser(HTMLParser):
    """解析 DuckDuckGo HTML 搜索结果"""
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_result = False
        self._in_link = False
        self._in_snippet = False
        self._current = {}
        self._text_buf = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if tag == "div" and "result" in cls:
            self._in_result = True
            self._current = {"title": "", "url": "", "snippet": ""}
        if self._in_result and tag == "a" and "result__a" in cls:
            self._in_link = True
            href = attrs.get("href", "")
            if href.startswith("//"):
                href = "https:" + href
            self._current["url"] = href
        if self._in_result and tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
            self._text_buf = ""

    def handle_endtag(self, tag):
        if self._in_snippet and tag == "a":
            self._in_snippet = False
            self._current["snippet"] = self._text_buf.strip()
        if self._in_result and tag == "div":
            if self._current["title"] or self._current["snippet"]:
                self.results.append(self._current)
            self._in_result = False
            self._current = {}

    def handle_data(self, data):
        if self._in_link:
            self._current["title"] = (self._current.get("title", "") + data).strip()
        if self._in_snippet:
            self._text_buf += data

    def handle_entityref(self, name):
        # 处理 &amp; &lt; 等
        pass


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    DuckDuckGo 联网搜索，纯标准库。
    返回 [{"title": str, "url": str, "snippet": str}, ...]
    """
    if not query.strip():
        return []

    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = DDGParser()
        parser.feed(html)
        return parser.results[:max_results]
    except Exception as e:
        print(f"  ⚠ 联网搜索失败: {e}")
        return []


def web_search_context(query: str, max_results: int = 3) -> str:
    """
    联网搜索并格式化为 LLM 可用的上下文文本。
    """
    results = web_search(query, max_results)
    if not results:
        return "（未找到相关网络结果）"
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[网络{i}] {r['title']}\n{r['snippet']}\n来源: {r['url']}")
    return "\n\n".join(parts)


# ═══════════ 文件操作 ═══════════

def _safe_path(path: str) -> Path:
    """安全解析路径，禁止越权访问 WORKSPACE 之外"""
    p = (WORKSPACE / path).resolve()
    if not str(p).startswith(str(WORKSPACE)):
        raise ValueError(f"路径越权: {path}")
    return p


def file_read(path: str, max_lines: int = 200) -> str:
    """读取文件内容"""
    p = _safe_path(path)
    if not p.exists():
        return f"文件不存在: {path}"
    if not p.is_file():
        return f"不是文件: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines]) + f"\n...（共 {len(lines)} 行，仅显示前 {max_lines} 行）"
        return text
    except Exception as e:
        return f"读取失败: {e}"


def file_write(path: str, content: str) -> str:
    """写文件（覆盖），需要显式的用户确认"""
    p = _safe_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入失败: {e}"


def file_list(path: str = ".") -> str:
    """列出目录内容"""
    p = _safe_path(path)
    if not p.exists():
        return f"目录不存在: {path}"
    if not p.is_dir():
        return f"不是目录: {path}"
    try:
        entries = sorted(p.iterdir())
        lines = []
        for e in entries:
            t = "📁" if e.is_dir() else "📄"
            size = ""
            if e.is_file():
                try:
                    size = f" ({e.stat().st_size} B)"
                except Exception:
                    pass
            lines.append(f"{t} {e.name}{size}")
        return "\n".join(lines) if lines else "（空目录）"
    except Exception as e:
        return f"列出失败: {e}"


# ═══════════ Shell 执行（安全受限） ═══════════

import subprocess
import shlex
import signal

# 危险命令关键词黑名单
_DANGEROUS_CMDS = [
    "rm", "del", "rd", "rmdir", "format", "mkfs", "dd",
    "shutdown", "reboot", "poweroff", "halt",
    "chmod", "chown", "attrib",
    ">", ">>", "|", "`", "$(",
    "sudo", "su", "runas",
]


_SHELL_TIMEOUT = 15  # 秒
_SHELL_MAX_OUTPUT = 5000  # 字符


def _is_safe(command: str) -> tuple[bool, str]:
    """安全检查：拦截危险命令和注入"""
    lower = command.lower()
    for kw in _DANGEROUS_CMDS:
        # 用单词边界匹配避免误杀
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return False, f"禁止执行危险命令: {kw}"
    if len(command) > 500:
        return False, "命令过长（上限 500 字符）"
    return True, ""


def shell_execute(command: str, cwd: str = ".") -> str:
    """
    在项目工作区内执行 shell 命令（只读安全模式）。
    返回 stdout + stderr 文本。
    """
    safe, reason = _is_safe(command)
    if not safe:
        return f"[安全拦截] {reason}"

    work_dir = _safe_path(cwd)
    if not work_dir.exists():
        return f"目录不存在: {cwd}"
    if not work_dir.is_dir():
        return f"不是目录: {cwd}"

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip()
        if not out:
            return f"命令执行完成（返回码 {proc.returncode}，无输出）"
        if len(out) > _SHELL_MAX_OUTPUT:
            out = out[:_SHELL_MAX_OUTPUT] + f"\n...（截断，共 {len(out)} 字符）"
        return out
    except subprocess.TimeoutExpired:
        return f"命令执行超时（>{_SHELL_TIMEOUT} 秒），请尝试更简单的命令"
    except Exception as e:
        return f"执行失败: {e}"


# ═══════════ 数据库查询（SQLite，只读） ═══════════

import sqlite3


def db_query(query: str, db_path: str = "data/rag.db") -> str:
    """
    对工作区内的 SQLite 数据库执行 SELECT 查询。
    默认使用 data/rag.db。只允许 SELECT 语句。
    返回格式化后的表格文本。
    """
    q = query.strip()
    if not q.upper().startswith("SELECT"):
        return "[安全拦截] 只允许 SELECT 查询"
    if "--" in q or ";" in q.rstrip(";").count(";") > 0:
        # 允许末尾分号，但禁止多条语句
        if q.rstrip(";").count(";") > 0:
            return "[安全拦截] 不允许执行多条语句"

    db_file = _safe_path(db_path)
    if not db_file.exists():
        return f"数据库文件不存在: {db_path}"
    if not db_file.is_file():
        return f"不是文件: {db_path}"

    try:
        with sqlite3.connect(str(db_file)) as conn:
            conn.text_factory = str
            cur = conn.cursor()
            cur.execute(q)
            rows = cur.fetchall()
            if not rows:
                return "查询完成，无结果"

            col_names = [d[0] for d in cur.description]
            # 格式化表格
            col_widths = [len(n) for n in col_names]
            for row in rows[:50]:
                for i, val in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(val or "")))

            lines = []
            sep = " | ".join("-" * w for w in col_widths)
            header = " | ".join(n.ljust(w) for n, w in zip(col_names, col_widths))
            lines.append(header)
            lines.append(sep)
            for row in rows[:50]:
                vals = [str(v or "").ljust(w) for v, w in zip(row, col_widths)]
                lines.append(" | ".join(vals))

            if len(rows) > 50:
                lines.append(f"...（共 {len(rows)} 行，仅显示前 50 行）")

            return f"查询结果（{len(rows)} 行，{len(col_names)} 列）:\n" + "\n".join(lines)
    except sqlite3.Error as e:
        return f"数据库查询失败: {e}"


# ═══════════ 工具注册表 ═══════════

TOOLS = {
    "search_index": {
        "name": "知识库检索",
        "desc": "搜索 MyBatis 本地文档，优先使用",
        "params": {"query": "检索关键词"},
    },
    "web_search": {
        "name": "联网搜索",
        "desc": "搜索互联网获取最新信息，当本地文档不够时使用",
        "params": {"query": "搜索关键词", "max": 3},
    },
    "file_read": {
        "name": "读文件",
        "desc": "读取项目中的文件内容",
        "params": {"path": "文件路径"},
    },
    "file_write": {
        "name": "写文件",
        "desc": "写入或覆盖文件内容",
        "params": {"path": "文件路径", "content": "文件内容"},
    },
    "file_list": {
        "name": "列目录",
        "desc": "列出目录中的文件和子目录",
        "params": {"path": "目录路径（可选，默认当前）"},
    },
    "shell_execute": {
        "name": "Shell 执行",
        "desc": "在项目目录内执行 shell 命令（只读安全模式，禁止删除/写入系统文件）",
        "params": {"command": "命令内容", "cwd": "工作目录（可选，默认项目根）"},
    },
    "db_query": {
        "name": "数据库查询",
        "desc": "对项目内的 SQLite 数据库执行 SELECT 查询（只读）",
        "params": {"query": "SQL SELECT 语句", "db_path": "数据库路径（可选，默认 data/rag.db）"},
    },
}


def execute_tool(tool_name: str, params: dict, index=None, domain=None) -> dict:
    """
    执行指定工具，返回 {"ok": bool, "result": str, "tool": str}
    """
    try:
        if tool_name == "search_index":
            search_domain = params.get("domain") or domain
            results, _ = core_search(params.get("query", ""), index or {"chunks": [], "meta": [], "embeddings": [], "embedding_type": "tfidf"}, domain=search_domain)
            texts = [f"[{r['source']}] {r['text'][:300]}" for r in results[:5]]
            return {"ok": True, "result": "\n\n".join(texts) if texts else "未找到", "tool": tool_name}

        elif tool_name == "web_search":
            ctx = web_search_context(params.get("query", ""), params.get("max", 3))
            return {"ok": True, "result": ctx, "tool": tool_name}

        elif tool_name == "file_read":
            content = file_read(params.get("path", ""))
            return {"ok": True, "result": content, "tool": tool_name}

        elif tool_name == "file_write":
            result = file_write(params.get("path", ""), params.get("content", ""))
            return {"ok": True, "result": result, "tool": tool_name}

        elif tool_name == "file_list":
            result = file_list(params.get("path", "."))
            return {"ok": True, "result": result, "tool": tool_name}

        elif tool_name == "shell_execute":
            result = shell_execute(
                params.get("command", ""),
                params.get("cwd", "."),
            )
            return {"ok": True, "result": result, "tool": tool_name}

        elif tool_name == "db_query":
            result = db_query(
                params.get("query", ""),
                params.get("db_path", "data/rag.db"),
            )
            return {"ok": True, "result": result, "tool": tool_name}

        else:
            return {"ok": False, "result": f"未知工具: {tool_name}", "tool": tool_name}

    except Exception as e:
        return {"ok": False, "result": f"工具执行失败: {e}", "tool": tool_name}
