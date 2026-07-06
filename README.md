# DocuStack

面向 Java 后端开发者的智能文档问答系统。自研七层 Agent 架构，支持 MyBatis / MySQL / Redis 三大知识库，562 篇文档，370 万字。

**本地一键启动，零依赖。** 纯 Python 标准库，不装任何 pip 包。

## 特性

- **PlannerOrchestrator 编排层** — 意图分类 + 步骤间拓扑排序 + 分层并行执行 + 数据流依赖
- **混合检索** — Embedding 语义召回 top20 → TF-IDF 关键词重排序
- **域路由** — 按知识域自动过滤（MyBatis / MySQL / Redis），跨域噪声降低 3~5 倍
- **三层会话记忆** — 热记忆 + 冷记忆（异步压缩） + 250K Token 预算
- **7 个内置工具** — 知识库检索、联网搜索、文件读写、Shell 执行、SQLite 查询
- **MCP 协议接口** — 任何 MCP 兼容客户端（Claude Desktop / Cursor）可直接调你的工具
- **流式 SSE 输出 + 中断对话 + 双击编辑消息 + 跨会话持久化**

## 快速开始

### 1. 配置 API Key

```bash
cp config.example.json config.json
# 编辑 config.json，填入你的 DeepSeek API Key
```

### 2. 启动服务

```bash
python server.py
```

打开 `http://localhost:8765`

### 3. （可选）构建向量索引

如果你有硅基流动 API Key，可以构建 Embedding 索引以提升检索质量：

```bash
export SILICONFLOW_API_KEY=sk-你的key
python index.py
```

没有 Key 也可运行，系统自动降级为 TF-IDF 字面检索。

## 知识库

| 知识域 | 来源 | 文档数 | 字数 |
|--------|------|--------|------|
| MyBatis | mybatis.org 官方中文 | 24 篇 | 31 万 |
| MySQL 8.0 | mysql.net.cn 官方中文 | 451 篇 | 273 万 |
| Redis | redis.com.cn 社区中文 | 87 篇 | 46 万 |

如需扩展知识库，运行对应爬虫：

```bash
python crawl_mysql.py    # MySQL 8.0 选爬
python crawl_redis.py    # Redis 中文
python index.py          # 重建索引
```

## MCP 接口

你的 7 个工具已暴露为 MCP Streamable HTTP 接口。在任何支持 MCP 的客户端中配置：

```json
{
  "mcpServers": {
    "mybatis-rag": {
      "type": "streamableHttp",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

之后客户端可直接调 `search_index`、`web_search`、`shell_execute` 等工具。

## 项目结构

```
mybatis-rag/
├── server.py          # HTTP 服务 + /mcp 端点
├── agents.py          # 编排层（PlannerOrchestrator + 会话记忆）
├── core.py            # 检索引擎 + LLM Provider + 安全层
├── tools.py           # 7 个工具 + 工具注册表
├── mcp.py             # MCP JSON-RPC 接口（97 行）
├── index.py           # 文档切块 + 索引构建
├── index.html         # Web UI（HanaAgent 主题）
├── crawl_mysql.py     # MySQL 爬虫
├── crawl_redis.py     # Redis 爬虫
├── data/raw/          # 562 篇原始文档（已提交）
├── data/vector_db/    # 向量索引（构建产物，需自行运行 index.py）
├── java-backend/      # Spring Boot 对照版本
└── DEVLOG.md          # 开发日志
```

## License

MIT
