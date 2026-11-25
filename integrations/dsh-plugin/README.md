# 把「AI 小说写作平台」接进 DeepSeek Harness（DSH）

> 平台路径：`D:\ai-xiaoshuo`（FastAPI，默认 `http://127.0.0.1:8788`）
> DSH：DeepSeek 开源的 Agent 运行底座（MIT，开发者预览版）——`npx @deepseek-ai/dsh web` → `http://127.0.0.1:3080`
> 本目录内容：`novel-platform.mjs`（Cordis 插件，10 个工具，**当前版**，适配 DSH 2026-10 工具 API）、`novel-platform.ts`（旧版，API 已过期，仅留档）、`cordis.yml`（挂载 patch）、本说明。

---

## 〇、先明确两种"接法"（可同时用）

| 接法 | 提供什么 | 适合 |
|---|---|---|
| **A. 插件（本目录）** | **状态 + 执行**：书架、章节、生成、扫描、导出、任务进度 | 接着现有书往下写（12 章 / 伏笔台账 / 摘要全在平台里） |
| **B. 技能（ai-xiaoshuo-skill）** | **方法论 + 指令**：手册 13 模块 / 35 万字原典 | 从零开新书、或不装平台时也让模型按手册干活 |

DSH 的 skill 就是 **SKILL.md 目录束**（kebab-case 名 + description 必填），我们的 `ai-xiaoshuo-skill` 格式**完全兼容**。

---

## A. 插件：让 DSH 直接操作写作平台

### 前置
1. **平台已启动**（二选一）：
   - 双击 `D:\ai-xiaoshuo\启动写作平台.bat`；
   - 或 `cd D:\ai-xiaoshuo && python -m uvicorn app.main:app --port 8788`。
   - 探活：`curl http://127.0.0.1:8788/api/health` 返回 `{"ok":true,...}` 即可。
2. **DSH 已安装**：Node.js ≥ 22；`npx @deepseek-ai/dsh web` 起 Web UI（首次填 DeepSeek API Key）。

### 挂载（三种方式，按你的 DSH 形态选）

**方式 1：桌面版 DSH（本机当前形态，推荐）**

桌面版走的是 profile patch 层，没有 `dsh web --patch` 入口。编辑

`C:\Users\<用户名>\.dsh\profiles\desktop\cordis.patch.yml`

在末尾追加：

```yaml
- insert:
    - id: novel-platform
      name: "file:///<项目绝对路径>/integrations/dsh-plugin/novel-platform.mjs"
```

保存后**完全退出再重启** DeepSeek Harness（profile 未启用 HMR，改 patch 不会热生效）。重启后新会话里工具列表应出现 `novel_list_books` 等 10 个工具。

**方式 2：源码仓库 + patch 覆盖层（官方推荐做法）**
```bash
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install && pnpm run build
pnpm dsh web --patch "<项目绝对路径>/integrations/dsh-plugin/cordis.yml"
```
`cordis.yml` 里 `name` 必须是**绝对路径**（换目录就改那一行）。

**方式 2：npx 直接把插件文件交给 DSH（若你的版本支持 --patch）**
```bash
npx @deepseek-ai/dsh web --patch "<项目绝对路径>/integrations/dsh-plugin/cordis.yml"
```

启动后终端出现插件加载日志 / Web UI 里能看到工具列表即为成功。DSH 是预览版，若 `ctx.tools.register` 的字段与报错不符，按 `develop/basic/tool` 文档微调 `novel-platform.ts`（业务逻辑不用动）。

### 在 DSH 里怎么用（对话示例）
> 「列出写作平台里的书，把《直播秘境：这副本我当年写的》第 13 章写出来：本章要点……写完跑一遍违禁词扫描，再导出合并稿。」

Agent 会自动串起：`novel_list_books` → `novel_book_flow` → `novel_create_chapter` → `novel_write_chapter`（内部轮询任务链）→ `novel_check_violations` → `novel_export`。

### 工具清单（10 个）
| 工具 | 作用 | 关键参数 |
|---|---|---|
| `novel_list_books` | 书架（id/书名/章节数） | — |
| `novel_book_flow` | 流程步骤 + 章节列表 + 当前步 | `book?` |
| `novel_kb_search` | 手册知识库检索 | `query`, `module?` |
| `novel_kb_blocks` | 抽取 skill 原文指令块 | `modules[]`, `keywords[]` |
| `novel_get_chapter` | 读正文 + 摘要 | `no` |
| `novel_create_chapter` | 新建章节（不花钱） | `no`, `title`, `brief` |
| `novel_write_chapter` | **一键写完本章**（起草→去AI味→摘要→伏笔→扫描）**并等待** | `no`, `title?`, `brief?` |
| `novel_generate_step` | 任意单步生成（含 `extra` 补充要求） | `step_id`, `chapter_no?`, `extra?` |
| `novel_check_violations` | 违禁词/AI 痕迹扫描 | `chapter_no?` |
| `novel_export` | 导出 txt | `mode?` |

> 平台默认 `127.0.0.1:8788`；如换端口，在 DSH 进程里设环境变量 `NOVEL_PLATFORM_BASE`。

---

## B. 技能：让 DSH 直接加载 `ai-xiaoshuo-skill`

DSH 技能发现目录（按优先级，数字越小越优先）：
1. `<项目>/.dsh/skills/`（项目级，最高）
2. `<项目>/.agents/skills/`
3. `Config.customSkillDirs`
4. `~/.dsh/skills/`（全局）
5. 用户级 agents 目录
6. bundled（内置）

**操作**：把整个 `ai-xiaoshuo-skill` 文件夹（`SKILL.md` + `references/`）拷进目标目录即可：
```text
<你的工作区>/.dsh/skills/ai-xiaoshuo-skill/SKILL.md
<你的工作区>/.dsh/skills/ai-xiaoshuo-skill/references/00-...md … 99-...md
```
或全局：`~/.dsh/skills/ai-xiaoshuo-skill/`。

- 名称必须 kebab-case（`ai-xiaoshuo-skill` ✓），frontmatter 只要求 `name` + `description`（我们已满足）。
- DSH 是**渐进披露**：模型先看目录摘要，需要时调 `skill({name:"ai-xiaoshuo-skill"})` 展开 SKILL.md，再按里面的路由去读 references —— 35 万字不会一次性塞进上下文。
- 局限：skill 只有方法与指令，**没有书/章节/伏笔/生成记录/合规扫描**这些状态。要接着这本 12 章的书写，请走 A。

---

## C. 模型怎么配（两个层面，别混）

1. **DSH 自己的模型**：Web UI → 设置 → 模型 → 填 DeepSeek API Key（或自定义 OpenAI 兼容端点，接本机 Ollama/vLLM 也行）。
2. **平台自己的模型**：平台是 OpenAI 兼容网关，模型配置在「模型」页；想直连 DeepSeek 官方 API 就新增一条：
   - `base_url`: `https://api.deepseek.com`（OpenAI 兼容）
   - `api_key`: DeepSeek 官方 key
   - `model_name`: 以官方 `/v1/models` 返回为准（别猜），然后 `POST /api/models/{id}/test` 验证。
   - 之后在「模型」页按步骤绑定（起草 / 改写 / 策划 / 扫描）。

> 两套模型互不影响：DSH 的模型负责"当 Agent"，平台的模型负责"写小说"。

---

## D. 平台好不好改？改哪里（速查）

| 想改什么 | 改哪里 | 验证 |
|---|---|---|
| 手册原文 / 检查清单 / 知识库 | `app/knowledge/data/references/*.md`（或把 `.env` 的 `PLATFORM_SKILL_DIR` 指到用户级 skill 的 references，保持单一真源） | 平台「知识库」页刷新可见 |
| 流程步骤（9 步/10 步/8 步、每步用哪些模块/关键词/输入） | `app/flows/definitions.py` | 重启 + `tools/dev/smoke_test.py --inproc` |
| 提示词与输出要求（含篇幅约束、改写保护阈值） | `app/flows/runner.py`（`STEP_OUTPUT_REQ` / `STEP_OUTPUT_REQ_BY_FLOW` 等） | 同上 |
| 指令块抽取规则 | `app/knowledge/blocks.py` | 同上 |
| 违禁词 / AI 特征词 | `app/checks/vocab.json` | 扫描页复跑 |
| 前端界面 | `static/app.js` + `index.html` | 刷新页面 |
| 数据库结构 | `app/store.py`（SCHEMA） | 重启自动迁移/备份 |

**事实**：`app/knowledge/data` 是 `ai-xiaoshuo-skill` 的**逐字节完整副本**（SKILL.md 3,703 字 + references 13 篇 / 355,512 字，2026-10-19 校验无差异）；但**工作流不是通用的 skill 解释器**，而是 `definitions.py` 里的硬编码实现——这就是它比"纯 skill"多出来的执行能力来源。

---

## E. 排查

| 症状 | 处理 |
|---|---|
| 工具报 `fetch failed` / ECONNREFUSED | 平台没起：双击 `启动写作平台.bat`；再 `curl /api/health` |
| 端口不是 8788 | 设 `NOVEL_PLATFORM_BASE=http://127.0.0.1:<端口>` 再启动 DSH |
| 插件没加载 | `cordis.yml` 的 `name` 必须是绝对路径；确认 `--patch` 指向的是这个 yml |
| 工具字段报错 | DSH 预览版 API 可能变动：对照 `develop/basic/tool` 调整 `parameters` / `execute` 的字段名 |
| 平台 402 / 余额不足 | 平台侧模型网关欠费：换 key（全量替换 models 表）后逐个 `/test` |

---

*2026-10-19 建立。DSH 仍在快速迭代，涉及插件 DSL 的细节以官方文档 https://deepseek-harness.github.io/deepseek-harness/ 为准。*
