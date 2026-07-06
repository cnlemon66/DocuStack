"""
MyBatis RAG - 核心模块
LLM Provider / Embedding / 安全校验 / 检索 / 流式调用
"""
import json
import os
import math
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
INDEX_FILE = ROOT / "data" / "vector_db" / "index.json"
CONFIG_FILE = ROOT / "config.json"
TOP_K = 5

# ═══════════ LLM Provider ═══════════

LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek-V3 (chat)"},
            {"id": "deepseek-reasoner", "name": "DeepSeek-R1 (reasoner)"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
        ],
    },
    "zhipu": {
        "name": "智谱 AI",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-4", "name": "GLM-4"},
            {"id": "glm-4-flash", "name": "GLM-4 Flash"},
        ],
    },
    "siliconflow": {
        "name": "硅基流动",
        "api_base": "https://api.siliconflow.cn/v1",
        "models": [
            {"id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek-V3"},
            {"id": "deepseek-ai/DeepSeek-R1", "name": "DeepSeek-R1"},
            {"id": "Qwen/Qwen2.5-7B-Instruct", "name": "Qwen2.5 7B"},
            {"id": "Pro/Qwen/Qwen2.5-7B-Instruct", "name": "Qwen2.5 7B (Pro)"},
        ],
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "api_base": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "moonshot-v1-8k", "name": "Moonshot v1 8K"},
            {"id": "moonshot-v1-32k", "name": "Moonshot v1 32K"},
            {"id": "moonshot-v1-128k", "name": "Moonshot v1 128K"},
        ],
    },
    "mimo": {
        "name": "小米 MiMo",
        "api_base": "https://api.xiaomimimo.com/v1",
        "models": [
            {"id": "mimo-v2.5-pro", "name": "MiMo-V2.5-Pro (旗舰)"},
            {"id": "mimo-v2.5", "name": "MiMo-V2.5 (均衡)"},
            {"id": "mimo-v2-flash", "name": "MiMo-V2-Flash (轻量)"},
        ],
    },
    "mimo-tp": {
        "name": "小米 MiMo (Token Plan)",
        "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
        "models": [
            {"id": "mimo-v2.5-pro", "name": "MiMo-V2.5-Pro (旗舰)"},
            {"id": "mimo-v2.5", "name": "MiMo-V2.5 (均衡)"},
            {"id": "mimo-v2-flash", "name": "MiMo-V2-Flash (轻量)"},
        ],
    },
    "custom": {
        "name": "自定义",
        "api_base": "",
        "models": [],
    },
}


def get_llm_config():
    cfg = load_config()
    llm = cfg.get("llm")
    if llm and isinstance(llm, dict):
        provider = llm.get("provider", "deepseek")
        if provider not in LLM_PROVIDERS:
            provider = "deepseek"
        preset = LLM_PROVIDERS[provider]
        models = preset.get("models", [])
        default_model = models[0]["id"] if models else ""
        return {
            "provider": provider,
            "name": preset["name"],
            "model": llm.get("model") or default_model,
            "api_key": llm.get("api_key", ""),
            "api_base": llm.get("api_base") or preset["api_base"],
        }
    return {
        "provider": "deepseek",
        "name": "DeepSeek",
        "model": "deepseek-chat",
        "api_key": cfg.get("deepseek_api_key", ""),
        "api_base": "https://api.deepseek.com",
    }


# ═══════════ Embedding Provider ═══════════

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
    forced = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()

    def get_key(pid):
        cfg = PROVIDERS[pid]
        key = os.environ.get(cfg["key_env"], "")
        if key:
            return cfg, key
        try:
            jcfg = load_config()
            k = jcfg.get(cfg["key_env"], "") or jcfg.get("openai_api_key", "")
            if k:
                return cfg, k
        except Exception:
            pass
        return None, None

    if forced and forced in PROVIDERS:
        cfg, key = get_key(forced)
        if key:
            return forced, cfg, key
    for pid in PROVIDERS:
        cfg, key = get_key(pid)
        if key:
            return pid, cfg, key
    return None, None, None


# ═══════════ 配置读写 ═══════════

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_index():
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════ 安全 ═══════════

import re as _re

MAX_QUERY_LEN = 2000
MAX_HISTORY_ROUNDS = 50

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|directives?)",
    r"system\s*:\s*(you\s+are|now|new|override|forget)",
    r"<\s*\|\s*im_start\s*\|\s*>",
    r"<\s*\|\s*im_end\s*\|\s*>",
    r"DAN\s+mode|developer\s+mode|jailbreak",
    r"pretend\s+(you\s+are|to\s+be)",
    r"from\s+now\s+on.*you\s+(are|will|must)",
]

KEY_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,60}", "sk-***"),
    (r"tp-[a-zA-Z0-9]{20,60}", "tp-***"),
    (r"Bearer\s+[a-zA-Z0-9_\-]{20,80}", "Bearer ***"),
]

SCRIPT_PATTERN = r"<\s*(script|iframe|object|embed|form|input|style)\b[^>]*>.*?</\s*\1\s*>"


def validate_input(query: str):
    if not query or not query.strip():
        return False, "输入不能为空"
    if len(query) > MAX_QUERY_LEN:
        return False, f"输入过长（{len(query)}/{MAX_QUERY_LEN} 字符）"
    if _re.search(r"(.)\1{50,}", query):
        return False, "输入包含大量重复字符"
    ql = query.lower()
    for pat in INJECTION_PATTERNS:
        if _re.search(pat, ql, _re.IGNORECASE):
            return False, "输入包含不被允许的内容"
    return True, ""


def sanitize_output(text: str) -> str:
    for pat, repl in KEY_PATTERNS:
        text = _re.sub(pat, repl, text)
    text = _re.sub(SCRIPT_PATTERN, "[已过滤]", text, flags=_re.IGNORECASE | _re.DOTALL)
    return text


# ═══════════ 检索 ═══════════

def cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def tfidf_sim(qv, dv):
    dot = sum(qv.get(k, 0) * dv.get(k, 0) for k in qv)
    nq = math.sqrt(sum(v * v for v in qv.values()))
    nd = math.sqrt(sum(v * v for v in dv.values()))
    return dot / (nq * nd) if nq and nd else 0.0


def tokenize(text):
    vec = {}
    for i, ch in enumerate(text):
        if ch.strip():
            vec[ch] = vec.get(ch, 0) + 1
            if i < len(text) - 1:
                bigram = ch + text[i + 1]
                vec[bigram] = vec.get(bigram, 0) + 1
    return vec


def api_post(url, data, api_key, timeout=60):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_embedding(text, provider_cfg, api_key):
    r = api_post(provider_cfg["url"],
                 {"input": text, "model": provider_cfg["model"]}, api_key)
    return r["data"][0]["embedding"]


# ── 域路由 ──

_DOMAIN_MAP = {
    "mysql": "mysql",
    "redis": "redis",
    "mb": "mybatis",
    "mbs": "mybatis",
    "mp": "mybatis",
}


# ── 简单 LRU 缓存（替代 lru_cache，因为 idx 不可哈希） ──

_S_CACHE = {}
_S_CACHE_ORDER = []
_S_CACHE_MAX = 256


def _cache_get(key: tuple) -> any | None:
    if key in _S_CACHE:
        _S_CACHE_ORDER.remove(key)
        _S_CACHE_ORDER.append(key)
        return _S_CACHE[key]
    return None


def _cache_put(key: tuple, value):
    _S_CACHE[key] = value
    _S_CACHE_ORDER.append(key)
    if len(_S_CACHE) > _S_CACHE_MAX:
        oldest = _S_CACHE_ORDER.pop(0)
        del _S_CACHE[oldest]


def _detect_domain(source: str) -> str:
    """从源文件名提取域"""
    for prefix, domain in _DOMAIN_MAP.items():
        if source.startswith(prefix):
            return domain
    return "mybatis"


def search(query, idx, domain=None, openai_key=None):
    # LRU 缓存查找
    cache_key = (query.strip(), domain)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    chunks = idx["chunks"]
    metas = idx["meta"]
    etype = idx["embedding_type"]
    t0 = time.time()

    # 域过滤
    if domain:
        domain_indices = [i for i, m in enumerate(metas) if _detect_domain(m["source"]) == domain]
        if not domain_indices:
            _cache_put(cache_key, ([], 0))
            return [], 0
        chunks = [chunks[i] for i in domain_indices]
        metas = [metas[i] for i in domain_indices]
        if idx["embeddings"]:
            embeddings = [idx["embeddings"][i] for i in domain_indices]
        else:
            embeddings = []
        tfidf_v = [idx["tfidf_vectors"][i] for i in domain_indices] if idx.get("tfidf_vectors") else []
    else:
        embeddings = idx["embeddings"]
        tfidf_v = idx.get("tfidf_vectors", [])

    is_embedding = etype and etype != "tfidf"
    has_tfidf = bool(tfidf_v)

    if is_embedding:
        pid, pcfg, api_key = detect_provider()
        if pid and api_key:
            q_emb = get_embedding(query, pcfg, api_key)
            scored = [(cosine_sim(q_emb, emb), i) for i, emb in enumerate(embeddings)]
            scored.sort(key=lambda x: x[0], reverse=True)

            # 混合检索：Embedding top20 → TF-IDF 重排序
            if has_tfidf and len(scored) > TOP_K:
                qv = tokenize(query)
                rerank_n = min(20, len(scored))
                reranked = []
                for emb_score, i in scored[:rerank_n]:
                    tfidf_s = tfidf_sim(qv, tfidf_v[i])
                    combined = emb_score * 0.7 + tfidf_s * 0.3
                    reranked.append((combined, i))
                reranked.sort(key=lambda x: x[0], reverse=True)
                # 合并：reranked 的 top5 + 剩余原始排序
                scored = reranked + scored[rerank_n:]
        else:
            print(f"⚠ Embedding 索引({etype}) 但无可用 Key")
            return [], 0
    else:
        qv = tokenize(query)
        scored = [(tfidf_sim(qv, vec), i) for i, vec in enumerate(embeddings)]

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

    _cache_put(cache_key, (results, elapsed))
    return results, elapsed


# ═══════════ LLM 调用 ═══════════

def call_llm(system_prompt: str, user_prompt: str, llm_config: dict) -> tuple[str, int]:
    data = json.dumps({
        "model": llm_config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "stream": False,
    }).encode("utf-8")
    url = llm_config["api_base"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {llm_config['api_key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    tokens = usage.get("total_tokens", 0)
    return content, tokens


def stream_llm(messages, llm_config):
    data = json.dumps({
        "model": llm_config["model"],
        "messages": messages,
        "temperature": 0.3,
        "stream": True,
    }).encode("utf-8")
    url = llm_config["api_base"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {llm_config['api_key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        buffer = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line or line == b"data: [DONE]":
                    continue
                if line.startswith(b"data: "):
                    line = line[6:]
                try:
                    obj = json.loads(line)
                    content = obj["choices"][0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass


# ═══════════ Token 估算 ═══════════

def count_tokens(text: str) -> int:
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - chinese
    return int(chinese * 1.5 + other)


def count_history_tokens(history: list) -> int:
    total = 0
    for h in history:
        total += count_tokens(h.get("content", ""))
    return total
