"""
RAG 问答 Agent v2（增强交互版）
- 检索过程可视化：显示搜索耗时、命中数、逐条得分
- 多轮对话：保留上下文，支持追问
- 关键词高亮引用来源
"""
import json
import os
import sys
import math
import time
import urllib.request
from pathlib import Path

INDEX_FILE = Path(__file__).parent / "data" / "vector_db" / "index.json"
TOP_K = 5
MAX_HISTORY = 6  # 保留最近 N 轮对话

# ── 终端颜色 ──
C = {
    "reset":  "\033[0m",
    "cyan":   "\033[96m",
    "yellow": "\033[93m",
    "green":  "\033[92m",
    "dim":    "\033[2m",
    "bold":   "\033[1m",
    "blue":   "\033[94m",
    "magenta":"\033[95m",
}


# ═══════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════

def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def tfidf_sim(qv: dict, dv: dict) -> float:
    dot = sum(qv.get(k, 0) * dv.get(k, 0) for k in qv)
    nq = math.sqrt(sum(v * v for v in qv.values()))
    nd = math.sqrt(sum(v * v for v in dv.values()))
    return dot / (nq * nd) if nq and nd else 0.0


def tokenize(text: str) -> dict:
    vec = {}
    for i, ch in enumerate(text):
        if ch.strip():
            vec[ch] = vec.get(ch, 0) + 1
            if i < len(text) - 1:
                bigram = ch + text[i + 1]
                vec[bigram] = vec.get(bigram, 0) + 1
    return vec


# ═══════════════════════════════════════════════════
# API 调用
# ═══════════════════════════════════════════════════

def api_post(url: str, data: dict, api_key: str, timeout: int = 60) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_embedding(text: str, api_key: str) -> list[float]:
    r = api_post("https://api.openai.com/v1/embeddings",
                 {"input": text, "model": "text-embedding-3-small"}, api_key)
    return r["data"][0]["embedding"]


# ═══════════════════════════════════════════════════
# 检索
# ═══════════════════════════════════════════════════

def search(query: str, idx: dict, openai_key: str | None = None) -> list[dict]:
    chunks = idx["chunks"]
    metas = idx["meta"]
    etype = idx["embedding_type"]

    t0 = time.time()

    if etype == "openai" and openai_key:
        q_emb = get_embedding(query, openai_key)
        scored = [(cosine_sim(q_emb, emb), i) for i, emb in enumerate(idx["embeddings"])]
    else:
        qv = tokenize(query)
        scored = [(tfidf_sim(qv, vec), i) for i, vec in enumerate(idx["embeddings"])]

    scored.sort(key=lambda x: x[0], reverse=True)
    elapsed = time.time() - t0

    results = []
    for score, i in scored[:TOP_K]:
        if score > 0:
            results.append({
                "text": chunks[i],
                "source": metas[i]["source"],
                "score": round(score, 4),
            })

    return results, elapsed, len(idx["chunks"])


# ═══════════════════════════════════════════════════
# 搜索过程可视化
# ═══════════════════════════════════════════════════

def render_search_bar(query: str, elapsed: float, total_docs: int):
    """渲染搜索状态条"""
    bar = f"{C['dim']}┌─ 搜索{C['reset']} "
    bar += f"{C['bold']}{query}{C['reset']}"
    bar += f"  {C['dim']}[{total_docs} 条索引 · {elapsed*1000:.0f}ms]{C['reset']}"
    print(bar)


def render_hit(i: int, chunk: dict):
    """渲染单条搜索结果"""
    source = chunk["source"]
    score = chunk["score"]
    # 得分颜色
    if score > 0.5:
        sc = f"{C['green']}{score}{C['reset']}"
    elif score > 0.2:
        sc = f"{C['yellow']}{score}{C['reset']}"
    else:
        sc = f"{C['dim']}{score}{C['reset']}"

    # 截取摘要（前 120 字符）
    preview = chunk["text"][:120].replace("\n", " ")
    if len(chunk["text"]) > 120:
        preview += "..."

    print(f"  {C['dim']}├ [{i}]{C['reset']} {C['cyan']}{source}{C['reset']} "
          f"{C['dim']}得分={sc}{C['dim']}  {preview}{C['reset']}")


def render_search_results(query: str, chunks: list[dict], elapsed: float, total_docs: int):
    """完整渲染搜索过程"""
    render_search_bar(query, elapsed, total_docs)
    if not chunks:
        print(f"  {C['dim']}└─ {C['yellow']}未命中{C['reset']}")
    else:
        for i, c in enumerate(chunks, 1):
            render_hit(i, c)
        print(f"  {C['dim']}└─ 命中 {len(chunks)} 条{C['reset']}")
    print()


# ═══════════════════════════════════════════════════
# LLM 回答
# ═══════════════════════════════════════════════════

def build_messages(query: str, chunks: list[dict], history: list[dict]) -> list[dict]:
    """构建含历史和检索上下文的 messages"""
    context_parts = []
    for i, c in enumerate(chunks, 1):
        context_parts.append(f"[文档{i}] 来源: {c['source']}\n{c['text']}")
    context = "\n\n---\n\n".join(context_parts)

    system = (
        "你是 MyBatis 技术文档助手。"
        "请严格基于下面提供的文档内容回答问题。"
        "如果文档中没有相关信息，请明确说「文档中未找到相关内容」，不要编造。"
        "回答时在关键信息后标注引用来源，格式为 [文档N]。"
        "回答请用中文，保留原始代码格式。"
        "如果用户追问上一轮的内容，结合历史对话和文档来回答。"
    )

    messages = [{"role": "system", "content": system}]

    # 加入最近几轮历史
    for h in history[-MAX_HISTORY:]:
        messages.append(h)

    # 当前问题 + 检索上下文
    user_msg = (
        f"=== 检索到的 MyBatis 文档 ===\n{context}\n=== 文档结束 ===\n\n"
        f"用户问题: {query}"
    )
    messages.append({"role": "user", "content": user_msg})

    return messages


def stream_answer(messages: list[dict], api_key: str):
    """流式输出 LLM 回答"""
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "stream": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )

    print(f"{C['green']}Agent{C['reset']}: ", end="", flush=True)

    with urllib.request.urlopen(req, timeout=120) as resp:
        buffer = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk
            # 解析 SSE 流
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line or line == b"data: [DONE]":
                    continue
                if line.startswith(b"data: "):
                    line = line[6:]
                try:
                    obj = json.loads(line)
                    delta = obj["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        print(content, end="", flush=True)
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
    print("\n")


# ═══════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════

def print_banner():
    print(f"""
{C['bold']}{C['cyan']}╔══════════════════════════════════════════════╗
║     MyBatis 文档 RAG 问答 Agent v2           ║
╚══════════════════════════════════════════════╝{C['reset']}
""")


def print_help():
    print(f"""  {C['dim']}命令:{C['reset']}
  {C['yellow']}/detail 问题{C['reset']}    只看检索结果，不调 LLM
  {C['yellow']}/clear{C['reset']}         清空对话历史
  {C['yellow']}/stats{C['reset']}         查看索引统计
  {C['yellow']}/help{C['reset']}          显示帮助
  {C['yellow']}/exit{C['reset']}          退出
""")


def ask_loop():
    if not INDEX_FILE.exists():
        print("❌ 索引文件不存在，请先运行 index.py")
        sys.exit(1)

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        idx = json.load(f)

    etype = idx.get("embedding_type", "tfidf")
    chunk_count = len(idx["chunks"])
    doc_sources = sorted(set(m["source"] for m in idx["meta"]))

    openai_key = os.environ.get("OPENAI_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")

    print_banner()

    if not deepseek_key:
        print(f"{C['yellow']}⚠ 未设置 DEEPSEEK_API_KEY，将只展示检索结果{C['reset']}\n")
        deepseek_key = input("输入 DeepSeek API Key 启动 (或回车跳过): ").strip()
        if not deepseek_key:
            print(f"{C['dim']}(检索模式){C['reset']}")

    print(f"  索引: {chunk_count} 块 | 文档: {len(doc_sources)} 篇 | 检索: {etype}")
    print(f"  LLM: {'DeepSeek ✓' if deepseek_key else '仅检索'}")
    print_help()

    history = []  # [{"role": "user/assistant", "content": ...}, ...]

    while True:
        try:
            raw = input(f"\n{C['bold']}{C['cyan']}你{C['reset']}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C['dim']}再见~{C['reset']}")
            break

        if not raw:
            continue

        if raw.lower() in ("/exit", "/quit"):
            print(f"{C['dim']}再见~{C['reset']}")
            break

        if raw == "/clear":
            history.clear()
            print(f"{C['dim']}对话历史已清空{C['reset']}")
            continue

        if raw == "/stats":
            print(f"\n{C['dim']}索引统计:{C['reset']}")
            print(f"  总块数: {chunk_count}")
            print(f"  检索方式: {etype}")
            print(f"  文档来源 ({len(doc_sources)} 篇):")
            for s in doc_sources:
                count = sum(1 for m in idx["meta"] if m["source"] == s)
                print(f"    {s}  ({count} 块)")
            continue

        if raw == "/help":
            print_help()
            continue

        # /detail 模式：只看检索
        if raw.startswith("/detail "):
            query = raw[8:].strip()
            chunks, elapsed, total = search(query, idx, openai_key)
            render_search_results(query, chunks, elapsed, total)
            for i, c in enumerate(chunks, 1):
                print(f"  {C['bold']}[{i}] {c['source']}{C['reset']}")
                print(f"  {c['text'][:500]}")
                print()
            continue

        # ── 正式问答 ──
        query = raw
        chunks, elapsed, total = search(query, idx, openai_key)
        render_search_results(query, chunks, elapsed, total)

        if not chunks:
            if deepseek_key:
                print(f"{C['green']}Agent{C['reset']}: 当前文档库中未找到相关内容。")
                print(f"  提示：试试换一种问法，或确认问题在 MyBatis 文档覆盖范围内。\n")
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": "未找到相关内容。"})
            continue

        if deepseek_key:
            messages = build_messages(query, chunks, history)
            try:
                stream_answer(messages, deepseek_key)
            except Exception as e:
                print(f"{C['yellow']}LLM 调用失败: {e}{C['reset']}\n")
                # 失败时也展示检索结果
                print(f"{C['dim']}检索到的原始内容:{C['reset']}")
                for i, c in enumerate(chunks, 1):
                    print(f"\n{C['bold']}[{i}] {c['source']}{C['reset']}")
                    print(c["text"][:400])

            history.append({"role": "user", "content": query})
            # 简单记录回答（实际流式输出后无法取回内容，这里用占位）
            history.append({"role": "assistant", "content": "(已基于文档回答)"})
        else:
            print(f"{C['green']}检索结果{C['reset']}:")
            for i, c in enumerate(chunks, 1):
                print(f"\n{C['bold']}[{i}] {c['source']}  得分={c['score']}{C['reset']}")
                print(c["text"][:300])
            print()


if __name__ == "__main__":
    ask_loop()
