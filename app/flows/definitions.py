"""流程定义：**skill 的工作流就是平台的流程**。

三个流程直接对应 skill 的 SKILL.md「核心工作流」：
  · long_novel  长篇开新书（9 步）—— SKILL.md 工作流 A
  · mid_novel   完本中篇（10 步）—— 工作流 A 的「完本」改编 + 番茄官方《完本签约模式》三审
                （15–30 万字；Lv.0/Lv.1 新作者的新作品只能走完本签约）
  · short_story 番茄短篇（8 步）—— SKILL.md 工作流 B（严格按 06 模块的 8 步流水线）
  · stuck       卡文 / 人设崩坏急救 —— SKILL.md 工作流 C

每一步声明四件事，平台据此自动组装：
  modules   去哪些 skill 模块取指令原文（支持 02b 这类简称）
  keywords  在该模块里按什么关键词挑原文节（抽取逻辑见 knowledge/blocks.py）
  inputs    组装提示词时带上的前序产出（步骤 id；空 = 只带书的资料）
  check     该步配套的规则检查（blacklist / violations / presubmit）
  stage     book=全书级（做一次）/ chapter=章节级（可重复，针对某一章）

⚠️ 步骤的用词尽量与 SKILL.md、06 模块的原文一致（如「黄金三章」「五章分章大纲」
「多版本章节结尾钩子」），这样关键词能精确命中对应的原文节。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

LONG_NOVEL: Dict[str, Any] = {
    "id": "long_novel",
    "name": "长篇开新书（9 步）",
    "source": "SKILL.md 工作流 A + 03 / 05 / 02a / 02d / 01 模块",
    "intro": "从选题到投稿前自查的完整长篇小说开工流程。顺序有讲究：先定方向 → 过合规 → "
             "书名简介 →**先锁人设** → 大纲 → 黄金三章 → 章节续写 → 去 AI 味 → 投稿前自查。",
    "steps": [
        {"id": "topic", "no": 1, "name": "选题定方向",
         "goal": "先查题材黑名单，再定方向：找有流量的题材，避开烂大街与红黑线。",
         "modules": ["03", "02a"], "keywords": ["黑名单", "选题", "定位", "流量"],
         "inputs": [], "check": "blacklist", "stage": "book"},
        {"id": "compliance", "no": 2, "name": "合规预检",
         "goal": "把题材与设定过一遍违禁词清单与违规情节自查表，确认题材本身不触线。",
         "modules": ["01"], "keywords": ["违禁词清单", "违规情节自查表", "投稿自查"],
         "inputs": ["topic"], "check": "violations", "stage": "book"},
        {"id": "title_intro", "no": 3, "name": "书名与简介",
         "goal": "按手册的书名公式 / 简介公式出候选：书名决定点击，简介决定读完率。"
                 "★ 定稿前务必在番茄搜一遍：①完整书名 ②书名前 4-6 字 ③核心句式"
                 "（句式撞车同样伤流量，平台无法代查，这一步必须人工做）。",
         "modules": ["03"], "keywords": ["书名公式", "高点击书名", "简介公式", "吸睛简介"],
         "inputs": ["topic", "compliance"], "stage": "book"},
        {"id": "characters", "no": 4, "name": "人设档案（先锁人设）",
         "goal": "写正文之前先把人设锁死：主角 + 核心配角 + 反派阶梯，逐位给基础信息 / 行为红线 / 说话风格 / 专属小习惯。",
         "modules": ["05"], "keywords": ["人设档案锁死法", "行为红线阈值法", "对话风格锁死法",
                                        "人配角群像", "心理逻辑校验法"],
         "inputs": ["topic", "title_intro"], "stage": "book"},
        {"id": "outline", "no": 5, "name": "大纲与长线",
         "goal": "搭故事大纲：主线、单元节奏、长线伏笔表，为改编与延展留接口。",
         "modules": ["02a"], "keywords": ["故事大纲", "大纲", "世界观", "伏笔"],
         "inputs": ["characters", "title_intro"], "stage": "book"},
        {"id": "golden3", "no": 6, "name": "黄金三章",
         "goal": "开篇三章的写法与钩子：前 300 字出冲突，不许大段铺垫。",
         "modules": ["02a", "03"], "keywords": ["黄金三章", "开篇公式", "爆款开头"],
         "inputs": ["characters", "outline"], "stage": "book"},
        {"id": "chapter", "no": 7, "name": "章节续写",
         "goal": "逐章写正文：贴紧人设与大钩子，一章一个推进，章尾留钩子。",
         "modules": ["02a"], "keywords": ["续写", "单章", "章节", "爽点"],
         "inputs": ["characters", "outline", "golden3"], "stage": "chapter", "repeatable": True},
        {"id": "deai", "no": 8, "name": "去 AI 味",
         "goal": "按 02d 的万能去 AI 味指令改写本章：保剧情、改表达，删模板化句式与套话。",
         "modules": ["02d", "05"], "keywords": ["万能通用核心去AI味", "去AI化实战", "替换特征词"],
         "inputs": ["characters"], "stage": "chapter", "repeatable": True},
        {"id": "presubmit", "no": 9, "name": "投稿前自查",
         "goal": "投稿前逐条核对清单（题材/字数/开篇/人设/参数/逻辑/AI 痕迹/钩子/排版/原创）。",
         "modules": ["01", "06"], "keywords": ["投稿自查核对清单", "终稿核验"],
         "inputs": [], "check": "presubmit", "stage": "book"},
    ],
}

MID_NOVEL: Dict[str, Any] = {
    "id": "mid_novel",
    "name": "完本中篇（10 步）",
    "source": "SKILL.md 工作流 A（9 步）的「完本」改编 + 番茄官方《完本签约模式》（2026-09-07 上线、"
              "2026-10-12 起 Lv.0/Lv.1 新作品仅可完本签约）+《作品签约标准说明》+《作家等级 3.0》成长分口径",
    "intro": "15–30 万字完本中篇（番茄「完本签约模式」）。与长篇 9 步同源，但把「连载体量」换成「完本线」："
             "大纲要一次收干净、伏笔必须全回收；并在最后两步加上**完本三审预检**（完结 / 安全 / 质量，"
             "对应官方三审与 3 次申请机会）与投稿前自查。"
             "⚠️ 完本模式签约前作品不对读者展示、签约后内容不可修改 —— 不适用日更/冷启动池那套连载玩法。",
    "steps": [
        {"id": "topic", "no": 1, "name": "选题定方向",
         "goal": "先查题材黑名单，再定方向：找有流量的题材，避开烂大街与红黑线。"
                 "★ 完本签约由编辑按「内容/创新/立意/文字/潜力」综合评估，创新性比蹭热门更重要。",
         "modules": ["03", "02a"], "keywords": ["黑名单", "选题", "定位", "流量"],
         "inputs": [], "check": "blacklist", "stage": "book"},
        {"id": "compliance", "no": 2, "name": "合规预检",
         "goal": "把题材与设定过一遍违禁词清单与违规情节自查表，确认题材本身不触线。",
         "modules": ["01"], "keywords": ["违禁词清单", "违规情节自查表", "投稿自查"],
         "inputs": ["topic"], "check": "violations", "stage": "book"},
        {"id": "title_intro", "no": 3, "name": "书名与简介",
         "goal": "按手册的书名公式 / 简介公式出候选：书名决定点击，简介决定读完率。"
                 "★ 定稿前务必在番茄搜一遍同名与同句式（平台无法代查，这一步必须人工做）。",
         "modules": ["03"], "keywords": ["书名公式", "高点击书名", "简介公式", "吸睛简介"],
         "inputs": ["topic", "compliance"], "stage": "book"},
        {"id": "characters", "no": 4, "name": "人设档案（先锁人设）",
         "goal": "写正文之前先把人设锁死。★ 中篇规模要克制：主角 + 核心配角 3-5 位 + 对手 2-3 位，"
                 "每位给行为红线 / 说话风格 / 专属小习惯 / 结局走向。",
         "modules": ["05"], "keywords": ["人设档案锁死法", "行为红线阈值法", "对话风格锁死法",
                                        "人配角群像", "心理逻辑校验法"],
         "inputs": ["topic", "title_intro"], "stage": "book"},
        {"id": "outline", "no": 5, "name": "大纲与完本线",
         "goal": "搭**能一次收干净**的中篇大纲：五幕 + 每幕章数（合计 ≈100 章 / 20 万字）+ 主线在第 N 章收束 + "
                 "伏笔逐个标注回收章。不要写成连载式「越写越大」。",
         "modules": ["02a"], "keywords": ["故事大纲", "大纲", "世界观", "伏笔"],
         "inputs": ["characters", "title_intro"], "stage": "book"},
        {"id": "golden3", "no": 6, "name": "黄金三章",
         "goal": "开篇三章的写法与钩子：前 300 字出冲突，不许大段铺垫。",
         "modules": ["02a", "03"], "keywords": ["黄金三章", "开篇公式", "爆款开头"],
         "inputs": ["characters", "outline"], "stage": "book"},
        {"id": "chapter", "no": 7, "name": "章节续写",
         "goal": "逐章写正文：贴紧人设与大钩子，一章一个推进，章尾留钩子。",
         "modules": ["02a"], "keywords": ["续写", "单章", "章节", "爽点"],
         "inputs": ["characters", "outline", "golden3"], "stage": "chapter", "repeatable": True},
        {"id": "deai", "no": 8, "name": "去 AI 味",
         "goal": "按 02d 的万能去 AI 味指令改写本章：保剧情、改表达，删模板化句式与套话。",
         "modules": ["02d", "05"], "keywords": ["万能通用核心去AI味", "去AI化实战", "替换特征词"],
         "inputs": ["characters"], "stage": "chapter", "repeatable": True},
        {"id": "finish_audit", "no": 9, "name": "完本三审预检",
         "goal": "正式投稿前，先按番茄完本签约官方三审自查一遍："
                 "①**完结审核**——故事是否完整、主线是否收干净、有无烂尾（不通过就进不了下一关）；"
                 "②**安全审核**——违禁词、低俗色情、违反公序良俗、侵权抄袭；"
                 "③**质量评估**——是否有粗制滥造 / 逻辑混乱 / 结构失常 / 内容空洞或刻意注水；"
                 "是否符合优质内容四标准（创新性 / 立意 / 文笔 / 结构完整与潜力）。"
                 "★ 输出「通过 / 需修改 / 不通过」+ 优先修改 3 件事。"
                 "★ 平台每部完本作品只有 3 次申请机会，这一关就是为那 3 次机会做预演。",
         "modules": ["01", "06"], "keywords": ["违禁词清单", "违规情节自查表", "终稿核验",
                                              "投稿自查核对清单"],
         "inputs": ["outline", "__manuscript__"], "stage": "book"},
        {"id": "presubmit", "no": 10, "name": "投稿前自查",
         "goal": "投稿前逐条核对清单（题材/字数/开篇/人设/参数/逻辑/AI 痕迹/钩子/排版/原创）。",
         "modules": ["01", "06"], "keywords": ["投稿自查核对清单", "终稿核验"],
         "inputs": [], "check": "presubmit", "stage": "book"},
    ],
}

SHORT_STORY: Dict[str, Any] = {
    "id": "short_story",
    "name": "短篇工业化（8 步）",
    "source": "SKILL.md 工作流 B + 06 模块「完整标准化创作流水线」（共 8 步，禁止跳步）",
    "intro": "8000-25000 字短篇的工业化流水线：设定表 → 五章大纲 → 大纲核查 → 单章正文 → "
             "三层加工 → 章尾钩子 → 全文漏洞扫描 → 终稿核验。",
    "steps": [
        {"id": "setup", "no": 1, "name": "三份基础设定表",
         "goal": "填空式完成故事底层根基（三份基础设定表），短篇的所有后续步骤都依赖它。",
         "modules": ["06"], "keywords": ["前置底层认知", "基础设定表", "填空"],
         "inputs": [], "stage": "book"},
        {"id": "outline", "no": 2, "name": "五章分章大纲",
         "goal": "套用【指令1：五章完整分章大纲生成指令】，让 AI 输出分章大纲。",
         "modules": ["06"], "keywords": ["五章完整分章大纲生成指令", "标准化创作流水线"],
         "inputs": ["setup"], "stage": "book"},
        {"id": "outline_check", "no": 3, "name": "大纲三项核查修订",
         "goal": "对 AI 大纲做三项标准化核查与修订，确认无误再进正文。",
         "modules": ["06"], "keywords": ["三项标准化核查", "核查与修订"],
         "inputs": ["outline"], "stage": "book"},
        {"id": "draft", "no": 4, "name": "单章正文生成",
         "goal": "套用【指令2：单章正文生成指令】，一次只生成一章，禁止一次性出全文。",
         "modules": ["06"], "keywords": ["单章正文生成指令", "单章"],
         "inputs": ["setup", "outline"], "stage": "chapter", "repeatable": True},
        {"id": "polish", "no": 5, "name": "三层加工处理",
         "goal": "对初稿做三层加工（表达/节奏/细节），把 AI 味压下去。",
         "modules": ["06", "02d"], "keywords": ["三层加工", "去AI味"],
         "inputs": [], "stage": "chapter", "repeatable": True},
        {"id": "hook", "no": 6, "name": "章尾钩子优化",
         "goal": "套用【指令5：多版本章节结尾钩子生成指令】，从多版本里挑一个替换章末。",
         "modules": ["06"], "keywords": ["多版本章节结尾钩子", "钩子"],
         "inputs": [], "stage": "chapter", "repeatable": True},
        {"id": "scan", "no": 7, "name": "全文逻辑漏洞扫描",
         "goal": "套用【指令6：全文逻辑漏洞扫描指令】做全局排查，列出漏洞与修补建议。",
         "modules": ["06"], "keywords": ["全文逻辑漏洞扫描指令", "漏洞"],
         "inputs": [], "stage": "book"},
        {"id": "submit_check", "no": 8, "name": "投稿终稿核验",
         "goal": "用终稿核验清单逐项核对，全部通过再投。",
         "modules": ["06", "01"], "keywords": ["终稿核验", "投稿自查核对清单"],
         "inputs": [], "check": "presubmit", "stage": "book"},
    ],
}

# ── 番茄短故事（2026.4 起官方分类体系）──────────────────────────────
# 与 SHORT_STORY 同为 8 步流水线，但体量按番茄短故事的发布口径改：
#   · 发表超 6000 字即有机会签约（短故事不是长篇小说，也不追求 8000+ 的"长篇短篇"）
#   · 单篇完结上传，正文用「第N章 标题」划分（短故事页签可一键建 5 章骨架）
#   · 发布必须选作品分类：主分类必选，最多 8 个（分类表见 app/short_categories.py）
# 短篇工业化（8000-25000 字）与短故事共用同一套官方课堂方法：起草给节奏爽点、核查给避坑清单
for _step in SHORT_STORY["steps"]:
    if _step["id"] in ("outline", "outline_check", "draft"):
        _mods = list(_step.get("modules") or [])
        if "15" not in _mods:
            _mods.append("15")
        _step["modules"] = _mods
        _kw = list(_step.get("keywords") or [])
        _kw += ["节奏", "冲突", "爽点", "避坑"]
        _step["keywords"] = _kw
        if _step["id"] == "draft":
            _step["block_limit"] = 3


FANQIE_SHORT: Dict[str, Any] = {
    "id": "fanqie_short",
    "name": "番茄短故事（8 步）",
    "source": "番茄作家专区·短故事发布页（2026.4 分类体系）+ 06 模块短篇工业化流水线",
    "intro": "6000–20000 字单篇完结短故事：三份设定表 → 五章大纲 → 大纲核查 → 单章正文 → "
             "三层加工 → 章尾钩子 → 全文漏洞扫描 → 发布前核验。正文 ≥6000 字即有机会签约。",
    # 深拷贝：短故事与「短篇工业化」共用同一套步骤结构，但两边必须能各改各的
    # （原来直接引用 SHORT_STORY["steps"]，改短篇会连带改短故事）
    "steps": copy.deepcopy(SHORT_STORY["steps"]),
}

# 把「番茄短故事发布规格」（14 模块）接进流程：
# 大纲步骤要知道作品分类怎么选，发布前核验要按发布页的 7 项来（而不是只跑通用清单）。
for _step in FANQIE_SHORT["steps"]:
    if _step["id"] == "setup":
        # 官方课堂的「人设五步法」「避坑指南」是设定表最直接的依据
        _step["modules"] = ["06", "15"]
        _step["keywords"] = ["让主角真正立起来", "人物小传", "避坑指南", "短故事避坑"]
    elif _step["id"] == "outline":
        _step["modules"] = ["06", "14", "15"]
        _step["keywords"] = ["五章完整分章大纲生成指令", "短故事分类释义", "五章写作口径",
                             "追妻", "热门方向", "世情", "节奏", "冲突"]
    elif _step["id"] == "outline_check":
        # 大纲核查：按官方课堂的「避坑指南」逐条查（同质化/视角/导语密度/规则矛盾）
        _step["modules"] = ["06", "15"]
        _step["keywords"] = ["三项标准化核查", "避坑指南", "选题", "节奏"]
    elif _step["id"] == "draft":
        # 起草：节奏/冲突/爽点三件事最容易翻车，官方课堂给了具体改法
        _step["modules"] = ["06", "15"]
        _step["keywords"] = ["单章正文生成指令", "节奏", "冲突", "爽点", "追妻"]
        _step["block_limit"] = 3
    elif _step["id"] == "polish":
        _step["modules"] = ["06", "02d", "15"]
        _step["keywords"] = ["三层加工", "去AI味", "节奏", "爽点"]
        _step["block_limit"] = 3
    elif _step["id"] == "submit_check":
        _step["modules"] = ["06", "01", "14", "15"]
        _step["keywords"] = ["终稿核验", "投稿自查核对清单", "发布前自检清单", "试读比例", "作品分类",
                             "自检清单", "追妻"]
        # 通用清单两块（01/06）就占满默认的 3 块额度，短故事的发布页自检会被挤掉；
        # 这一步多给一块，保证 14 模块的「发布前自检清单（短故事版）」能进来。
        _step["block_limit"] = 5

# 长篇：起草步骤接「时间线管理」（官方课堂 15 模块）——长篇最容易翻车的就是时间对不上。
for _step in LONG_NOVEL["steps"]:
    if _step["id"] in ("draft", "chapter"):
        _mods = list(_step.get("modules") or [])
        if "15" not in _mods:
            _mods.append("15")
        _step["modules"] = _mods
        _kw = list(_step.get("keywords") or [])
        _kw += ["时间线", "多线叙事", "节奏", "冲突"]
        _step["keywords"] = _kw

STUCK: Dict[str, Any] = {
    "id": "stuck",
    "name": "卡文 / 人设崩坏急救",
    "source": "SKILL.md 工作流 C + 05 模块（卡文自检 / OOC 纠错 / 去 AI 化）",
    "intro": "写不下去或人设崩了时的急救箱。多数卡文不是句子问题，是大纲与动机问题 —— "
             "先按自检清单定位，再对症状选法。",
    "steps": [
        {"id": "diagnose", "no": 1, "name": "卡文自检定位",
         "goal": "按卡文快速自检清单逐条排查，定位到底卡在大纲、动机还是桥段。",
         "modules": ["05"], "keywords": ["卡文快速自检清单", "卡文"],
         "inputs": [], "stage": "book"},
        {"id": "ooc_fix", "no": 2, "name": "OOC 即时纠错",
         "goal": "对崩掉的人设/对话做纠错改写，回到人设档案锁定的行为与口吻。",
         "modules": ["05"], "keywords": ["OOC即时纠错改写", "对话风格锁死法", "心理逻辑校验法"],
         "inputs": [], "stage": "chapter", "repeatable": True},
        {"id": "deai_fix", "no": 3, "name": "去 AI 化补强",
         "goal": "按去 AI 化实战 8 法对症状补强：口语化、碎感、具象细节、打乱节奏。",
         "modules": ["05", "02d"], "keywords": ["去AI化实战", "万能通用核心去AI味"],
         "inputs": [], "stage": "chapter", "repeatable": True},
    ],
}

FLOWS: Dict[str, Dict[str, Any]] = {f["id"]: f for f in (LONG_NOVEL, MID_NOVEL, SHORT_STORY, FANQIE_SHORT, STUCK)}

# 开书流程入口（「卡文急救」是工具箱，不作为开书入口）
# mid_novel = 番茄「完本签约模式」：Lv.0/Lv.1 新作者的新作品只能走这条。
BOOK_FLOWS = ["long_novel", "mid_novel", "short_story", "fanqie_short"]


def get_flow(flow_id: str) -> Optional[Dict[str, Any]]:
    return FLOWS.get(flow_id)


def get_step(flow_id: str, step_id: str) -> Optional[Dict[str, Any]]:
    flow = FLOWS.get(flow_id) or {}
    for s in flow.get("steps", []):
        if s["id"] == step_id:
            return s
    return None


def next_step(flow_id: str, step_id: str) -> Optional[Dict[str, Any]]:
    flow = FLOWS.get(flow_id) or {}
    steps = flow.get("steps", [])
    for i, s in enumerate(steps):
        if s["id"] == step_id:
            return steps[i + 1] if i + 1 < len(steps) else None
    return steps[0] if steps else None
