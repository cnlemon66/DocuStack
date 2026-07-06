# MyBatis RAG 开发日志

## 2026-06-23

### 项目启动
- 确定方向：MyBatis 官方中文文档作为知识库，做定向问答 RAG
- 选 Python 标准库（零 pip 依赖），避开沙箱权限问题

### 爬虫 (crawl.py)
- 爬取 MyBatis 3 中文文档 7 页 + MyBatis-Spring 7 页，共 14 篇，13.7 万字
- 纯标准库：urllib + html.parser
- 问题：URL 路径 /zh/ 应该是 /zh_CN/

### 切块 + 索引 (index.py)
- 切块规则：按段落 → 按行 → 硬切 500 字 + 100 字重叠，最小 30 字
- TF-IDF 模式：字 + 双字组合，余弦相似度
- 产出：352 个文本块，index.json

### CLI 问答 (ask.py v1)
- 基础交互：input → 检索 → LLM → print
- 用户反馈：「笨笨的，没有搜索过程」

### CLI 问答 v2
- 检索过程可视化：搜索耗时、命中条数、逐条得分（绿/黄/灰）
- 多轮对话支持（/clear、/stats、/detail）
- 用户反馈：想用 Web UI

### Web 界面 (index.html v1)
- Python http.server 后端 (server.py)，端口 8765
- SSE 流式回答
- 蓝紫配色自设计，被用户否定

### Web 界面 v2 —— HanaAgent 主题
- 直接引用 HanaAgent 官方 CSS 变量和色值
- 三栏布局：侧边栏 / 聊天区 / 检索面板
- 主题系统：新暖纸、暖纸、珊瑚、青夜、青夜·HC
- 设置对话框弹窗

### 交互优化
- 检索摘要块（可折叠，最多显示 5 条）
- 「检索完成」与「📝 回答」分隔
- Markdown 渲染：**粗体**、`代码`、```代码块```、无序列表、[文档N]
- 修复：SSE 缓冲重放 bug、发送按钮锁死 bug（finally 兜底）

### B1: Java Spring Boot 重写
- Spring Boot 2.7 + Java 11，端口 8766
- 4 个 Java 文件：RagApplication / RagController / SearchService / DeepSeekService
- 与 Python 版共享 data/ 和 config.json
- 结论：保留作为对照学习材料，主力仍用 Python 版

### A1: Embedding 模型升级
- 多 Provider 支持：硅基流动 BAAI/bge-m3 / OpenAI / 智谱
- 自动检测环境变量或 config.json
- 硅基流动 Key 已就绪，待运行 `python index.py` 切换

### C: 多 Agent 拆分 ✓
- 三个 Worker Agent 插入 /api/chat 流程：
  1. **QueryRewriter**：复杂问题拆原子查询（对比/多步/多主题），简单问题原样通过
  2. **Searcher**：多路检索 + 去重合并（前 80 字去重），最多 8 条给 Reviewer
  3. **Reviewer**：LLM 审查每条结果是否真能回答问题，过滤跑题块
- 每个 Worker 有独立 System Prompt + LLM 调用
- 前端：显示子查询拆分信息、Worker 状态（⚙ 查询重写 / 审查过滤）+ 耗时

### 耗时追踪 + Token 统计 ✓
- 底部统计栏：重写 Xms · 检索 Xms · 审查 Xms · 生成 Xms · 总计 Xms
- Token 估算（输入 + 输出），每轮对话累积
- 上下文轮数显示：上下文 3/6 轮
- 上下文上限：滑动窗口 6 轮（build_messages 中 history[-6:]）

### Bug 修复
- JS 语法错误：finally 块缺少闭合括号，整个页面 JS 不执行
- 服务端连接断开崩溃：SSE 写入加 try-except 保护
- 缩进错误：/api/chat 分支整体重写，统一缩进
- Embedding 索引无 Key 时检索崩溃：改回返回空结果而非抛异常
- **多轮对话卡死**：详见 2026-06-24 Bug 追踪

---

## 2026-06-24

### A2: 补文档 + 改进切块策略 ✓（2026-06-24 15:00~16:00 ≈1h）

**补文档（crawl.py）**
- 新增 MyBatis 3 简介页（mb_index）
- 新增 MyBatis-Plus 官方中文文档 9 篇（baomidou.com）：
  - 简介、快速开始、持久层接口（CRUD）、条件构造器（Wrapper）
  - 使用配置、注解、代码生成器配置、插件主体、常见问题
- 知识库扩展：14 篇 → 24 篇，13.7 万字 → 31.7 万字

**改进切块策略（index.py）**
- 块大小：500 → 800，重叠：100 → 150
- 新增代码块保护：```...``` 不拆散，10 行以内代码块整体保留
- 新增标题感知切分：按 ## / ### 切分，标题保留为块前缀
- 新增句子边界切分：超长行先在。！？处断开，实在不行才硬切
- 相邻块尾部重叠：100 字
- 产出：352 块 → 480 块

**切块对比**
```
A2 前：段落切分 → ≤500字成块 → 超长行拼 → 硬切(100重叠)
A2 后：代码块保护 → 标题切分 → 段落切分 → 句子边界切 → 硬切(150重叠, 100尾重叠)
```

---

### Bug 追踪：多轮对话卡死（耗时 ≈2h）

**症状**
- 第一轮问答正常，答完后发送按钮变灰不恢复
- 上下文轮数始终显示 0/6（说明 `H.push()` 未执行）
- 浏览器网络面板无报错，服务端日志无异常

**根因**
Python `http.server` 对 HTTP/1.1 请求默认连接复用（`close_connection=False`）。
SSE 流式响应无 Content-Length，浏览器 `ReadableStream` 只能靠 TCP 断开感知流结束。
服务端发完 `done` 事件后 TCP 连接不关闭 → 前端 `rd.read()` 永远收不到 `{done:true}` → while 死循环 → finally 不执行。

```
服务端: done 事件已写入 → do_POST 返回 → TCP 保持 ESTABLISHED
浏览器: ReadableStream → rd.read() → 永不返回 done → while(!done) 死等
前端: H.push() 不执行 → finally 不执行 → B 永为 true → 按钮永灰
```

**修复**
`send_sse()` 末尾加一行：
```python
self.close_connection = True  # 强制关闭 TCP，信号化 SSE 流结束
```
配套加固（防御性）：
- QueryRewriter / Reviewer LLM 调用失败降级为原样查询，不阻断流程
- 前端 `AbortController` 60s 看门狗，超时自动恢复

**排查踩坑**
- 改完代码验证时发现不生效 → 两台 `python server.py` 进程同时抢 8765 端口，浏览器连到旧进程
- Worker 异常静默吞掉（`except: return` / `except: pass`），SSE 已开但没发 done/error 事件

### 会话层：三层记忆 ✓（2026-06-24 17:00~18:30 ≈1.5h）

**热/冷记忆 + 异步压缩**

- 预算：250K tokens（DeepSeek 1M 的 25%）
  - 热记忆（原文）：55% ≈ 137K，保留最近 N 轮完整对话
  - 冷记忆（摘要）：20% ≈ 50K，旧对话压缩为结构化摘要
  - 弹性缓冲：25% ≈ 60K
- 触发：总 token > 预算 75% 时，保留最近 5 轮原文，其余压缩
- 异步：后台线程执行 LLM 压缩，不阻塞当前响应
- 冷记忆超限时二次压缩（摘要的摘要）

**压缩格式**
```
[早期对话摘要]
[主题] - 用户关心的技术问题
[结论] - 助手给出的关键方案
[概念] - 涉及的技术名词
```

**前端适配**
- 自动生成 session_id（localStorage 持久化）
- 新对话重置 session_id
- 统计栏：热记忆 N 轮 + 冷记忆 激活状态

**代码改动**
- 新增 `count_tokens()` / `count_history_tokens()` / `compress_history()` / `maybe_compress()`
- `build_messages()` 接受冷记忆参数，热记忆按 token 预算动态保留
- Handler._sessions 管理多会话冷记忆
- /api/chat done 事件返回 hot_rounds / cold_active

### UI 微调
- 字体栈：`Inter` → `system-ui` + `Noto Sans SC`，字号 14→15px
- 设置对话框：厂商→模型二级选择，新增大模型预设（含 MiMo / Token Plan）
- 测试连接按钮 + 中文错误提示
- 保存与重建索引分离，换模型无需重建

### 安全层（基础版）✓

### 规划层（第一阶段）✓（2026-06-24 20:00~20:30 ≈0.5h）

**意图分类 + 计划生成**

- 新增 Planner Worker，在 QueryRewriter 之前运行
- 五种意图：simple（直接检索）、compare（对比分析）、multi_step（分步解决）、debug（排查错误）、off_topic（拒绝）
- LLM 输出 JSON 计划：包含 steps（检索目标+关键词）、difficulty、synthesis_hint
- 执行策略：
  - simple → 走原来的 QueryRewriter 多路检索
  - compare/multi_step/debug → 按计划步骤顺序检索，去重合并
  - off_topic → 直接拒绝，不消耗检索资源
- 每个检索结果标注所属步骤，注入 synthesis_hint 到 System Prompt
- 前端：显示意图类型、难度、步骤列表

### 工具层（联网搜索 + 文件操作）✓

### 调度层：Boss Agent ✓

### 跨会话持久化 ✓
- 对话存盘：`data/conversations/{id}.json`，自动保存每轮后
- 侧边栏对话列表：按时间倒序，显示标题+消息数，点击切换
- 首轮问题自动做标题（截取前 30 字）
- API：GET/POST/DELETE /api/conversations
- 新增 Boss 动态调度器（`boss_dispatch`）
- 流程：分析当前状态 → 选择工具 → 执行 → 评估结果 → 循环（最多 5 步）
- 三种动作：continue（继续搜）、answer（信息够了直接回答）、clarify（追问用户）
- 复杂问题（难度≥3）自动启用 Boss，简单问题走原静态路径
- 每步显示思考原因，前端实时反馈调度过程
- 新增 `tools.py`：DuckDuckGo 联网搜索、文件读写/列目录
- 路径安全：禁止越权 WORKSPACE
- 工具注册表 + Planner 支持 tool 字段按步骤分发
- 前端显示工具图标（📖 🌐 📄 📁）

---

## 2026-06-25

### UI：推荐问题卡片样式 + 时间戳自持
- `makeRecsClickable` 重写：推荐问题区域改为卡片容器（底色+边框），每条问题是可点击的形状色块，悬停变色
- `<ul>` 无间隙，`<li>` 之间不空行
- 时间戳+Token 写入消息本体（`full`），历史加载后仍能看到
- `loadConv` 恢复推荐问题可点击

### 编排层：规划+调度+工人三合一 → PlannerOrchestrator ✓
- 旧架构：4 次 LLM 调用（分类→Boss→评审→回答），server.py 200 行路由
- 新架构：1 次 LLM 调用（编排）→ 并行执行 → 回答，server.py 40 行路由
- **步骤间依赖**：plan JSON 增加 `depends_on` 字段，拓扑排序分层执行（同层并行，高层等低层）
- **结果验证**：空结果或 <2 条时自动降级搜索（拼接步骤关键词），设置 `fallback` 标记
- **简单问题本地规则**：零 LLM 调用，regex 匹配直接搜
- 代码：server.py 660→500 行，agents.py 339→265 行

### 工具层扩展：5→7 个工具 ✓
- `shell_execute`：subprocess.run，黑名单拦截（rm/format/sudo/|/`$等），15s 超时，路径越权防护
- `db_query`：SQLite SELECT 只读，拦截非 SELECT/多条语句，格式化输出表格
- `ORCH_SYSTEM` 可用工具列表同步更新
- 工具总数 7 → MCP 门槛（≥6）已满足

### UI：中断对话 + 双击编辑消息 ✓
- 发送按钮旁增加停止按钮（coral 色），流式生成时可见，点击触发 AbortController.abort()
- 生成中止友好提示：⏹ 已停止生成
- 用户消息双击即可编辑：清空之后的消息，原文填入输入框，修改后重新发送

### 编排层：步骤间数据流依赖 ✓
- `_exec_step(s, step_idx)` 增加 step_results dict 支持
- 步骤有 depends_on 时，从 step_results 取上一步返回文本的前 300 字注入 query
- 上一步空结果时自动降级到 `fallback_query`（LLM 在 plan JSON 里预填的备选查询）
- 三层防线：步骤级 fallback → 全局降级宽搜 → Answer LLM "找不到就说没找到"
- ORCH_SYSTEM 步骤 schema 增加 `fallback_query` 字段

### 混合检索：Embedding + TF-IDF 重排序 ✓
- Embedding 语义召回 top 20 → TF-IDF 对 top 20 重排序 → 加权合并（0.7×嵌入 + 0.3×关键词）
- `index.py` 始终计算 TF-IDF 向量存入 `tfidf_vectors` 字段
- 域路由后同样生效：`tfidf_v` 随 embeddings 一起过滤
- 效果：语义相关 + 关键词精确，避免纯 Embedding 把同义词误拉进来

### 冷记忆压缩增强 ✓
- 新增 `[关键参数]` 输出段：必须保留原数值（如 maxActive=20）
- 显式禁止概括为"配置了连接池"
- 格式从三部分扩为四部分：主题 / 关键参数 / 结论 / 概念

### MCP 协议接口 ✓
- 新建 `mcp.py`，纯标准库实现 JSON-RPC 2.0 over HTTP
- 单端点 `/mcp`，支持 `initialize` / `tools/list` / `tools/call` 三个核心方法
- 工具 schema 自动转换为 MCP inputSchema 格式
- 任何 MCP 兼容客户端（Claude Desktop、Cursor、ChatGPT）可直接发现并调用 7 个工具
- 97 行代码，零依赖

### 知识库扩展：MySQL + Redis ✓
- **MySQL 8.0 官方中文文档**选爬：优化(8)/数据类型(11)/SQL语句(13)/InnoDB(15) + 备份恢复/函数/存储对象/服务器管理
- 来源：mysql.net.cn，8 章 451 页，273 万字
- **Redis 中文文档**：redis.com.cn，核心主题 33 页 + 63 条常用命令
- 87 页，46 万字；含数据类型/持久化/事务/复制/哨兵/集群/分布式锁
- 文件总数：24 → 562（+538），索引：480 → 4231 块
- 当前为 TF-IDF 模式（键），后续可补 Embedding Key 提升检索质量

### 域路由（分层检索）✓
- 给 `search()` 加 `domain` 参数（mybatis/mysql/redis），按文件名前缀过滤 meta
- `ORCH_SYSTEM` 增加 `domain` 字段，LLM 生成步骤时指定搜索域
- 本地简单查询做关键词域推断（_detect_local_domain），2 倍权重以上才锁定域
- 前端 plan 事件显示当前 domain
- 5800 块搜索范围缩小 3~5 倍，跨域噪声大大减少

#### 切块规则 vs 域路由 vs 数据流依赖 对比
| | 切块规则（A2） | 域路由 | 数据流依赖 |
|------|--------------|--------|----------|
| 解决什么 | 跨段信息不完整 | 跨域检索混杂 | 步骤间信息没有传递 |
| 作用层面 | 建索引时 | 检索时 | 步骤执行时 |
| 方法 | 代码块保护→标题切→句子边界 | 前缀映射→域过滤→域内搜 | depends_on + step_results 注入 + fallback_query 降级 |
| 效果 | 同一段不腰斩 | 搜 Redis 不命中 MySQL | 步骤 1 能用步骤 0 的结果，上一步空了也能降到独立查询 |

---

## 当前项目状态

```
索引：4231 块 × TF-IDF（字双字组合）
LLM：DeepSeek Chat SSE 流式
架构：PlannerOrchestrator → 并行工具执行 → 流式回答
会话：三层记忆（冷/热）+ 异步压缩，250K token 预算
前端：HanaAgent 主题，三栏 Web 界面，耗时+Token+热记忆统计
后端：Python http.server（主力）+ Spring Boot（对照）
知识库：562 篇（MyBatis 24 + MySQL 451 + Redis 87），约 370 万字
```

### 新架构实现度（合并规划/调度/工人 → 编排层）

| 层 | 完成度 | 说明 |
|------|------|------|
| UI 层 | **85%** | Web 界面、流式、主题、耗时统计、热记忆计数、推荐问题、中断按钮、双击编辑消息 |
| 会话层 | 85% | 三层记忆+异步压缩+跨会话持久化+多对话列表 |
| **编排层** | **85%** | **PlannerOrchestrator：意图分类+依赖排序+分层并行执行+结果验证+空结果降级搜索** |
| 工具层 | **90%** | 7 个工具（检索/联网/文件读写/列目录/Shell执行/SQLite查询）；工具注册表；MCP Streamable HTTP 接口 |
| 安全层 | 40% | 输入校验+prompt注入检测+Key脱敏；缺审计日志 |

### 旧架构实现度（参考对比）

| 层 | 完成度 | 说明 |
|------|------|------|
| UI 层 | 85% | Web 界面、流式、主题、耗时统计、热记忆计数、推荐问题、中断按钮、双击编辑消息 |
| 会话层 | 85% | 三层记忆+异步压缩+跨会话持久化+多对话列表 |
| ~~规划层~~ | ~~40%~~ | 已合并进编排层 |
| ~~调度层~~ | ~~40%~~ | 已合并进编排层 |
| ~~工人层~~ | ~~70%~~ | 已合并进编排层 |
| 工具层 | 90% | 7 个工具：检索/联网/文件读写/列目录/Shell执行/SQLite查询；工具注册表；MCP Streamable HTTP 接口 |
| 安全层 | 40% | 输入校验+prompt注入检测+Key脱敏；缺审计日志 |

## 待办

### 已完成
- ~~A2：补文档 + 改进切块策略~~ ✓
- ~~会话层：摘要压缩 + 异步压缩~~ ✓
- ~~多轮对话卡死（TCP 连接不关闭）~~ ✓
- ~~LLM Provider 多厂商 + 测试连接~~ ✓
- ~~UI：字体、设置对话框、统计栏~~ ✓
- ~~安全层：输入校验 + 输出过滤~~ ✓
- ~~规划层 + 调度层 + 工人层 → 编排层~~ ✓
- ~~编排层完善：步骤间依赖 + 结果验证（65% → 85%）~~ ✓
- ~~跨会话持久化：冷记忆存盘、历史对话列表~~ ✓
- ~~UI：字体、设置对话框、统计栏、推荐问题、时间戳自持~~ ✓
- ~~工具层扩展：shell_execute + db_query（5→7 个工具）~~ ✓
- ~~知识库扩展：MySQL 选爬 + Redis 核心（24→562 页，480→4231 块）~~ ✓
- ~~UI：中断对话 + 双击编辑消息~~ ✓
- ~~MCP 协议接口 ~~ ✓

### 待推进（按优先级）
1. 补 Embedding Key 并重建索引 → 启用混合检索 + 语义缓存
2. LLM 路由（difficulty 分级调不同模型，省钱不降体验）

---

## 技术要点

### 切块规则（A2 改进版）
```
代码块保护 → 标题切分 → 段落切分 → 句子边界切 → 硬切(150字重叠) → 尾重叠100字 → 过滤<30字
```

### TF-IDF vs Embedding
| | TF-IDF | Embedding (bge-m3) |
|------|--------|-----|
| 原理 | 字面匹配 | 语义匹配 |
| 维度 | 稀疏字典 | 1024 维稠密 |
| 「怎么连数据库」| 0.15 分 | 预计 0.7+ |

### API 端点 (server.py)
```
GET  /              → index.html
GET  /api/config    → 配置状态
POST /api/config    → 保存 API Key
POST /api/search    → 检索
POST /api/chat      → SSE 流式回答
POST /api/reindex   → 在线重建索引
```
