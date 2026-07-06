package com.mybatis.rag.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.io.File;
import java.io.IOException;
import java.util.*;

/**
 * 文档检索服务：加载索引，执行 TF-IDF / Embedding 检索
 * 对应 Python 版 index.py 的检索逻辑
 */
@Service
public class SearchService {

    @Value("${rag.data-dir:../data}")
    private String dataDir;

    private List<String> chunks;
    private List<Map<String, Object>> metas;
    private List<Object> embeddings;
    private String embeddingType;  // "tfidf" 或 "openai"

    private final ObjectMapper mapper = new ObjectMapper();

    @PostConstruct
    public void init() throws IOException {
        File indexFile = new File(dataDir, "vector_db/index.json");
        if (!indexFile.exists()) {
            chunks = List.of();
            metas = List.of();
            embeddings = List.of();
            embeddingType = "tfidf";
            return;
        }

        Map<String, Object> idx = mapper.readValue(indexFile,
                new TypeReference<Map<String, Object>>() {});

        this.chunks = (List<String>) idx.get("chunks");
        this.metas = (List<Map<String, Object>>) idx.get("meta");
        this.embeddings = (List<Object>) idx.getOrDefault("embeddings", List.of());
        this.embeddingType = (String) idx.getOrDefault("embedding_type", "tfidf");

        System.out.println("索引加载: " + chunks.size() + " 块 | " + embeddingType);
    }

    /**
     * 检索 Top-K 个最相关的文档块
     */
    public List<Map<String, Object>> search(String query, int topK) {
        if (chunks.isEmpty()) return List.of();

        long t0 = System.currentTimeMillis();

        // 查询向量化
        Object queryVec;
        if ("openai".equals(embeddingType)) {
            // TODO: 调用 OpenAI Embedding API
            queryVec = tokenize(query);  // 暂时退化为 TF-IDF
        } else {
            queryVec = tokenize(query);
        }

        // 打分排序
        List<ScoreEntry> scored = new ArrayList<>();
        for (int i = 0; i < chunks.size(); i++) {
            double score;
            if ("openai".equals(embeddingType) && i < embeddings.size()
                    && embeddings.get(i) instanceof List) {
                score = cosineSim((List<Double>) queryVec, (List<Double>) embeddings.get(i));
            } else if (embeddings.get(i) instanceof Map) {
                score = tfidfSim((Map<String, Double>) queryVec,
                        (Map<String, Double>) embeddings.get(i));
            } else {
                score = 0;
            }
            if (score > 0) scored.add(new ScoreEntry(score, i));
        }

        scored.sort((a, b) -> Double.compare(b.score, a.score));

        // 组装结果
        long elapsed = System.currentTimeMillis() - t0;
        List<Map<String, Object>> results = new ArrayList<>();
        for (int i = 0; i < Math.min(topK, scored.size()); i++) {
            ScoreEntry se = scored.get(i);
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("text", chunks.get(se.index));
            item.put("source", metas.get(se.index).get("source"));
            item.put("score", Math.round(se.score * 10000.0) / 10000.0);
            results.add(item);
        }
        return results;
    }

    public int getChunkCount() { return chunks.size(); }
    public String getEmbeddingType() { return embeddingType; }

    // ═══════════════════════════════════════════
    // TF-IDF（与 Python 版 tokenize + tfidf_sim 一致）
    // ═══════════════════════════════════════════

    private Map<String, Double> tokenize(String text) {
        Map<String, Double> vec = new HashMap<>();
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (!Character.isWhitespace(ch)) {
                String t = String.valueOf(ch);
                vec.merge(t, 1.0, Double::sum);
                if (i < text.length() - 1) {
                    String bigram = ch + String.valueOf(text.charAt(i + 1));
                    vec.merge(bigram, 1.0, Double::sum);
                }
            }
        }
        return vec;
    }

    private double tfidfSim(Map<String, Double> qv, Map<String, Double> dv) {
        double dot = 0;
        for (Map.Entry<String, Double> e : qv.entrySet()) {
            dot += e.getValue() * dv.getOrDefault(e.getKey(), 0.0);
        }
        double nq = Math.sqrt(qv.values().stream().mapToDouble(v -> v * v).sum());
        double nd = Math.sqrt(dv.values().stream().mapToDouble(v -> v * v).sum());
        return (nq == 0 || nd == 0) ? 0 : dot / (nq * nd);
    }

    private double cosineSim(List<Double> a, List<Double> b) {
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < Math.min(a.size(), b.size()); i++) {
            dot += a.get(i) * b.get(i);
            na += a.get(i) * a.get(i);
            nb += b.get(i) * b.get(i);
        }
        return (na == 0 || nb == 0) ? 0 : dot / (Math.sqrt(na) * Math.sqrt(nb));
    }

    private static class ScoreEntry {
        double score;
        int index;
        ScoreEntry(double s, int i) { this.score = s; this.index = i; }
    }
}
