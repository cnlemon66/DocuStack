package com.mybatis.rag.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Consumer;

/**
 * 调用 DeepSeek Chat API，SSE 流式返回
 */
@Service
public class DeepSeekService {

    private final ObjectMapper mapper = new ObjectMapper();

    /**
     * 流式调用 DeepSeek，每收到一个 token 回调 consumer
     */
    public void streamChat(List<Map<String, String>> messages, String apiKey,
                           Consumer<String> onToken, Consumer<String> onError) {
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("model", "deepseek-chat");
            body.put("messages", messages);
            body.put("temperature", 0.3);
            body.put("stream", true);

            URL url = new URL("https://api.deepseek.com/chat/completions");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Authorization", "Bearer " + apiKey);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(120000);

            // 发送请求体
            try (OutputStream os = conn.getOutputStream()) {
                os.write(mapper.writeValueAsBytes(body));
            }

            // 读取 SSE 流
            int status = conn.getResponseCode();
            if (status != 200) {
                onError.accept("DeepSeek API 返回 " + status);
                return;
            }

            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    if (line.isEmpty()) continue;
                    if (!line.startsWith("data: ")) continue;
                    String data = line.substring(6);
                    if ("[DONE]".equals(data.trim())) break;

                    try {
                        Map<String, Object> obj = mapper.readValue(data,
                                new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {});
                        List<Map<String, Object>> choices = (List<Map<String, Object>>) obj.get("choices");
                        if (choices != null && !choices.isEmpty()) {
                            Map<String, Object> delta = (Map<String, Object>) choices.get(0).get("delta");
                            if (delta != null) {
                                Object content = delta.get("content");
                                if (content != null && !content.toString().isEmpty()) {
                                    onToken.accept(content.toString());
                                }
                            }
                        }
                    } catch (Exception e) {
                        // 跳过解析失败的行
                    }
                }
            }
        } catch (Exception e) {
            onError.accept(e.getMessage());
        }
    }
}
