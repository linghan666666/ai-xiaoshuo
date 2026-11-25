/**
 * novel-platform.ts —— 把「AI 小说写作平台（<项目绝对路径>）」接进 DeepSeek Harness（DSH）
 *
 * 形态：Cordis 插件（TypeScript）。注册一组工具，execute 里直接调平台的 REST API。
 * 前置：写作平台已在本机运行（默认 http://127.0.0.1:8788，可用环境变量 NOVEL_PLATFORM_BASE 覆盖）。
 * 挂载：见同目录 cordis.yml 与 README.md。
 *
 * 注意：DSH 仍是开发者预览版，工具 DSL（parameters / execute 返回值）以官方
 *      /deepseek-harness/develop/basic/tool 为准；若升级后字段有变，按报错微调本文件即可，
 *      下面的业务逻辑（对平台 API 的调用）不依赖 DSH 内部实现。
 */
import type { Context } from '@deepseek-ai/cordis'

export const name = 'novel-platform'
export const inject = ['tools']

const BASE = process.env.NOVEL_PLATFORM_BASE ?? 'http://127.0.0.1:8788'

async function api(path: string, init?: any): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    ...(init ?? {}),
    headers: { 'content-type': 'application/json', ...((init && init.headers) ?? {}) },
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`[novel-platform] HTTP ${res.status} ${path}: ${text.slice(0, 400)}`)
  try {
    return JSON.parse(text)
  } catch {
    return { raw: text }
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function resolveBook(book?: string): Promise<{ id: string; name: string }> {
  const r = await api('/api/books')
  const books = r.books ?? []
  if (!books.length) throw new Error('写作平台里还没有书，请先在平台里创建')
  const hit = book
    ? books.find((b: any) => b.id === book || String(b.name ?? '').includes(book))
    : books[0]
  const b = hit ?? books[0]
  return { id: b.id, name: b.name }
}

async function findChapter(pid: string, no: number): Promise<any | undefined> {
  const r = await api(`/api/books/${pid}/chapters`)
  return (r.chapters ?? []).find((c: any) => Number(c.no) === Number(no))
}

/** 轮询任务直到结束（或超时） */
async function waitTask(taskId: string, timeoutSec = 900): Promise<any> {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutSec * 1000) {
    const r = await api(`/api/tasks/${taskId}`)
    const t = r.task ?? r
    if (t && t.status && t.status !== 'running') return t
    await sleep(8000)
  }
  return { status: 'timeout', task_id: taskId, hint: '任务仍在后台跑，稍后用 novel_task_status 再查' }
}

export function apply(ctx: Context) {
  const tools = (ctx as any).tools
  const register = (def: any) => tools.register(def)

  // ── 1. 书架 ──────────────────────────────────────────────
  register({
    name: 'novel_list_books',
    description:
      '列出「AI 小说写作平台」里的书（id / 书名 / 章节数 / 已完成产出数）。任何小说任务先调它拿 book_id。',
    parameters: { type: 'object', properties: {} },
    execute: async () => {
      const r = await api('/api/books')
      return (r.books ?? []).map((b: any) => ({
        id: b.id,
        name: b.name,
        chapters: b.chapters,
        artifacts: b.artifacts,
      }))
    },
  })

  // ── 2. 书况：流程步骤 + 章节列表 ─────────────────────────
  register({
    name: 'novel_book_flow',
    description:
      '查看一本书的流程状态：当前步骤、每个步骤是否有产出、章节列表（编号/标题/字数/状态）。book 可传书名片段或 book_id，省略取最近更新的书。',
    parameters: {
      type: 'object',
      properties: { book: { type: 'string', description: '书名片段或 book_id（可省略）' } },
    },
    execute: async ({ book }: any) => {
      const b = await resolveBook(book)
      const flow = await api(`/api/books/${b.id}/flow`)
      return {
        book: b.name,
        current_step: (flow.steps ?? []).find((s: any) => s.is_current)?.name,
        steps: (flow.steps ?? []).map((s: any) => ({
          no: s.no,
          name: s.name,
          has_output: s.has_output,
          locked: s.locked,
        })),
        chapters: (flow.chapters ?? []).map((c: any) => ({
          no: c.no,
          title: c.title,
          words: c.words,
          status: c.status,
        })),
      }
    },
  })

  // ── 3. 知识库检索（手册原文节）──────────────────────────
  register({
    name: 'novel_kb_search',
    description:
      '在平台的《番茄小说 AI 写作手册》知识库（13 模块 / 35 万字）里检索原文节。写情节、查规则、找模板时用。',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '关键词，如「黄金三章」「违禁词」「书名公式」' },
        module: { type: 'string', description: '限定模块号（如 "03"），可省略' },
        max_chars: { type: 'number', description: '返回内容上限，默认 4000' },
      },
      required: ['query'],
    },
    execute: async ({ query, module, max_chars }: any) =>
      api('/api/kb/search', {
        method: 'POST',
        body: JSON.stringify({ query, module: module ?? '', max_chars: max_chars ?? 4000 }),
      }),
  })

  // ── 4. 抽取 skill 原文指令块（模块 + 关键词）────────────
  register({
    name: 'novel_kb_blocks',
    description:
      '按「模块号 + 关键词」抽取 skill 原文指令块（平台生成提示词用的就是它）。需要照着手册给作者出方案时用。',
    parameters: {
      type: 'object',
      properties: {
        modules: { type: 'array', items: { type: 'string' }, description: '模块号数组，如 ["02d","05"]' },
        keywords: { type: 'array', items: { type: 'string' }, description: '关键词数组' },
        limit: { type: 'number', description: '最多几块，可省略' },
      },
      required: ['modules'],
    },
    execute: async ({ modules, keywords, limit }: any) =>
      api('/api/kb/blocks', {
        method: 'POST',
        body: JSON.stringify({ modules, keywords: keywords ?? [], limit: limit ?? 0 }),
      }),
  })

  // ── 5. 读一章（正文 + 摘要）────────────────────────────
  register({
    name: 'novel_get_chapter',
    description: '读取某一章的正文与结构化摘要（跨章连贯靠摘要）。参数：book 可省略；no = 章节号。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        no: { type: 'number', description: '章节号，如 12' },
      },
      required: ['no'],
    },
    execute: async ({ book, no }: any) => {
      const b = await resolveBook(book)
      const ch = await findChapter(b.id, no)
      if (!ch) throw new Error(`第 ${no} 章不存在`)
      const full = await api(`/api/chapters/${ch.id}`)
      const c = full.chapter ?? {}
      return { book: b.name, no: c.no, title: c.title, words: c.words, status: c.status, content: c.content, summary: c.summary }
    },
  })

  // ── 6. 新建章节（标题 + 本章要点）──────────────────────
  register({
    name: 'novel_create_chapter',
    description: '新建一章：给定章节号、标题、本章要点（给模型的写作指令），返回章节 id。不花钱、不调模型。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        no: { type: 'number', description: '章节号' },
        title: { type: 'string', description: '章节标题' },
        brief: { type: 'string', description: '本章要点：情节推进、爽点、章尾钩子、字数要求等' },
      },
      required: ['no', 'title', 'brief'],
    },
    execute: async ({ book, no, title, brief }: any) => {
      const b = await resolveBook(book)
      const r = await api(`/api/books/${b.id}/chapters`, {
        method: 'POST',
        body: JSON.stringify({ no, title, brief }),
      })
      return { book: b.name, chapter: r.chapter ? { id: r.chapter.id, no: r.chapter.no, title: r.chapter.title } : r }
    },
  })

  // ── 7. 一键写完本章（起草 → 去AI味 → 摘要 → 伏笔 → 扫描）──
  register({
    name: 'novel_write_chapter',
    description:
      '对某一章跑「一键写完本章」完整链（起草正文 → 去 AI 味 → 章节摘要 → 更新伏笔台账 → 违禁词扫描），并等待完成。缺章时会先按 title/brief 建章。返回最终字数、状态、摘要长度、任务各步状态。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        no: { type: 'number', description: '章节号' },
        title: { type: 'string', description: '若该章不存在，用它建章' },
        brief: { type: 'string', description: '若该章不存在，用这段要点建章' },
        wait_seconds: { type: 'number', description: '最长等待秒数，默认 900' },
      },
      required: ['no'],
    },
    execute: async ({ book, no, title, brief, wait_seconds }: any) => {
      const b = await resolveBook(book)
      let ch = await findChapter(b.id, no)
      if (!ch) {
        if (!title || !brief) throw new Error(`第 ${no} 章不存在，请给 title 和 brief 先建章`)
        const created = await api(`/api/books/${b.id}/chapters`, {
          method: 'POST',
          body: JSON.stringify({ no, title, brief }),
        })
        ch = created.chapter
      }
      const kicked = await api(`/api/chapters/${ch.id}/finish`, { method: 'POST' })
      const task = await waitTask(kicked.task_id, wait_seconds ?? 900)
      const latest = await findChapter(b.id, no)
      const full = await api(`/api/chapters/${ch.id}`)
      const c = full.chapter ?? {}
      return {
        book: b.name,
        no,
        task_status: task.status,
        steps: (task.steps ?? []).map((s: any) => ({ name: s.name, status: s.status, msg: s.msg })),
        words: latest?.words ?? c.words,
        chapter_status: latest?.status ?? c.status,
        summary_chars: (c.summary ?? '').length,
      }
    },
  })

  // ── 8. 单步生成（书级步骤 / 章节级步骤 / 改写）──────────
  register({
    name: 'novel_generate_step',
    description:
      '跑平台的某一步骤（如 topic 选题 / title_intro 书名简介 / characters 人设 / outline 大纲 / golden3 黄金三章 / chapter 起草 / deai 去AI味），可带 extra 补充要求，等待完成后返回生成记录摘要。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        step_id: { type: 'string', description: '步骤 id（见 novel_book_flow 的步骤名或平台流程页）' },
        chapter_no: { type: 'number', description: '章节级步骤时传章节号' },
        extra: { type: 'string', description: '追加给模型的补充要求（可选）' },
        wait_seconds: { type: 'number', description: '最长等待秒数，默认 900' },
      },
      required: ['step_id'],
    },
    execute: async ({ book, step_id, chapter_no, extra, wait_seconds }: any) => {
      const b = await resolveBook(book)
      const payload: any = { step_id }
      if (chapter_no) {
        const ch = await findChapter(b.id, chapter_no)
        if (!ch) throw new Error(`第 ${chapter_no} 章不存在`)
        payload.chapter_id = ch.id
      }
      if (extra) payload.extra = extra
      const g = await api(`/api/books/${b.id}/generate`, { method: 'POST', body: JSON.stringify(payload) })
      const t0 = Date.now()
      let gen: any = null
      while (Date.now() - t0 < (wait_seconds ?? 900) * 1000) {
        const r = await api(`/api/gens/${g.gen_id}`)
        gen = r.gen ?? r
        if (gen && gen.status && gen.status !== 'running') break
        await sleep(6000)
      }
      return {
        book: b.name,
        step_id,
        model: gen?.model_name,
        status: gen?.status,
        chars: gen?.chars,
        error: gen?.error ?? null,
        gen_id: g.gen_id,
      }
    },
  })

  // ── 9. 违禁词 / AI 痕迹扫描 ────────────────────────────
  register({
    name: 'novel_check_violations',
    description: '跑平台的违禁词与 AI 痕迹扫描（可指定某一章；不指定则扫全书）。返回结论文本与命中明细。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        chapter_no: { type: 'number', description: '章节号（可省略=全书）' },
      },
    },
    execute: async ({ book, chapter_no }: any) => {
      const b = await resolveBook(book)
      const payload: any = {}
      if (chapter_no) {
        const ch = await findChapter(b.id, chapter_no)
        if (!ch) throw new Error(`第 ${chapter_no} 章不存在`)
        payload.chapter_id = ch.id
      }
      const r = await api(`/api/books/${b.id}/check/violations`, { method: 'POST', body: JSON.stringify(payload) })
      return { book: b.name, chapter_no: chapter_no ?? '全书', report: r.report, ai_words: r.ai_words }
    },
  })

  // ── 10. 导出成稿 ────────────────────────────────────────
  register({
    name: 'novel_export',
    description: '把一本书导出为 txt（合并或分章），返回文件路径与章节/字数统计。',
    parameters: {
      type: 'object',
      properties: {
        book: { type: 'string', description: '书名片段或 book_id（可省略）' },
        mode: { type: 'string', description: 'merged（合并，默认）或 chapters（分章）' },
      },
    },
    execute: async ({ book, mode }: any) => {
      const b = await resolveBook(book)
      const r = await api(`/api/books/${b.id}/export`, {
        method: 'POST',
        body: JSON.stringify({ mode: mode ?? 'merged', fmt: 'txt' }),
      })
      return { book: b.name, files: r.files, dir: r.dir, count: r.count, words: r.words }
    },
  })
}
