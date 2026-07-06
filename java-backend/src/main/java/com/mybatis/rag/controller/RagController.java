package com.mybatis.rag.controller;

import com.mybatis.rag.service.ConfigService;
import com.mybatis.rag.service.DeepSeekService;
import com.mybatis.rag.service.SearchService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.*;

/**
 * REST 控制器：镜像 Python server.py 的 5 个端点
 */
@RestController
public class RagController {

    private final ConfigService configService;
    private final SearchService searchService;
    private final DeepSeekService deepSeekService;

    public RagController(ConfigService configService, SearchService searchService,
                         DeepSeekService deepSeekService) {
        this.configService = configService;
        this.searchService = searchService;
        this.deepSeekService = deepSeekService;
    }

    // ═══════════════════════════════════════════
    // GET /api/config
    // ═══════════════════════════════════════════
    @GetMapping("/api/config")
    public Map<String, Object> getConfig() {
        Map<String, Object> cfg = configService.load();
        Map<String, Object> safe = new LinkedHashMap<>();
        safe.put("deepseek_set", !configService.getDeepseekKey().isEmpty());
        safe.put("openai_set", !configService.getOpenaiKey().isEmpty());
        safe.put("embedding_type", searchService.getEmbeddingType());
        safe.put("chunk_count", searchService.getChunkCount());
        return safe;
    }

    // ═══════════════════════════════════════════
    // POST /api/config
    // ═══════════════════════════════════════════
    @PostMapping("/api/config")
    public Map<String, Object> saveConfig(@RequestBody Map<String, String> body) {
        configService.save(body.get("deepseek_api_key"), body.get("openai_api_key"));
        return Map.of("ok", true);
    }

    // ═══════════════════════════════════════════
    // POST /api/search
    // ═══════════════════════════════════════════
    @PostMapping("/api/search")
    public Map<String, Object> search(@RequestBody Map<String, String> body) {
        String query = body.getOrDefault("query", "").trim();
        if (query.isEmpty()) return Map.of("error", "empty query");

        long t0 = System.currentTimeMillis();
        List<Map<String, Object>> chunks = searchService.search(query, 5);
        long elapsed = System.currentTimeMillis() - t0;

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("chunks", chunks);
        result.put("elapsed_ms", elapsed);
        result.put("total_docs", searchService.getChunkCount());
        return result;
    }

    // ═══════════════════════════════════════════
    // POST /api/chat  （SSE 流式回答）
    // ═══════════════════════════════════════════
    @PostMapping("/api/chat")
    public void chat(@RequestBody Map<String, Object> body, HttpServletResponse response)
            throws IOException {
        String query = ((String) body.getOrDefault("query", "")).trim();
        List<Map<String, String>> history = (List<Map<String, String>>) body.getOrDefault("history", List.of());

        if (query.isEmpty()) {
            response.sendError(400, "empty query");
            return;
        }

        String deepseekKey = configService.getDeepseekKey();
        if (deepseekKey.isEmpty()) {
            response.sendError(400, "请先设置 DeepSeek API Key");
            return;
        }

        // SSE 响应头
        response.setContentType("text/event-stream");
        response.setCharacterEncoding("UTF-8");
        response.setHeader("Cache-Control", "no-cache");
        response.setHeader("Connection", "keep-alive");
        PrintWriter writer = response.getWriter();

        // 1. 检索
        long t0 = System.currentTimeMillis();
        List<Map<String, Object>> chunks = searchService.search(query, 5);
        long elapsed = System.currentTimeMillis() - t0;

        // 2. 发送检索结果
        Map<String, Object> searchEvent = new LinkedHashMap<>();
        searchEvent.put("type", "search");
        searchEvent.put("chunks", chunks);
        searchEvent.put("elapsed_ms", elapsed);
        searchEvent.put("total_docs", searchService.getChunkCount());
        writer.write("data: " + toJson(searchEvent) + "\n\n");
        writer.flush();

        if (chunks.isEmpty()) {
            writer.write("data: {\"type\":\"done\"}\n\n");
            writer.flush();
            return;
        }

        // 3. 构建 Prompt
        List<Map<String, String>> messages = buildMessages(query, chunks, history);

        // 4. 流式 LLM 回答
        deepSeekService.streamChat(
                messages,
                deepseekKey,
                token -> {
                    try {
                        writer.write("data: {\"type\":\"token\",\"content\":" +
                                toJson(token) + "}\n\n");
                        writer.flush();
                    } catch (Exception ignored) { /* 客户端断开 */ }
                },
                error -> {
                    try {
                        writer.write("data: {\"type\":\"error\",\"content\":" +
                                toJson(error) + "}\n\n");
                        writer.flush();
                    } catch (Exception ignored) { /* ignore */ }
                }
        );

        writer.write("data: {\"type\":\"done\"}\n\n");
        writer.flush();
    }

    // ═══════════════════════════════════════════
    // 辅助方法
    // ═══════════════════════════════════════════

    private List<Map<String, String>> buildMessages(String query,
                                                     List<Map<String, Object>> chunks,
                                                     List<Map<String, String>> history) {
        // 构建上下文
        StringBuilder ctx = new StringBuilder();
        for (int i = 0; i < chunks.size(); i++) {
            Map<String, Object> c = chunks.get(i);
            ctx.append("[文档").append(i + 1).append("] 来源: ")
               .append(c.get("source")).append("\n")
               .append(c.get("text")).append("\n\n---\n\n");
        }

        String system = "你是 MyBatis 技术文档助手。" +
                "请严格基于下面提供的文档内容回答问题。" +
                "如果文档中没有相关信息，请明确说「文档中未找到相关内容」，不要编造。" +
                "回答时在关键信息后标注引用来源，格式为 [文档N]。" +
                "回答用中文，保留原始代码格式。";

        List<Map<String, String>> messages = new ArrayList<>();
        messages.add(Map.of("role", "system", "content", system));

        // 最近 6 轮历史
        int start = Math.max(0, history.size() - 6);
        for (int i = start; i < history.size(); i++) {
            messages.add(history.get(i));
        }

        messages.add(Map.of("role", "user", "content",
                "=== 检索到的 MyBatis 文档 ===\n" + ctx +
                "=== 文档结束 ===\n\n用户问题: " + query));

        return messages;
    }

    private String toJson(Object obj) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(obj);
        } catch (Exception e) {
            return "\"\"";
        }
    }
}
