"""
MyBatis RAG Web 服务
"""
import json
import os
import time
import urllib.request
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

from core import (
    ROOT, INDEX_FILE, CONFIG_FILE, TOP_K,
    load_config, save_config, load_index,
    get_llm_config, detect_provider, get_embedding,
    validate_input, sanitize_output,
    search, stream_llm, count_tokens,
    LLM_PROVIDERS, PROVIDERS, MAX_HISTORY_ROUNDS,
)
from agents import (
    plan_and_execute, build_messages,
    maybe_compress, get_session,
)
from tools import execute_tool, TOOLS
import mcp

CONV_DIR = ROOT / "data" / "conversations"


# ═══════════ HTTP Handler ═══════════

class Handler(BaseHTTPRequestHandler):
    idx = None
    config = None

    def log_message(self, format, *args):
        print(f"  {args[0]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_sse(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_DELETE(self):
        if self.path.startswith("/api/conversations"):
            conv_id = self.path[len("/api/conversations"):].strip("/")
            if conv_id:
                delete_conversation(self, conv_id)
            else:
                self.send_json({"error": "need id"}, 400)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            html = (ROOT / "index.html").read_text(encoding="utf-8")
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/config":
            cfg = load_config()
            pid, pcfg, pkey = detect_provider()
            llm = get_llm_config()
            safe = {
                "llm_set": bool(llm.get("api_key")),
                "llm_provider": llm["provider"],
                "llm_model": llm["model"],
                "embedding_set": pid is not None,
                "embedding_provider": pid or "none",
                "embedding_type": self.idx.get("embedding_type", "tfidf") if self.idx else "tfidf",
                "chunk_count": len(self.idx["chunks"]) if self.idx else 0,
                "llm_providers": [{"id": k, "name": v["name"], "models": v.get("models", [])} for k, v in LLM_PROVIDERS.items()],
                "tools": [{"id": k, "name": v["name"], "desc": v["desc"]} for k, v in TOOLS.items()],
            }
            self.send_json(safe)

        elif self.path.startswith("/api/conversations"):
            conv_id = self.path[len("/api/conversations"):].strip("/")
            if conv_id:
                get_conversation(self, conv_id)
            else:
                list_conversations(self)

        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/config":
            body = self.read_body()
            cfg = load_config()
            if body.get("llm"):
                cfg["llm"] = body["llm"]
            if body.get("deepseek_api_key"):
                cfg["deepseek_api_key"] = body["deepseek_api_key"]
            if body.get("openai_api_key"):
                cfg["openai_api_key"] = body["openai_api_key"]
            if body.get("siliconflow_api_key"):
                cfg["siliconflow_api_key"] = body["siliconflow_api_key"]
            save_config(cfg)
            self.send_json({"ok": True})

        elif self.path == "/api/search":
            body = self.read_body()
            query = body.get("query", "").strip()
            if not query:
                self.send_json({"error": "empty query"}, 400)
                return
            domain = body.get("domain")
            chunks, elapsed = search(query, self.idx, domain=domain)
            self.send_json({
                "chunks": chunks,
                "elapsed_ms": round(elapsed * 1000),
                "total_docs": len(self.idx["chunks"]) if self.idx else 0,
            })

        elif self.path == "/mcp":
            body = self.read_body()
            try:
                result = mcp.handle(body, idx=self.idx)
                if result is None:
                    self.send_response(202)
                    self.end_headers()
                else:
                    self.send_json(result)
            except Exception as e:
                self.send_json({"jsonrpc": "2.0", "id": body.get("id"),
                    "error": {"code": -32603, "message": str(e)}})

        elif self.path == "/api/chat":
            body = self.read_body()
            query = body.get("query", "").strip()
            history = body.get("history", [])
            session_id = body.get("session_id", "")

            if not query:
                self.send_json({"error": "empty query"}, 400)
                return

            valid, err = validate_input(query)
            if not valid:
                self.send_json({"error": err}, 400)
                return

            if len(history) > MAX_HISTORY_ROUNDS * 2:
                history = history[-(MAX_HISTORY_ROUNDS * 2):]

            cfg = load_config()
            llm_config = get_llm_config()
            if not llm_config.get("api_key"):
                self.send_json({"error": "请先设置 LLM API Key"}, 400)
                return

            cold_memory = ""
            if session_id:
                sess = get_session(session_id)
                cold_memory = sess.get("cold", "")

            self.send_sse()
            total_tokens = 0
            timings = {}
            t_total = time.time()

            try:
                # Planner
                t0 = time.time()
                plan = plan_and_execute(query, history, self.idx, llm_config)
                intent = plan["intent"]
                diff = plan["difficulty"]
                analysis = plan["analysis"]
                steps = plan["steps"]
                chunks = plan["chunks"]
                timings["plan"] = plan["timings"]["plan"]
                timings["search"] = plan["timings"]["search"]

                if analysis:
                    self.wfile.write(f"data: {json.dumps({'type':'analysis','content':analysis,'time_ms':timings['plan']},ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(f"data: {{\"type\":\"status\",\"content\":\"🔍 检索相关知识…\"}}\n\n".encode("utf-8"))
                self.wfile.flush()

                if intent == "off_topic":
                    if len(history) >= 2:
                        intent = "simple"
                        diff = 1
                    else:
                        self.wfile.write(f"data: {json.dumps({'type':'token','content':'抱歉，我是 MyBatis 技术文档助手，无法回答与 MyBatis 无关的问题。'},ensure_ascii=False)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        timings["total"] = round((time.time() - t_total) * 1000)
                        self.wfile.write(f"data: {json.dumps({'type':'done','tokens':0,'timings':timings,'hot_rounds':len(history),'cold_active':bool(cold_memory)},ensure_ascii=False)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        return

                iname = {"simple":"直接回答","compare":"对比分析","multi_step":"分步解决","debug":"排查错误"}.get(intent, intent)
                self.wfile.write(f"data: {json.dumps({'type':'plan','intent':intent,'intent_name':iname,\
                    'difficulty':diff,'steps':steps,'domain':plan.get('domain'),'time_ms':timings['plan']},ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

                search_event = {"type":"search","chunks":chunks,"elapsed_ms":timings["search"],
                    "total_docs":len(self.idx["chunks"]) if self.idx else 0,
                    "sub_queries":[s.get("query","") for s in steps] if steps else []}
                self.wfile.write(f"data: {json.dumps(search_event,ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

                if not chunks:
                    self.wfile.write("data: {\"type\": \"done\"}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    return

                messages = build_messages(query, chunks, history, cold_memory,
                    plan.get("synthesis_hint", ""))
                gen_chars = 0
                t_gen = time.time()
                try:
                    for token in stream_llm(messages, llm_config):
                        token = sanitize_output(token)
                        self.wfile.write(f"data: {json.dumps({'type':'token','content':token},ensure_ascii=False)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        gen_chars += len(token)
                except Exception as e:
                    self.wfile.write(f"data: {{\"type\":\"error\",\"content\":\"{str(e)}\"}}\n\n".encode("utf-8"))
                    self.wfile.flush()

                timings["generation"] = round((time.time() - t_gen) * 1000)
                total_tokens += int(gen_chars * 1.5)
                timings["total"] = round((time.time() - t_total) * 1000)

                stats = {"type":"done","tokens":total_tokens,"timings":timings,
                         "hot_rounds":len(history),"cold_active":bool(cold_memory)}
                self.wfile.write(f"data: {json.dumps(stats,ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

                if session_id and history:
                    try:
                        maybe_compress(session_id, history, llm_config)
                    except Exception:
                        pass

            except Exception as e:
                try:
                    self.wfile.write(f"data: {{\"type\":\"error\",\"content\":\"服务错误: {str(e)}\"}}\n\n".encode("utf-8"))
                    self.wfile.write("data: {\"type\": \"done\"}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

        elif self.path == "/api/test-llm":
            body = self.read_body()
            provider = body.get("provider", "deepseek")
            api_key = body.get("api_key", "").strip()
            model = body.get("model", "").strip()
            api_base = body.get("api_base", "").strip()

            if not api_key:
                self.send_json({"ok": False, "error": "API Key 不能为空"})
                return

            preset = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["deepseek"])
            if not model:
                models = preset.get("models", [])
                model = models[0]["id"] if models else ""
            if not api_base:
                api_base = preset["api_base"]

            t0 = time.time()
            try:
                data = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                    "stream": False,
                }).encode("utf-8")
                url = api_base.rstrip("/") + "/chat/completions"
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    json.loads(resp.read())
                ms = round((time.time() - t0) * 1000)
                self.send_json({"ok": True, "model": model, "ms": ms})
            except Exception as e:
                err = str(e)
                if "401" in err or "Unauthorized" in err:
                    err = "401 认证失败：API Key 无效或格式错误"
                elif "403" in err:
                    err = "403 拒绝访问：Key 无权限或账户欠费"
                elif "404" in err:
                    err = "404 未找到：API 地址或模型名称可能错误"
                elif "429" in err:
                    err = "429 请求过于频繁，请稍后重试"
                elif "timeout" in err.lower():
                    err = "连接超时：API 地址不可达或网络不通"
                elif len(err) > 200:
                    err = err[:200]
                self.send_json({"ok": False, "error": err})

        elif self.path == "/api/reindex":
            pid, pcfg, api_key = detect_provider()
            if not pid:
                self.send_json({"error": "未检测到 Embedding API Key"}, 400)
                return

            self.send_sse()
            self.wfile.write("data: {\"type\": \"status\", \"content\": \"重新索引中...\"}\n\n".encode("utf-8"))
            self.wfile.flush()

            try:
                rebuild_index(pcfg, api_key, pid, self)
                self.idx = load_index()
                self.wfile.write(
                    f"data: {{\"type\": \"done\", \"chunk_count\": {len(self.idx['chunks'])}}}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception as e:
                self.wfile.write(
                    f"data: {{\"type\": \"error\", \"content\": \"{str(e)}\"}}\n\n".encode("utf-8"))
                self.wfile.flush()

        elif self.path.startswith("/api/conversations"):
            conv_id = self.path[len("/api/conversations"):].strip("/")
            body = self.read_body()
            if conv_id:
                save_conversation(self, conv_id, body)
            else:
                create_conversation(self, body)

        else:
            self.send_json({"error": "not found"}, 404)


# ═══════════ 对话持久化 ═══════════

def _conv_path(conv_id):
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    return CONV_DIR / f"{conv_id}.json"

def list_conversations(handler):
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    convs = []
    for f in sorted(CONV_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            convs.append({"id": f.stem, "title": data.get("title","新对话"),
                "msg_count": len(data.get("messages",[])), "updated_at": data.get("updated_at","")})
        except Exception:
            pass
    handler.send_json(convs)

def get_conversation(handler, conv_id):
    p = _conv_path(conv_id)
    if not p.exists(): handler.send_json({"error":"not found"},404); return
    handler.send_json(json.loads(p.read_text(encoding="utf-8")))

def create_conversation(handler, body):
    import uuid
    conv_id = body.get("id") or uuid.uuid4().hex[:12]
    data = {"id":conv_id,"title":body.get("title","新对话"),"messages":body.get("messages",[]),
        "cold_memory":body.get("cold_memory",""),"created_at":time.strftime("%Y-%m-%d %H:%M"),
        "updated_at":time.strftime("%Y-%m-%d %H:%M")}
    _conv_path(conv_id).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    handler.send_json({"ok":True,"id":conv_id})

def save_conversation(handler, conv_id, body):
    p = _conv_path(conv_id)
    existing = {} if not p.exists() else json.loads(p.read_text(encoding="utf-8"))
    for k in ("id","title","messages","cold_memory"):
        existing[k] = body.get(k, existing.get(k, "" if k!="messages" else []))
    existing["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    if "created_at" not in existing: existing["created_at"] = existing["updated_at"]
    p.write_text(json.dumps(existing,ensure_ascii=False,indent=2),encoding="utf-8")
    handler.send_json({"ok":True})

def delete_conversation(handler, conv_id):
    p = _conv_path(conv_id)
    if p.exists(): p.unlink()
    handler.send_json({"ok":True})
# ═══════════ 重建索引 ═══════════

def rebuild_index(pcfg, api_key, pid, handler=None):
    import re as _re2
    RAW_DIR = ROOT / "data" / "raw"
    CHUNK_SIZE = 500
    CHUNK_OVERLAP = 100
    SECTION_OVERLAP = 80

    def split_sections(text):
        sections = _re2.split(r'\n(?=##+ )', text)
        all_chunks = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            heading = ""
            body = section
            heading_match = _re2.match(r'^(##+ .+)', section)
            if heading_match:
                heading = heading_match.group(1) + "\n"
                body = section[heading_match.end():].strip()
            if not body and heading:
                if len(heading) >= 30:
                    all_chunks.append(heading.strip())
                continue
            paragraphs = _re2.split(r"\n\s*\n", body)
            for para in [p.strip() for p in paragraphs if p.strip()]:
                full_text = heading + para if heading else para
                if len(full_text) <= CHUNK_SIZE:
                    all_chunks.append(full_text)
                else:
                    lines = full_text.split("\n")
                    current = ""
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        if len(current) + len(line) <= CHUNK_SIZE:
                            current += ("\n" if current else "") + line
                        else:
                            if current:
                                all_chunks.append(current)
                            if len(line) > CHUNK_SIZE:
                                for i in range(0, len(line), CHUNK_SIZE - CHUNK_OVERLAP):
                                    sub = line[i:i + CHUNK_SIZE]
                                    if sub.strip():
                                        all_chunks.append(sub.strip())
                            else:
                                current = line
                    if current:
                        all_chunks.append(current)
        all_chunks = [c for c in all_chunks if len(c) >= 30]
        if SECTION_OVERLAP > 0 and len(all_chunks) > 1:
            overlapped = []
            for i, chunk in enumerate(all_chunks):
                if i > 0:
                    chunk = all_chunks[i - 1][-SECTION_OVERLAP:] + "\n" + chunk
                overlapped.append(chunk)
            return overlapped
        return all_chunks

    txt_files = sorted(RAW_DIR.glob("*.txt"))
    all_chunks = []
    all_meta = []
    for fpath in txt_files:
        doc_name = fpath.stem
        text = fpath.read_text(encoding="utf-8")
        chunks = split_sections(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_meta.append({"source": doc_name, "chunk_index": i})

    total = len(all_chunks)
    all_embeddings = []
    for i, chunk in enumerate(all_chunks):
        if handler and (i + 1) % 20 == 0:
            handler.wfile.write(
                f"data: {{\"type\": \"status\", \"content\": \"Embedding {i+1}/{total}...\"}}\n\n".encode("utf-8"))
            handler.wfile.flush()
        emb = get_embedding(chunk, pcfg, api_key)
        all_embeddings.append(emb)

    # calculate TF-IDF vectors for hybrid search reranking
    if handler:
        handler.wfile.write(
            'data: {"type": "status", "content": "TF-IDF..."}\n\n'.encode("utf-8"))
        handler.wfile.flush()
    from index import compute_tfidf
    tfidf_vectors = compute_tfidf(all_chunks)

    index_data = {
        "chunks": all_chunks,
        "meta": all_meta,
        "embeddings": all_embeddings,
        "tfidf_vectors": tfidf_vectors,
        "embedding_type": pid,
    }
    VECTOR_DIR = ROOT / "data" / "vector_db"
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False)


# ═══════════ 主入口 ═══════════

def main():
    cfg = load_config()
    port = cfg.get("port", 8765)

    if INDEX_FILE.exists():
        print(f"加载索引 → {INDEX_FILE}")
        Handler.idx = load_index()
        print(f"  {len(Handler.idx['chunks'])} 块 | 类型: {Handler.idx.get('embedding_type', 'tfidf')}")
    else:
        print("⚠ 索引文件不存在，请先运行 index.py")
        Handler.idx = {"chunks": [], "meta": [], "embeddings": [], "embedding_type": "tfidf"}

    Handler.config = cfg
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  MyBatis RAG Web 服务已启动")
    print(f"  打开浏览器访问 → http://localhost:{port}")
    print(f"  按 Ctrl+C 停止\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
