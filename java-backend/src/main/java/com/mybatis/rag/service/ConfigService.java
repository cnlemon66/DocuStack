package com.mybatis.rag.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.io.File;
import java.io.IOException;
import java.util.Map;

/**
 * 管理 config.json，读写 API Key
 */
@Service
public class ConfigService {

    @Value("${rag.data-dir:../data}")
    private String dataDir;

    private File configFile;
    private final ObjectMapper mapper = new ObjectMapper();

    @PostConstruct
    public void init() {
        // config.json 放在 dataDir 的上一级（即项目根目录）
        configFile = new File(dataDir).getParentFile().toPath().resolve("config.json").toFile();
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> load() {
        try {
            return mapper.readValue(configFile, Map.class);
        } catch (IOException e) {
            return Map.of("deepseek_api_key", "", "openai_api_key", "");
        }
    }

    public void save(String deepseekKey, String openaiKey) {
        try {
            Map<String, Object> cfg = load();
            if (deepseekKey != null && !deepseekKey.isBlank())
                cfg.put("deepseek_api_key", deepseekKey);
            if (openaiKey != null && !openaiKey.isBlank())
                cfg.put("openai_api_key", openaiKey);
            mapper.writerWithDefaultPrettyPrinter().writeValue(configFile, cfg);
        } catch (IOException e) {
            throw new RuntimeException("保存配置失败", e);
        }
    }

    public String getDeepseekKey() {
        Object key = load().get("deepseek_api_key");
        return key != null ? key.toString() : "";
    }

    public String getOpenaiKey() {
        Object key = load().get("openai_api_key");
        return key != null ? key.toString() : "";
    }
}
