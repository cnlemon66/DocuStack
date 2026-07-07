"""
文档索引脚本（纯标准库版本）
支持多 Provider：OpenAI / 硅基流动 / 智谱
通过环境变量自动检测或手动指定
"""
import json
import re
import os
import math
from pathlib import Path
import urllib.request

RAW_DIR = Path(__file__).parent / "data" / "raw"
VECTOR_DIR = Path(__file__).parent / "data" / "vector_db"
INDEX_FILE = VECTOR_DIR / "index.json"

# ── Provider 配置 ──
PROVIDERS = {
    "siliconflow": {
        "name": "硅基流动 BAAI/bge-m3",
        "url": "https://api.siliconflow.cn/v1/embeddings",
        "model": "BAAI/bge-m3",
        "key_env": "SILICONFLOW_API_KEY",
    },
    "openai": {
        "name": "OpenAI text-embedding-3-small",
        "url": "https://api.openai.com/v1/embeddings",
        "model": "text-embedding-3-small",
        "key_env": "OPENAI_API_KEY",
    },
    "zhipu": {
        "name": "智谱 Embedding-3",
        "url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
        "model": "embedding-3",
        "key_env": "ZHIPU_API_KEY",
    },
}


def detect_provider():
    """自动检测可用的 Provider：优先环境变量指定，否则按序检查 Key"""
    forced = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    if forced and forced in PROVIDERS:
        cfg = PROVIDERS[forced]
        key = os.environ.get(cfg["key_env"], "")
        if key:
            return forced, cfg, key
        print(f"[WARN] EMBEDDING_PROVIDER={forced} 但未找到 {cfg['key_env']}")

    for pid, cfg in PROVIDERS.items():
        key = os.environ.get(cfg["key_env"], "")
        if key:
            return pid, cfg, key
    return None, None, None


def get_embedding(text: str, provider_cfg: dict, api_key: str) -> list[float]:
    """通用 Embedding API 调用"""
    data = json.dumps({
        "input": text,
        "model": provider_cfg["model"],
    }).encode("utf-8")
    req = urllib.request.Request(
        provider_cfg["url"],
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    return result["data"][0]["embedding"]


# ── 切块（A2 改进版）──

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
SECTION_OVERLAP = 100  # 相邻块之间的尾部重叠
CODE_BLOCK_MIN_LINES = 3  # 少于3行的代码块不强制保护


def split_sections(text: str) -> list[str]:
    """
    四层切分（A2 改进）：
    1. 保护代码块（```...```），用占位符替换，不拆散
    2. 按 ## / ### 标题切（保留标题作为上下文前缀）
    3. 章节内按段落切
    4. 长段落按句子边界切（。！？），最后才硬切
    所有相邻块之间叠加 SECTION_OVERLAP 字重叠
    """
    # 第〇步：保护代码块
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    code_placeholder = "__CB{}"
    for i, cb in enumerate(code_blocks):
        text = text.replace(cb, code_placeholder.format(i), 1)

    # 第一步：按标题切分
    sections = re.split(r'\n(?=##+ )', text)
    all_chunks = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # 提取标题行
        heading = ""
        body = section
        heading_match = re.match(r'^(##+ .+)', section)
        if heading_match:
            heading = heading_match.group(1) + "\n"
            body = section[heading_match.end():].strip()

        if not body and heading:
            if len(heading) >= 30:
                all_chunks.append(heading.strip())
            continue

        # 第二步：章节内按段落切
        paragraphs = re.split(r"\n\s*\n", body)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        for para in paragraphs:
            full_text = heading + para if heading else para

            if len(full_text) <= CHUNK_SIZE:
                all_chunks.append(full_text)
            else:
                # 检查是否是代码块占位符（集中一个块）
                is_code_placeholder = re.match(r'^__CB(\d+)__$', full_text.strip())
                if is_code_placeholder:
                    idx = int(is_code_placeholder.group(1))
                    cb = code_blocks[idx]
                    cb_lines = cb.count('\n')
                    # 小代码块整体保留，大代码块按 2×CHUNK_SIZE 分
                    if len(cb) <= CHUNK_SIZE * 2:
                        all_chunks.append(cb)
                    else:
                        for i in range(0, len(cb), CHUNK_SIZE - CHUNK_OVERLAP):
                            sub = cb[i:i + CHUNK_SIZE].strip()
                            if sub:
                                all_chunks.append(sub)
                    continue

                # 第三步：长段落优先按句子边界切
                lines = full_text.split("\n")
                current = ""
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    combined = current + "\n" + line if current else line
                    if len(combined) <= CHUNK_SIZE:
                        current = combined
                    else:
                        if current:
                            all_chunks.append(current)
                        if len(line) > CHUNK_SIZE:
                            # 按句子边界切
                            sentences = re.split(r'(?<=[。！？])\s*', line)
                            sent_buf = ""
                            for s in sentences:
                                s = s.strip()
                                if not s:
                                    continue
                                sc = sent_buf + s if sent_buf else s
                                if len(sc) <= CHUNK_SIZE:
                                    sent_buf = sc
                                else:
                                    if sent_buf:
                                        all_chunks.append(sent_buf)
                                    if len(s) > CHUNK_SIZE:
                                        for i in range(0, len(s), CHUNK_SIZE - CHUNK_OVERLAP):
                                            sub = s[i:i + CHUNK_SIZE].strip()
                                            if sub:
                                                all_chunks.append(sub)
                                        sent_buf = ""
                                    else:
                                        sent_buf = s
                            if sent_buf:
                                current = sent_buf
                            else:
                                current = ""
                        else:
                            current = line
                if current:
                    all_chunks.append(current)

    # 替换回代码块
    all_chunks = [_restore_code_blocks(c, code_blocks, code_placeholder) for c in all_chunks]

    # 过滤太短的
    all_chunks = [c for c in all_chunks if len(c) >= 30]

    # 相邻块加重叠
    if SECTION_OVERLAP > 0 and len(all_chunks) > 1:
        overlapped = []
        for i, chunk in enumerate(all_chunks):
            if i > 0:
                prev_tail = all_chunks[i - 1][-SECTION_OVERLAP:]
                chunk = prev_tail + "\n" + chunk
            overlapped.append(chunk)
        return overlapped

    return all_chunks


def _restore_code_blocks(text: str, code_blocks: list[str], placeholder: str) -> str:
    """将占位符替换回真实代码块"""
    for i, cb in enumerate(code_blocks):
        text = text.replace(placeholder.format(i), cb)
    return text


# ── TF-IDF（无 API Key 时的备选检索） ──

def compute_tfidf(chunks: list[str]) -> list[dict]:
    """为每个 chunk 构建词频向量"""
    # 简易分词（按字 + 双字组合，适合中文）
    all_docs_tokens = []
    for chunk in chunks:
        tokens = []
        for i, ch in enumerate(chunk):
            if ch.strip():
                tokens.append(ch)
                if i < len(chunk) - 1:
                    tokens.append(ch + chunk[i + 1])
        all_docs_tokens.append(tokens)

    # 计算 DF
    doc_count = len(chunks)
    df = {}
    for tokens in all_docs_tokens:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    # 计算每个文档的 TF-IDF 向量
    vectors = []
    for tokens in all_docs_tokens:
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        vec = {}
        for t, count in tf.items():
            idf = math.log((doc_count + 1) / (df.get(t, 1) + 1)) + 1
            vec[t] = count * idf
        vectors.append(vec)
    return vectors


# ── 主流程 ──

def build_index():
    txt_files = sorted(RAW_DIR.glob("*.txt"))
    if not txt_files:
        print("❌ data/raw/ 下没有 txt 文件，请先运行 crawl.py")
        return

    pid, pcfg, api_key = detect_provider()

    if pid:
        print(f"使用 {pcfg['name']}（{pid}）")
        print(f"  模型: {pcfg['model']}")
    else:
        print("[WARN] 未检测到任何 Embedding API Key")
        print("  支持的 Provider:")
        for p, c in PROVIDERS.items():
            print(f"    {c['name']:35s}  set {c['key_env']}=sk-xxx")
        print("  将使用 TF-IDF 检索（字面匹配，效果较差）\n")

    print(f"找到 {len(txt_files)} 个文档\n")

    # 切块
    all_chunks = []
    all_meta = []

    for fpath in txt_files:
        doc_name = fpath.stem
        text = fpath.read_text(encoding="utf-8")
        chunks = split_sections(text)
        print(f"  {doc_name}: {len(chunks)} 块 ({len(text)} 字符)")

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_meta.append({"source": doc_name, "chunk_index": i})

    print(f"\n总计 {len(all_chunks)} 个文本块")

    # 生成向量
    if pid:
        print(f"调用 Embedding API（{len(all_chunks)} 次）...")
        all_embeddings = []
        for i, chunk in enumerate(all_chunks):
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(all_chunks)}", flush=True)
            emb = get_embedding(chunk, pcfg, api_key)
            all_embeddings.append(emb)
        print(f"  {len(all_chunks)}/{len(all_chunks)} [OK]")
    else:
        print("计算 TF-IDF 向量 ...", end=" ", flush=True)
        all_embeddings = compute_tfidf(all_chunks)
        print("[OK]")

    # 始终计算 TF-IDF 向量（用于混合检索重排序）
    print("计算 TF-IDF 向量（用于混合检索）...", end=" ", flush=True)
    tfidf_vectors = compute_tfidf(all_chunks)
    print("[OK]")

    # 存盘
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    index_data = {
        "chunks": all_chunks,
        "meta": all_meta,
        "embeddings": all_embeddings,
        "tfidf_vectors": tfidf_vectors,
        "embedding_type": pid if pid else "tfidf",
    }
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False)
    print(f"\n向量库就绪: {INDEX_FILE}")
    print(f"共 {len(all_chunks)} 条记录 | 类型: {index_data['embedding_type']}")


if __name__ == "__main__":
    build_index()
