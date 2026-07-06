# MyBatis RAG 项目路线图

## 当前状态
- 知识库：14 篇 MyBatis + MyBatis-Spring 中文文档，352 个 TF-IDF 文本块
- 后端：Python 标准库 http.server（server.py）
- 前端：纯 HTML/CSS/JS 三栏聊天界面（index.html），HanaAgent 主题系统
- 启动：双击 start.bat，或 `python server.py`，端口 8765

## 下一步 B1：Java Spring Boot 重写后端
- 目标：用 Spring Boot 替代 server.py，保留检索逻辑
- 技术点：@RestController、RestTemplate 调 DeepSeek API、SSE 流式输出
- 保留现有：index.html 前端、data/ 知识库目录

## 之后 A1：切 Embedding 模式
- 需要 OpenAI API Key
- 跑 python index.py，自动用 text-embedding-3-small 重建索引
- 检索质量从字面匹配升级为语义匹配

## 其他可选路线
- B2：RAG 嵌入 game-image-web 项目
- C：多 Agent 拆分（SearchAgent + AnswerAgent）
- D：Java 基础深化（MySQL Explain、Spring Boot 自动配置）
