"""
MyBatis RAG - Agent 模块
编排层（PlannerOrchestrator）/ 会话层（冷热记忆 + 压缩）
"""
import json
import re
import time
import threading
from core import (
    call_llm, search, count_tokens, count_history_tokens,
)

# ═══════════ 会话层配置 ═══════════

TOKEN_BUDGET = 250_000
HOT_RATIO = 0.55
COLD_RATIO = 0.20
COMPRESS_TRIGGER = 0.75
HOT_KEEP_ROUNDS = 5

_sessions = {}

COMPRESS_SYSTEM = (
    "你是一个对话摘要压缩器。将多轮技术问答对话压缩为结构化摘要。\n"
    "规则：\n"
    "1. 只提取技术事实，不要评价、不要寒暄\n"
    "2. 每条信息控制在一行以内\n"
    "3. 参数配置类信息必须保留原数值，如 maxActive=20、timeout=5000\n"
    "   不允许压缩为\"配置了连接池\"，必须保留具体数字\n"
    "4. 输出格式固定为四部分：\n\n"
    "[主题]\n"
    "- 用户主要问了哪些技术问题（每个一行）\n\n"
    "[关键参数]\n"
    "- 对话中讨论到的所有配置参数和值，每行一个（无参数则写\"无\"）\n\n"
    "[结论]\n"
    "- 助手给出了哪些关键答案和方案（每个一行）\n\n"
    "[概念]\n"
    "- 涉及的核心技术名词（逗号分隔）\n\n"
    "控制在 500 字以内，中文输出。"
)


def compress_history(history: list[dict], llm_config: dict) -> str:
    if not history:
        return ""
    lines = []
    for i, h in enumerate(history, 1):
        role = "用户" if h["role"] == "user" else "助手"
        content = h["content"][:500]
        lines.append(f"[第{i}轮]\n{role}: {content}")
    dialog = "\n\n".join(lines)
    try:
        summary, _ = call_llm(COMPRESS_SYSTEM, f"请压缩以下对话（共 {len(history)} 轮）：\n\n{dialog}", llm_config)
        return summary.strip()
    except Exception:
        return ""


def maybe_compress(session_id: str, history: list[dict], llm_config: dict):
    if session_id not in _sessions:
        return
    session = _sessions[session_id]
    hot_tokens = count_history_tokens(history)
    cold_tokens = count_tokens(session.get("cold", ""))
    total = hot_tokens + cold_tokens
    if total < int(TOKEN_BUDGET * COMPRESS_TRIGGER) or hot_tokens < int(TOKEN_BUDGET * HOT_RATIO):
        return
    if session.get("compressing") or len(history) <= HOT_KEEP_ROUNDS:
        return
    old_history = history[:-HOT_KEEP_ROUNDS]

    def _do_compress():
        try:
            summary = compress_history(old_history, llm_config)
            if summary:
                with threading.Lock():
                    old_cold = session.get("cold", "")
                    session["cold"] = f"{old_cold}\n\n{summary}" if old_cold else summary
                    if count_tokens(session["cold"]) > int(TOKEN_BUDGET * COLD_RATIO):
                        try:
                            session["cold"], _ = call_llm(
                                "将以下对话历史摘要进一步压缩为 300 字以内的精炼版本。", session["cold"], llm_config)
                        except Exception:
                            pass
                    print(f"  ✓ 压缩完成: {len(old_history)} 轮")
        except Exception as e:
            print(f"  ⚠ 压缩异常: {e}")
        finally:
            session["compressing"] = False

    session["compressing"] = True
    threading.Thread(target=_do_compress, daemon=True).start()


def get_session(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {"cold": "", "compressing": False}
    return _sessions[session_id]


# ═══════════ 编排层（PlannerOrchestrator） ═══════════

ORCH_SYSTEM = (
    "分析用户问题，输出 JSON。\n"
    "intent: simple(直接) | compare(对比) | multi_step(分步) | debug(排错) | off_topic(无关)\n"
    "analysis: 一句话分析，20 字以内\n"
    "difficulty: 1-5\n"
    "steps: [{\"goal\":\"\",\"tool\":\"search_index|web_search|file_read|file_list\","
    "\"query\":\"\",\"domain\":null,\"depends_on\":null,\"fallback_query\":null}], depends_on \u6307\u5411\u4f9d\u8d56\u7684 step index\uff080-based\uff09\uff0cfallback_query \u662f\u4e0a\u4e00\u6b65\u7a7a\u7ed3\u679c\u65f6\u7684\u5907\u7528\u67e5\u8be2\n"
    "domain: \u641c\u7d22\u57df mybatis/mysql/redis\uff0c\u4ece\u4e0a\u4e0b\u6587\u63a8\u65ad\n"
    "synthesis_hint: \u56de\u7b54\u5efa\u8bae(\u53ef\u9009)\n\n"
    "\u53ef\u7528\u5de5\u5177\uff1asearch_index(\u9ed8\u8ba4)/web_search/file_read/file_list/shell_execute/db_query\n"
    "\u5982\u679c\u5bf9\u8bdd\u4e0a\u4e0b\u6587\u5728\u8ba8\u8bba MyBatis\uff0c\u5f53\u524d\u95ee\u9898\u5373\u4f7f\u6ca1\u63d0\u4e5f\u4e0d\u5224 off_topic\u3002\n"
    "\u53ea\u8f93\u51fa JSON\uff0c\u65e0\u89e3\u91ca\u3002"
)


# 判断简单问题的本地规则（零 LLM 调用）
_SIMPLE_PATTERNS = re.compile(
    r"^(怎么|如何|什么是|介绍|简述|解释|说一下)\S{0,10}$"
    r"|^\S{1,15}(怎么配置|怎么用|是什么|怎么实现|如何使用)$"
)


def _is_simple(query: str) -> bool:
    """快速判断是否是简单问题，不调 LLM"""
    q = query.strip()
    if len(q) > 30:
        return False
    if any(w in q for w in ["和", "与", "对比", "区别", "vs", "还是", "或者"]):
        return False
    return bool(_SIMPLE_PATTERNS.search(q))


_DOMAIN_KEYWORDS = {
    "redis": ["redis", "缓存", "分布式锁", "哨兵", "pipeline", "pub/sub", "缓存穿透",
              "缓存雪崩", "缓存击穿", "set", "get", "zset", "string", "hash",
              "list", "sorted set", "sentinel", "cluster", "主从"],
    "mysql": ["mysql", "数据库", "sql", "索引", "事务", "innodb", "mvcc",
              "锁", "表", "查询", "select", "join", "b+树", "explain",
              "慢查询", "分库分表", "读写分离"],
}


def _detect_local_domain(query: str, history: list) -> str | None:
    """本地规则检测域，不调 LLM"""
    combined = query
    # 加上最近对话上下文
    for h in history[-2:]:
        combined += " " + h.get("content", "")
    lower = combined.lower()

    scores = {"redis": 0, "mysql": 0}
    for domain, kws in _DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in lower:
                scores[domain] += 1

    if scores["redis"] > scores["mysql"] * 2:
        return "redis"
    if scores["mysql"] > scores["redis"] * 2:
        return "mysql"
    return None  # 不限域


def plan_and_execute(query: str, history: list, idx: dict, llm_config: dict) -> dict:
    """
    一体化编排：意图分类 + 步骤生成 + 并行执行 + 结果合并。
    返回 {
        "intent": str, "analysis": str, "difficulty": int,
        "steps": list, "synthesis_hint": str,
        "chunks": list, "timings": {"search": ms, "plan": ms}
    }
    """
    t0 = time.time()
    result = {"intent": "simple", "analysis": "", "difficulty": 1,
              "steps": [], "synthesis_hint": "", "chunks": [], "timings": {"plan": 0, "search": 0}}

    # 简单问题：本地规则判断，零 LLM
    # 同时做本地域推断
    local_domain = _detect_local_domain(query, history)

    if _is_simple(query):
        result["analysis"] = "直接回答"
        chunks, ms = search(query, idx, domain=local_domain)
        result["chunks"] = chunks
        result["timings"]["search"] = round(ms * 1000)
        result["timings"]["plan"] = round((time.time() - t0) * 1000)
        result["intent"] = "simple"
        result["domain"] = local_domain
        return result

    # 复杂问题：调 LLM 生成计划
    ctx = f"用户问题：{query}"
    if history and len(history) >= 2:
        ctx = "对话上下文：\n"
        for h in history[-4:]:
            role = "用户" if h["role"] == "user" else "助手"
            ctx += f"{role}: {h['content'][:100]}\n"
        ctx += f"\n当前问题：{query}"

    try:
        resp, _ = call_llm(ORCH_SYSTEM, ctx, llm_config)
        clean = resp.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            if clean.endswith("```"):
                clean = clean[:-3].strip()
        plan = json.loads(clean)
    except Exception as e:
        print(f"  ⚠ 编排失败: {e}")
        plan = {"intent": "simple", "steps": [], "analysis": ""}

    intent = plan.get("intent", "simple")
    steps = plan.get("steps", []) if intent in ("compare", "multi_step") else []
    plan_domain = plan.get("domain", local_domain)
    result["intent"] = intent
    result["analysis"] = plan.get("analysis", "")
    result["difficulty"] = plan.get("difficulty", 3)
    result["synthesis_hint"] = plan.get("synthesis_hint", "")
    result["domain"] = plan_domain
    result["timings"]["plan"] = round((time.time() - t0) * 1000)

    # ── 有步骤：依赖分析 + 分层并行执行 ──
    if steps:
        # 拓扑分层
        def _get_level(i, steps, depth=0):
            if depth > 10:
                return 0
            deps = steps[i].get("depends_on")
            if deps is None:
                return 0
            return _get_level(deps, steps, depth + 1) + 1

        level_map = {}
        for i in range(len(steps)):
            lv = _get_level(i, steps)
            level_map.setdefault(lv, []).append(i)

        lock = threading.Lock()
        all_chunks = []
        seen = set()
        step_results = {}  # step_index → 工具返回的文本，供依赖步使用

        def _exec_step(s, step_idx):
            from tools import execute_tool
            tool_name = s.get("tool", "search_index")
            step_domain = s.get("domain", plan_domain)
            query_text = s.get("query", query)

            # 数据流依赖：注入上一步的结果
            dep = s.get("depends_on")
            if dep is not None and dep in step_results:
                prev = step_results[dep]
                if prev:
                    query_text = f"{query_text}\n[参考上文检索结果: {prev[:300]}]"
                else:
                    # 上一步空结果 → 用 fallback_query 降级
                    query_text = s.get("fallback_query", query_text)

            params = {"query": query_text, "domain": step_domain}
            if tool_name in ("file_read", "file_list"):
                params = {"path": s.get("path", s.get("query", "."))}
            r = execute_tool(tool_name, params, idx)
            if not r.get("ok") or not r.get("result"):
                step_results[step_idx] = ""  # 标记空结果
                return
            text = r["result"]
            step_results[step_idx] = text
            with lock:
                sig = text[:80]
                if sig not in seen:
                    seen.add(sig)
                    score = 1.0 if tool_name != "search_index" else 0.3
                    all_chunks.append({
                        "text": text,
                        "source": tool_name,
                        "score": score,
                        "step_goal": s.get("goal", ""),
                        "tool": tool_name,
                    })

        for lv in sorted(level_map):
            indices = level_map[lv]
            threads = [threading.Thread(target=_exec_step, args=(steps[i], i)) for i in indices]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
        result["chunks"] = all_chunks[:10]

        # ── 结果验证 ──
        chunks = result["chunks"]
        if not chunks or len(chunks) < 2:
            # 降级搜索：用所有步骤的关键词
            ext_qs = [s.get("query", "") for s in steps if s.get("query")]
            fallback_q = " ".join(ext_qs[:3]) if ext_qs else query
            fb_chunks, _ = search(fallback_q, idx, domain=plan_domain)
            if fb_chunks:
                fb_seen = set(c["text"][:80] for c in chunks)
                for c in fb_chunks:
                    if c["text"][:80] not in fb_seen:
                        fb_seen.add(c["text"][:80])
                        chunks.append(c)
                chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
                result["chunks"] = chunks[:10]
                result["fallback"] = True

    else:
        # 无步骤：简单搜索
        chunks, ms = search(query, idx, domain=plan_domain)
        result["chunks"] = chunks
        result["timings"]["search"] = round(ms * 1000)

    result["timings"]["search"] = round((time.time() - t0) * 1000)
    return result


# ═══════════ 构建消息 ═══════════

def build_messages(query, chunks, history, cold_memory="", synthesis_hint=""):
    context_parts = []
    for i, c in enumerate(chunks, 1):
        prefix = ""
        if c.get("step_goal"):
            prefix = f"[步骤: {c['step_goal']}] "
        context_parts.append(f"[文档{i}] {prefix}来源: {c['source']}\n{c['text']}")
    context = "\n\n---\n\n".join(context_parts)

    system = (
        "你是 MyBatis 技术文档助手。请按以下结构回答问题：\n\n"
        "**问题分析**\n"
        "一句话说明用户想问什么，涉及哪几个技术点。\n\n"
        "**思考过程**\n"
        "从文档中提取相关信息，逐条说明每条信息的含义和适用场景。\n"
        "如果有多个方案，对比它们的区别和适用条件。\n\n"
        "**结论**\n"
        "用简洁的语言总结答案。如果有代码示例，用 ``` 包裹。\n"
        "每个关键信息后标注引用来源，格式为 [文档N]。\n\n"
        "**相关推荐**\n"
        "基于当前问题，推荐 2-3 个用户可能感兴趣的后续问题。\n"
        "每行一个，格式为「- 问题内容」（不用编号不用加粗）。\n\n"
        "规则：严格基于文档内容，找不到就说「文档中未找到相关内容」。"
    )

    if synthesis_hint:
        system += f"\n\n回答组织建议: {synthesis_hint}"

    messages = [{"role": "system", "content": system}]

    if cold_memory:
        messages.append({"role": "system", "content": (
            f"[历史对话摘要 — 之前的讨论涉及以下内容]\n{cold_memory}"
        )})

    hot_budget = int(TOKEN_BUDGET * HOT_RATIO) - count_tokens(system) - count_tokens(cold_memory) - count_tokens(context) - count_tokens(query)
    kept = []
    kept_tokens = 0
    for h in reversed(history):
        t = count_tokens(h.get("content", ""))
        if kept_tokens + t > hot_budget:
            break
        kept.insert(0, h)
        kept_tokens += t

    for h in kept:
        messages.append(h)

    messages.append({"role": "user", "content": (
        f"=== 检索到的 MyBatis 文档 ===\n{context}\n=== 文档结束 ===\n\n用户问题: {query}"
    )})
    return messages
