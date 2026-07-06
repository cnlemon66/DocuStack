# Agent 全流程开发指南

> 从零构建生产级 AI Agent 的完整方法论，基于实际项目经验总结。
> 适用场景：本地 Agent / API Agent / CLI Agent / 企业内部工具。

---

## 目录

1. [七层架构总览](#1-七层架构总览)
2. [UI 层](#2-ui-层)
3. [会话层](#3-会话层)
4. [规划层](#4-规划层)
5. [调度层](#5-调度层)
6. [工人层](#6-工人层)
7. [工具层](#7-工具层)
8. [安全层](#8-安全层)
9. [技术选型速查](#9-技术选型速查)
10. [开发路线图](#10-开发路线图)
11. [常见陷阱](#11-常见陷阱)

---

## 1. 七层架构总览

```
┌──────────────────────────────────────────────────┐
│  1. UI 层       │ 终端 / Web / IDE 插件 / API    │
├──────────────────────────────────────────────────┤
│  2. 会话层      │ 窗口管理 / 记忆系统 / 多轮对话  │
├──────────────────────────────────────────────────┤
│  3. 规划层      │ 任务拆解 / 审批门 / 步骤追踪    │
├──────────────────────────────────────────────────┤
│  4. 调度层      │ Boss Agent：拆活 + 派活 + 汇总  │
├──────────────────────────────────────────────────┤
│  5. 工人层      │ 专职 Worker：搜索 / 审查 / 写码  │
├──────────────────────────────────────────────────┤
│  6. 工具层      │ MCP / 文件 / Shell / API / 检索  │
├──────────────────────────────────────────────────┤
│  7. 安全层      │ 输入护栏 / 沙箱 / 权限 / 审计    │
└──────────────────────────────────────────────────┘
```

**核心原则**：Agent 只是中间两层，上下各两层缺一不可。别让工人直接面对用户，也别让 UI 直接调工具。

---

## 2. UI 层

### 职责

- 接收用户输入
- 渲染 Agent 输出（文本 / 表格 / 代码块 / 图表）
- 管理交互状态（流式输出、中断、历史记录）

### 关键决策

| 决策点 | 选项 | 适用场景 |
|--------|------|---------|
| 交互模式 | 终端 CLI | 开发者工具，轻量快速 |
| | Web 聊天界面 | 对外产品，富文本渲染 |
| | IDE 插件 | 代码助手，嵌入工作流 |
| | API 接口 | 被其他系统调用 |
| 输出方式 | 非流式 | 简单场景，实现容易 |
| | 流式（SSE/WebSocket） | 长回答，体感更好 |

### 实践建议

```
✅ 流式输出是标配——用户不想等 5 秒才看到第一个字
✅ 代码/表格单独渲染，别塞在纯文本里
✅ 支持中断：用户按 Ctrl+C 或点停止按钮，Agent 要能优雅退出
✅ 历史记录可搜索、可复用
❌ 别在 UI 层做业务逻辑
```

---

## 3. 会话层

### 职责

- 上下文窗口管理（消息裁剪 / 摘要压缩）
- 跨会话记忆（用户偏好 / 项目知识 / 上次对话结论）
- 多轮对话状态维护

### 上下文窗口管理

| 策略 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| 滑动窗口 | 只保留最近 N 条消息 | 简单 | 丢失早期上下文 |
| 摘要压缩 | 旧消息让 LLM 压缩成摘要 | 保留关键信息 | 细节丢失 |
| Token 感知裁剪 | 实时算 token 数，超了就裁 | 精确 | 需要 tokenizer |

**推荐**：摘要 + 滑动窗口混合。总字符超阈值时，保留最近 4-6 条消息，其余压缩成 200 字摘要。

```python
# 伪代码
if count_chars(messages) > MAX_CONTEXT_CHARS:
    messages = compress_context(messages)
    # system + 摘要 + user + 最近 4 条
```

### 记忆系统

```
会话内记忆：   弱（依赖上下文窗口）
跨会话记忆：   强（持久化存储，下次对话加载）
```

| 记忆类型 | 存储 | 示例 |
|----------|------|------|
| 用户偏好 | 持久化 | "用中文回答"、"代码风格用双引号" |
| 项目知识 | 持久化 | "这个项目 API 地址是 xxx" |
| 对话摘要 | 持久化 | 上次聊到一半的结论 |
| 临时上下文 | 会话内 | 当前问题的检索结果 |

### 实践建议

```
✅ 窗口管理是强制要求——Agent 跑 20 轮你不压，模型开始忘 System Prompt
✅ 跨会话记忆用简单的文件/Markdown 存储，别上来就接向量库
✅ 记忆要有过期机制——旧偏好可能已被用户推翻
```

---

## 4. 规划层

### 职责

- 复杂任务拆成步骤
- 用户审批（危险操作前确认）
- 步骤执行追踪与回滚

### 为什么需要规划层

```
无规划：
  用户："重构整个支付模块"
  Agent："好的" → 直接改代码 → 改错了 → 炸了

有规划：
  用户："重构整个支付模块"
  Agent："我会做以下修改：
        1. 拆分 order.py 为 3 个子模块
        2. 抽离公共接口
        3. 更新 import 路径
        确认吗？"
  用户审批 → 逐步执行 → 每步 checkpoint → 出错可回滚
```

### 关键设计

| 要素 | 说明 |
|------|------|
| 审批门 | 改文件 / 执行命令 / 调外部 API 前，让用户确认 |
| 步骤追踪 | 每个步骤完成状态（pending / doing / done / failed） |
| 失败回滚 | 步骤失败时，不影响已完成步骤，或支持撤销 |

### 实践建议

```
✅ 不要让 Agent 自己决定"重构哪个文件"——先出方案，人审批
✅ 步骤粒度适中：一个步骤 = 改 1-3 个文件或跑 1 个命令
✅ 规划层是可选的——简单问答不需要它，复杂任务才用
```

---

## 5. 调度层（Boss Agent）

### 职责

- 分析用户问题，拆成子任务
- 派发给对应 Worker Agent
- 汇总 Worker 结果，生成最终输出

### Boss Agent 的工具

```
Boss 不干具体活，它的"工具"是其他 Agent：

  call_search_agent  → 派搜索工
  call_code_agent    → 派代码工
  call_review_agent  → 派审查工
  call_writer_agent  → 派写手
```

### 拆 Worker 的原则

```
✅ 一个 Worker 只干一类活，配最少工具
✅ 单 Worker 的 System Prompt 超过 2000 字就该拆
✅ Worker 之间不直接通信，都通过 Boss 中转
✅ 无依赖的 Worker 并行跑
```

### 实践建议

```
✅ Boss 的 System Prompt 只写"怎么拆任务"，不写"怎么干活"
✅ Boss 的 tool_choice 设 auto——简单问题自己答，不用调 Worker
✅ Worker 输出先过一遍再给用户——Boss 是质量把关人
```

---

## 6. 工人层（Worker Agent）

### 职责

- 接收 Boss 分配的子任务
- 调自己的工具集完成工作
- 返回结果给 Boss

### Worker 类型参考

| Worker | 工具集 | System Prompt 要点 |
|--------|--------|-------------------|
| Search | search_kb, web_search | "只搜不答，返回原始内容" |
| Code | read_file, write_file, run_cmd | "只改代码不闲聊" |
| Review | read_file, git_diff | "找真问题，不找格式问题" |
| Writer | 无（只拼答案） | "基于素材写，不编造" |
| Planner | 无（纯推理） | "拆步骤，不执行" |

### 实践建议

```
✅ 每个 Worker 只配 1-3 个工具，多了选择困难
✅ Worker 出错时返回错误信息给 Boss，由 Boss 决定换策略
✅ Worker 的 System Prompt 写清"你是干什么的"和"你不该干什么"
```

---

## 7. 工具层

### 职责

- 给 Agent 提供"手"——真正执行操作
- 通过 MCP 协议标准化接入

### 工具分类

| 类别 | 示例 | 通信方式 |
|------|------|---------|
| 文件系统 | read_file, write_file, list_dir | 本地 stdio |
| 代码执行 | run_shell, run_python | 本地沙箱 stdio |
| 数据查询 | query_db, search_vector | 本地/远程 |
| 外部 API | send_email, create_ticket | HTTP |
| 检索 | RAG 搜索知识库 | 本地 Chroma/FAISS |

### MCP 核心概念

```
MCP = 工具的 USB 协议

没 MCP：
  模型 ←→ 胶水代码 ←→ 工具  （写死，换工具要改代码）

有 MCP：
  模型 ←→ MCP Client ←→ 标准协议 ←→ MCP Server（工具）
```

| 概念 | 说明 |
|------|------|
| Resources | 暴露数据（GET） |
| Tools | 暴露操作（POST） |
| Prompts | 预定义提示词模板 |

### 工具设计原则

```
✅ 一个工具只干一件事——search_docs 好过 do_everything
✅ 描述写好——模型靠 description 判断什么时候调用
✅ 返回值要结构化，包含"是否成功"的标记
✅ 工具出错时返回错误信息而不是抛异常——让 Agent 自己调整策略
✅ 一次别给 20+ 个工具——模型会选错
```

---

## 8. 安全层

### 职责

- 输入校验（防注入、防越界）
- 输出过滤（防泄露）
- 工具执行权限控制
- 审计日志

### 五层防护

```
第 1 层：输入校验
  空输入拦截 / 长度限制 / 范围检查 / 敏感词过滤

第 2 层：模型约束
  System Prompt 里写清行为边界

第 3 层：工具权限
  敏感操作需要用户确认 / 沙箱隔离

第 4 层：输出过滤
  检测 System Prompt 泄露 / 过滤敏感信息

第 5 层：审计日志
  记录每次决策、每次工具调用，可追溯
```

### 安全实践

```
✅ API Key 永远从环境变量读取，不硬编码
✅ 文件操作限制在 sandbox 目录内
✅ Shell 命令黑白名单
✅ 用户确认门：删文件、发邮件、调外部 API 前让人点头
✅ 输出过一遍 guard_output——防模型把 System Prompt 吐出来
```

---

## 9. 技术选型速查

| 层面 | 推荐方案 | 备选 |
|------|---------|------|
| LLM | DeepSeek（便宜+Function Calling） | GPT-4o, Claude 3.5 |
| LLM SDK | openai-python（兼容多厂商） | langchain（重，不推荐新手） |
| 本地模型 | Ollama + qwen2.5:7b | llama.cpp, vLLM |
| Embedding | BAAI/bge-small-zh（中文） | all-MiniLM-L6-v2（英文） |
| 向量库 | Chroma（零配置） | FAISS, Milvus, Qdrant |
| 工具协议 | MCP（标准） | 自定义 Function Calling |
| 记忆 | Markdown 文件 + 摘要压缩 | 向量库做长期记忆 |
| 消息队列 | Redis / RabbitMQ | AIO 多线程 |
| 部署 | Docker + GPU 实例 | Kubernetes |

---

## 10. 开发路线图

### 阶段 0：基础知识（1-2 天）

- [ ] Temperature / Top-P / System Prompt / Function Calling
- [ ] 上下文窗口作用和限制
- [ ] API 调用（OpenAI SDK 基本用法）

### 阶段 1：最小可行 Agent（1 天）

- [ ] 单 Agent 循环骨架（while + tool_calls）
- [ ] 1-2 个工具（搜索 + 回答）
- [ ] 能跑通就行，不追求完美

### 阶段 2：生产加固（2-3 天）

- [ ] LLM 调用加重试（指数退避）
- [ ] 工具执行错误保护
- [ ] 上下文窗口管理（摘要压缩）
- [ ] 自我纠错（搜索结果质量分级）
- [ ] 可观测性（每步 token / 计时 / 费用）
- [ ] 安全护栏（输入校验 + 输出过滤）

### 阶段 3：多 Agent（2-3 天）

- [ ] Boss + Worker 架构
- [ ] 专职 Worker（搜索工、写手工、代码工）
- [ ] 并行调度

### 阶段 4：知识库（1-2 周）

- [ ] Chroma/FAISS 向量库
- [ ] 文档切块 + Embedding
- [ ] RAG 检索 + 生成

### 阶段 5：工具生态（1-2 周）

- [ ] MCP 协议接入
- [ ] 文件工具 / Shell 工具 / 数据库工具
- [ ] 外部 API 对接

### 阶段 6：产品化（持续）

- [ ] 流式输出
- [ ] 跨会话记忆
- [ ] 审批门
- [ ] 评测体系
- [ ] 本地模型部署

---

## 11. 常见陷阱

### 架构陷阱

| 陷阱 | 后果 | 怎么避免 |
|------|------|---------|
| 一个 Agent 挂 20 个工具 | 模型选错工具，输出混乱 | 按职责拆 Worker，每个配 1-3 个工具 |
| System Prompt 写 5000 字 | 实际遵守率极低 | 精简到 500 字以内，分规则优先级 |
| 没有上下文压缩 | 跑 20 轮后模型"失忆" | 超阈值自动压缩 |
| 工具出错直接抛异常 | Agent 崩溃 | 返回错误信息给模型，让它自己换方案 |

### 工程陷阱

| 陷阱 | 后果 | 怎么避免 |
|------|------|---------|
| API Key 硬编码 | 提交到 git，泄漏 | 环境变量 / .env 文件 |
| 不限制重试次数 | 死循环刷爆 API 账单 | 最大 3 次重试 + 轮次上限 |
| 不记录日志 | 出问题不知道哪步错了 | trace 每步 token + 决策 |
| 不给用户审批门 | Agent 删了不该删的文件 | 写操作前确认 |

### 体验陷阱

| 陷阱 | 后果 | 怎么避免 |
|------|------|---------|
| 没有流式输出 | 用户干等 10 秒 | SSE 流式返回 |
| 模型"编造"答案 | 看起来靠谱其实假的 | System Prompt 约束"不知道就说不知道" |
| 搜索结果直接给用户 | 一堆乱码 | Writer Agent 格式化后再输出 |

---

## 附录：最小 Agent 骨架（Python）

```python
import json
from openai import OpenAI

llm = OpenAI(api_key="sk-xxx", base_url="https://api.deepseek.com")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search",
        "description": "搜索知识库",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}]

def agent_ask(question):
    messages = [
        {"role": "system", "content": "你是助手。先搜知识库，再回答。"},
        {"role": "user", "content": question},
    ]

    while True:
        response = llm.chat.completions.create(
            model="deepseek-chat", messages=messages, tools=TOOLS
        )
        msg = response.choices[0].message

        if not msg.tool_calls:          # 信息够了，回答
            return msg.content

        tc = msg.tool_calls[0]          # 执行工具
        result = do_search(json.loads(tc.function.arguments)["query"])
        messages.append({"role": "assistant", "tool_calls": [tc.model_dump()]})
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
```

---

*最后更新：2026 年 6 月*
