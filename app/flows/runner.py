"""流程执行器：把「书 + 步骤 + skill 原文指令」组装成提示词，调用模型并留痕。

组装顺序（这是平台的"提示词工厂"）：
    book 的资料 → 前序产出（人设档案 / 大纲…）→ **skill 原文指令** → 本章信息 → 补充要求
skill 原文指令放在"依据"位置而不是系统提示里，是为了让它可核对、可复制、可追溯。

生成一律走后台任务：接口立刻返回 gen_id，前端轮询；这样长文生成不会卡住 HTTP，
也避免超时就丢结果（历史可在"生成记录"里回看提示词与产出）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .. import store, textkit
from ..config import (CONTEXT_SUMMARY_CHAPTERS, CONTEXT_TAIL_CHARS,
                      PROMPT_EXTRA_MAX_CHARS, TRACK_MODULES, estimate_cost)
from ..knowledge import blocks as kb_blocks
from ..llm import client as llm
from . import definitions

RUNNING: Dict[str, Any] = {}
TASK_LIMIT = float(os.environ.get("PLATFORM_TASK_LIMIT", "2400"))


# ---------------------------------------------------------------- 后台任务承载

def spawn(coro) -> None:
    """把后台协程跑起来，**绝不阻塞调用方**。

    有事件循环（async 端点里调用）→ 挂到该循环；没有（同步端点跑在 threadpool 里）→
    另起一个线程跑自己的循环。

    ⚠️ 这里绝不能再出现 `except RuntimeError: asyncio.run(...)` 这种兜底：
    那等于把"后台任务"静默降级成"在请求线程里同步跑完"——正是「一键写完本章」
    逐步进度失效、提交要等好几分钟的根因（工具 tools/dev/probe_async.py 专门盯着这条）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and not loop.is_closed():
        loop.create_task(coro)
        return
    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()

AUDIENCE_LABEL = {"male": "男频", "female": "女频", "none": "不限"}

# 写「新正文」的步骤（需要前情摘要 + 伏笔台账；改写类步骤不需要，省 token）
DRAFT_STEPS = ("chapter", "draft")

SYSTEM = ("你是番茄小说平台的资深作者兼编辑，严格按用户给出的手册指令工作。"
          "只输出被要求的成品，不解释、不评论、不输出 markdown 围栏。")

# 每章摘要的结构（跨章连贯的载体：后续章节靠它记人设状态、已回收伏笔与悬念）
SUMMARY_PROMPT = """请为下面这一章写「结构化摘要」，供后续章节保持连贯使用。

【本章（第{no}章《{title}》）正文】
{content}

严格按下面五节输出，每节 2-4 行，只写事实、不评论：
【关键情节】本章发生了什么（按顺序，含关键数字/物件）
【出场人物】谁出场了、做了什么
【人物状态变化】关系/立场/资源/情绪的变化
【遗留悬念】还没解决、后文要回收的线索
【衔接提示】下一章开头的自然切入点
只输出这五节，不要标题以外的解释。"""

# 伏笔台账：让模型直接给出"更新后的完整账本"，平台整段替换（简单、不易漏）
FORESHADOW_PROMPT = """请更新这本书的「待回收伏笔账本」。

【当前账本】
{current}

【本章（第{no}章《{title}》）正文】
{content}

规则：
1. 本章新埋下的、以后要用到的线索 → 追加进账本；
2. 本章已经明确回收/解答的线索 → 从账本中删除；
3. 只保留「仍未回收」的条目；纯氛围描写、已当场解释清楚的不算伏笔；
4. 拿不准的一律保留（宁可留着，也别误删）。
输出格式：每行一条，形如 `- （第N章）一句话写清线索内容`；没有条目就输出 `（暂无）`。
只输出账本本身，不要任何解释。"""


# ---------------------------------------------------------------- 组装

def artifacts_of(book_id: str) -> Dict[str, Dict[str, Any]]:
    return {a["key"]: a for a in store.query(
        "SELECT * FROM artifacts WHERE book_id=?", [book_id])}


# 需要按受众追加赛道模块的步骤：章节级全部 + 选题 + 黄金三章
TRACK_STEPS = ("topic", "golden3")

# 受众错配的模块：男频书不许吃女频指令，反之亦然（实测：男频书的黄金三章抽到过女频开局模板）
WRONG_TRACK = {"male": ("02c",), "female": ("02b",), "none": ("02b", "02c")}


def modules_for(step: Dict[str, Any], book: Dict[str, Any]) -> List[str]:
    """步骤声明的模块 + 按受众追加赛道模块（章节级步骤 + 选题/黄金三章）。"""
    mods = list(step.get("modules") or [])
    track = TRACK_MODULES.get(book.get("audience") or "")
    if track and (step.get("stage") == "chapter" or step["id"] in TRACK_STEPS) \
            and track[:3] not in [m[:3] for m in mods]:
        mods.append(track[:3])
    return mods


def exclude_modules(book: Dict[str, Any]) -> List[str]:
    return list(WRONG_TRACK.get(book.get("audience") or "", ()))


def avoid_terms(book: Dict[str, Any]) -> List[str]:
    """节级过滤词：通用模块里也混着按受众写的节（如 03 里的《女频…开局模板》）。"""
    aud = book.get("audience") or ""
    return ["女频"] if aud == "male" else (["男频"] if aud == "female" else [])


def _prev_summaries(book: Dict[str, Any], before_no: int, window: int = CONTEXT_SUMMARY_CHAPTERS) -> str:
    """之前各章的结构化摘要（最近 window 章逐章给；更早的只报数量，省 token）。"""
    rows = [c for c in store.query(
        "SELECT no, title, summary, content FROM chapters WHERE book_id=? AND no<? ORDER BY no",
        [book["id"], before_no])]
    if not rows:
        return ""
    recent = rows[-window:]
    parts = []
    if len(rows) > len(recent):
        parts.append(f"（更早的 {len(rows) - len(recent)} 章摘要已省略；如需回溯请看章节列表）")
    for c in recent:
        s = (c.get("summary") or "").strip() or textkit.tail(c.get("content") or "", 200)
        parts.append(f"第{c['no']}章《{c.get('title') or ''}》\n{s}")
    return "\n\n".join(parts)


# 按流程告诉模型"这本书是什么体量"——否则它会按通用爆款逻辑出题，
# 长篇拿到的却是短平快的短剧式标题与爽点（2026-10-12 用户提问暴露的缺口）。
FLOW_BRIEF = {
    "long_novel": "长篇连载（番茄长篇通常 100 万字以上、数百章）。产出必须服务长线："
                  "主线要能撑住几百章、单元节奏清晰、有可持续的升级/扩张/势力迭代空间；"
                  "只靠一个开篇反转撑不住长篇。",
    "mid_novel": "完本中篇（番茄「完本签约模式」：作品须**完本**且达到 **15 或 30 万字**才可申请签约；"
                 "签约前不对读者展示、签约后内容**不可修改或删除**；每部作品**仅 3 次申请机会**；"
                 "审核为整书一次性审核，走完结审核 → 安全审核 → 质量评估三关）。"
                 "**本书体量一律以「本书资料」里写明的完本线为准**（步骤里出现的章数只作参考，不得覆盖本书完本线）。"
                 "产出必须服务**一次收干净**：五幕闭环、主线在明确章号收束、"
                 "所有伏笔在完本前回收；禁止为凑字数注水、禁止支线膨胀、禁止「越写越大」的连载式扩张。",
    "short_story": "短篇（8000–25000 字、五章结构，见手册 06 模块）。产出要服务五章闭环："
                   "节奏快、冲突密度高、结局要闭环。",
    "fanqie_short": "番茄短故事（单篇完结，正文 6000–30000 字、五章；官方发布页写明："
                    "发表超 6000 字即有机会签约）。产出要服务单篇闭环：开篇 300 字内进冲突、"
                    "五章走完 困境→压迫→反击→高潮→收束，结局必须收干净，不留续集尾巴。",
}


# 按流程给"输出要求"加上体量与长线约束。
# 为什么要写在这里：手册原文（选题指令）本身是"爆款流量取向"的，光在资料区写一句"本书是长篇"
# 会被手册的强取向压过去（实测：连跑两次都出短剧式标题）。把要求写进**输出要求**才管得住。
FLOW_OUTPUT_REQ = {
    "long_novel": "★ 本书是长篇连载（番茄长篇通常 100 万字以上、数百章）。每个候选必须附一行"
                  "【长线潜力】：①开局的可持续升级点（主角变强/扩张/身份迭代的方向）"
                  "②对手或势力阶梯（至少三层）③可支撑章数估计。"
                  "撑不到 200 章、或靠一次性反转/身份揭示撑场的方案，一律不要输出。",
    "short_story": "★ 本书是短篇（8000–25000 字、五章结构）：每个候选必须说明五章各自的冲突与结局闭环。",
    "fanqie_short": "★ 本书是**番茄短故事**（单篇完结，正文 6000–20000 字，五章）：每个候选必须说明"
                    "①五章各自的冲突与结局闭环 ②这篇为什么适合所选作品分类（分类决定读者预期）"
                    "③开篇第一句如何直接进冲突（短故事没有铺垫余地）④全篇预计字数（不得低于 6000）。",
    "mid_novel": "★ 本书是**完本中篇**（约 20 万字 / 100 章，一次性完本；番茄完本签约模式下签约后不可修改）。"
                 "每个候选必须附一行【完本可行性】：①这条主线能否在 100 章内收干净（在第几章收束、"
                 "结局是什么）②中段（第 30–70 章）的持续张力从哪来（**不许靠「越写越大」续命**）"
                 "③一句话说清本方案的**差异化创新点**（官方明确：达到基础质量不代表能签约，"
                 "选择热门题材也不足以决定签约结果）。"
                 "撑不到 100 章、或必须拖到 30 万字以上才能自洽的方案，一律不要输出。",
}

# 步骤专属的追加要求（写在输出要求里）
# 标签 → 情节硬要求（只写最容易"选了但没落地"的那些；模型看到具体情节要求才会真写）
_TAG_RULES = {
    "追妻火葬场": "前 3 章女主被反复伤害、最终决意离开；第 4–5 章男主必须真切追悔（付出实际代价、当众或当面认错），"
                  "女主**不能轻易回头**（拒绝复合或开放式结局）——「追」与「火葬场」两段都要写出来。",
    "追夫火葬场": "前 3 章男主被反复伤害、决意离开；第 4–5 章女主追悔、付出代价挽回，男主不轻易原谅。",
    "真假千金": "必须写出身份错位：假千金占位、真千金被亏待；中段身份揭露，结尾真千金拿回属于自己的东西，假千金付出代价。",
    "先婚后爱": "先契约/被迫结婚（前 2 章），中段相处生情，结尾情感确认；不能一上来就相爱。",
    "打脸逆袭": "前期至少两次被当众轻视/羞辱，后期必须有**公开场合**的打脸场面（对方失势/认错），让读者解气。",
    "破镜重圆": "先分开（误会或伤害），中段重逢并解开误会，结尾复合；复合前必须有一次坦诚对话。",
    "系统": "金手指规则要写清楚（触发条件、奖励、代价），并且规则要影响关键选择，不能只是背景设定。",
    "金手指": "能力必须有**限制或代价**，主角靠它解决问题但不能万能，否则爽感崩。",
    "大女主": "关键冲突由女主自己判断、自己出手解决（收集证据/谈判/反击），**不能靠男主或男性角色救场**。",
    "重生": "带着前世记忆重生，前期用记忆规避灾祸，中期改变关键节点，结尾改变命运并与前世仇人清算。",
    "穿越": "现代人进入古代/书中世界，用现代知识或信息差解决问题，且要写出与时代规则的碰撞。",
    "暗恋": "长期隐忍不说的暗恋，中段有曝光或误会，结尾表白或释怀；要有「想说不敢说」的细节。",
    "赘婿": "上门女婿被岳家轻视、被要求签不平等协议，后期翻身，结尾让轻视他的人付出代价。",
    "替身": "被当成替身（白月光/前任），中段真相揭露，结尾反转——被替身者才是真正被选择的人。",
    "婆媳": "婆媳冲突要具体（钱、房、带孩子、话语权），不能只吵架；结尾要有明确输赢或边界划定。",
    "凤凰男": "男方或男方家庭吸血（要钱、要房、要资源），女主/主角一步步识破并止损。",
    "白月光": "男主心里有别人，前期女主被比较/被牺牲，中段白月光登场，结尾男主付出代价。",
    "豪门世家": "家族内部的利益斗争（继承权、联姻、股份），主角在斗争中站住脚。",
    "甜宠": "高密度甜互动（每章至少两处），冲突轻、误会快速解开，结尾圆满。",
    "虐文": "情绪要压到底：误解、牺牲、背叛至少两轮，结尾可以悲或苦尽甘来。",
    "爽文": "节奏快、打脸密集（每章至少一个爽点），反派要被打得干脆。",
}


STEP_OUTPUT_REQ = {
    "outline": "★ 长篇大纲必须写全：主线目标的三次以上迭代、对手/势力阶梯（至少三层）、"
               "单元节奏（每个单元多少章 + 单元内爽点密度）、长线伏笔表（埋点 → 预计回收章）。"
               "★ 沿用《人设档案》里已锁定的角色设定，**不要改动已有角色的年龄/身份/结局**；"
               "若大纲新增了命名角色，末尾单列【新增角色表】（姓名 / 定位 / 首次登场章 / 一句话性格），"
               "便于回补人设档案——后文会反复出现的人必须进档案。",
    "setup": "★ 三份设定表要填空式给出，能被后续五章直接复用（不要写成创作建议）。",
    # 实测事故：第 5 章改写从 4401 字写成 12115 字——模型在「改写」里续写了新情节，
    # 被字数保护拦下，这一章就停在草稿。改写只该动表达，不该动情节与篇幅。
    "polish": "★ 这是**改写**任务，不是续写：只优化表达（去 AI 味、换说法、补细节质感），"
              "**不得新增情节、不得新增场景/人物、不得把本章写成下一章**；"
              "输出篇幅必须与原文相近（0.85–1.2 倍），明显变长视为跑偏。",
    "characters": "★ 这是要撑几百章的**全员人设档案**，不是主角单人卡。必须分类覆盖："
                  "①主角（含成长弧线与弱点）②核心配角 3-4 位（各写清立场、出场阶段、与主角关系变化）"
                  "③对手/反派阶梯至少三层，每层 1-2 位代表（写清他们怎么一步步变强/变坏）"
                  "④规则性角色 1-2 位（如直播系统的判官/秘境守卫这类非人类角色）。"
                  "每位都要给：基础信息（姓名/年龄/身份/外貌一句话）· 核心性格 · 说话风格（附 1 句例句）"
                  "· 绝对不做的事 · 专属小动作 · 结局走向（大致）。"
                  "凡是后文会反复出现的人，这里必须锁死；宁多勿漏。"
                  "★ **取名避坑**（平台实测教训）：主角名**不要用番茄高频名**"
                  "（林渊/林辰/叶辰/江辰/苏辰/周野/李航/陈默 这类已有数十本同名），"
                  "也不要撞知名角色的名字；优先 2-3 字、有辨识度、不与本书同赛道作品重名的名字。"
                  "每位角色名后用一行标注【撞名自评】：常见度（低/中/高）+ 是否与已知作品重名。",
    "title_intro": "★ **书名硬规则（番茄官方，必须遵守）**：含标点在内**最多 15 字**；只允许中英文、阿拉伯数字"
                   "与中文标点（叹号、逗号、问号、中括号、冒号）——**英文标点会报错**；每个候选后面必须标注"
                   "【字数：N/15】。"
                   "书名候选给 6-8 个，**必须分别套用不同的书名公式**（至少覆盖：身份反差+金手指+极端结果 / "
                   "数字+设定+爽点 / 马甲曝光+身份+震惊全场 / 境遇+打脸逆袭），不要全是同一种句式；"
                   "每个候选后面用一行标注【套用公式】与【撞车风险自评】（该句式是否是番茄近期常见套话，"
                   "常见则标「高」并给出一个更差异化的改写）。简介按手册的简介公式写（≤200 字），"
                   "前 3 句必须完成「处境 → 转折 → 爽点承诺」。",
    "golden3": "★ 黄金三章要能直接开写，不许只给「设计说明」："
               "①**第 1 章前 300 字的完整正文草稿**（可直接用的原稿，必须开门见冲突/异常，"
               "严禁大段背景铺垫、严禁先介绍世界观）；"
               "②三章各自给：本章冲突、爽点节点、章尾钩子的具体写法（每章给 3 个备选钩子句）；"
               "③三章内要交代的信息清单（控制在 5 条以内，其余留到后文）；"
               "④给出第 1 章第 1 段的三个不同开头方案（各 100 字左右）并说明各自的钩子逻辑。",
}


def flow_brief(book: Dict[str, Any]) -> str:
    return FLOW_BRIEF.get(book.get("flow_id") or "", "")


# 流程专属的步骤要求（优先级高于通用的 STEP_OUTPUT_REQ）
# 为什么需要：同样的步骤 id（outline / characters / golden3）在长篇与完本中篇里的
# 体量取向完全相反（长篇要"撑几百章"，中篇要"一次收干净"），通用条款会把中篇带偏。
STEP_OUTPUT_REQ_BY_FLOW: Dict[str, Dict[str, str]] = {
    "long_novel": {
        "chapter": "★ 本章篇幅：**目标 2500–3500 字，不得低于 2400 字、不得超过 4500 字**。"
                   "用具体场景、对话、动作与细节把「本章要点」写足；"
                   "不得注水、不得重复、不得大段抒情，也不要写任何复盘/自检/说明。"
                   "★ 只写这一章，不要越过本章要点写下一章的内容。",
    },
    "short_story": {
        "outline": "★ 这是**短篇（五章）大纲**（不是长篇，也不是中篇）：只规划 5 章，"
                   "全篇 8000–25000 字（每章 1500–3500 字）。"
                   "①第 1 章 300 字内进冲突，第 3 章必须有**一次实质性反击**，第 5 章收干净；"
                   "②每章写清：本章冲突、情绪节点、章尾钩子（给 3 个备选钩子句）；"
                   "③给一张**极简伏笔表**（伏笔内容｜埋点章｜回收章），全部在 5 章内回收；"
                   "④**不要**写几百章的长线结构、不要写对手势力阶梯、不要给续集留尾巴。",
    },
    "fanqie_short": {
        # 短故事的硬指标只有「整篇 6000–20000 字」；**不按章卡字数**，
        # 否则会出现「第 5 章 787 字」这种整篇够量却被拒的情况。
        "setup": "★ 必须严格按「本书资料」里写的**题材分类**来写：分类决定频道与套路"
                 "（例如「男频脑洞」要有脑洞设定/金手指/规则反转，不能写成婆媳婚姻家庭；"
                 "「婚姻家庭」才是婚恋婆媳题材）。实测事故：选了男频脑洞，设定表却写成上门女婿婆媳戏。"
                 "设定表的时代、身份、场景、冲突类型都要对上分类。",
        "draft": "★ 这是短故事的章节正文：**整篇目标 6000–30000 字，本章字数不限**"
                 "（由情节需要决定，1500–3500 字都正常）。只写这一章，不要越章；"
                 "章末必须留钩子（对话或悬念），不要写成总结。",
        "outline": "★ 这是**单篇完结的番茄短故事大纲**（不是长篇、也不是中篇）：只规划 **5 章**，"
                   "全篇 6000–30000 字（不限制每章字数，但五章要完整、节奏递进）。"
                   "①第 1 章 300 字内进冲突，第 3 章必须有**一次实质性反击**，第 5 章收干净；"
                   "②每章写清：本章冲突、情绪节点、章尾钩子（给 3 个备选钩子句）；"
                   "③给一张**极简伏笔表**（伏笔内容｜埋点章｜回收章），全部在 5 章内回收；"
                   "④**不要**写几百章的长线结构、不要写对手势力阶梯、不要给续集留尾巴；"
                   "⑤**人名、年龄、关系、关键金额必须严格沿用设定表，一个都不许改名**"
                   "（实测事故：设定表写「周明远」，大纲擅自改成「陈屿」，正文和设定表就此分裂）。"
                   "若必须新增角色，单列【新增角色：姓名/与主角关系/首次登场章】，且不得替换已有角色。",
    },
    "mid_novel": {
        "outline": "★ 这是**完本中篇大纲**（不是连载长篇大纲，别写成几百章规模）："
                   "①先按「本书资料」里写明的完本线换算出**总章数**，再给出五幕结构 + **每幕章数**"
                   "（各幕章数合计必须等于总章数；若本书资料未写章数，按 2200 字/章换算）；"
                   "②每幕写清：本幕冲突、转折点、幕末主角状态；"
                   "③明确**主线在第 N 章收束、结局是什么**（完结审核第一关就是「故事是否完整」，"
                   "烂尾直接不通过）；"
                   "④**必须输出「伏笔表」**（单独一张表，每行 =【伏笔内容｜埋点章｜**回收章**】）——"
                   "这是番茄完本签约「完结审核」的核心检查项，**不得省略、不得用文字描述代替表格**；"
                   "每一处伏笔都要在完本前回收，不留跨书级大坑；"
                   "⑤**不要**写「主线三次以上迭代」「势力阶梯三层」「可支撑几百章」这类连载要求；"
                   "⑥若大纲新增了《人设档案》里没有的角色，**末尾必须单列【新增角色表】**"
                   "（姓名 / 定位 / 首次登场章 / 一句话性格 / 结局），便于回补人设档案；后文会反复出现的人必须进表。"
                   "★ 沿用《人设档案》里已锁定的角色设定，不要改动已有角色的年龄/身份/结局。",
        "characters": "★ 这是**完本中篇**的人设档案（体量以「本书资料」里的完本线为准），**规模要克制**：主角 1 位（含成长弧线与最终结局）"
                      "+ 核心配角 3-5 位（各写立场、登场幕、与主角关系变化）+ 对手/反派 2-3 位（分层）"
                      "+ 规则性角色 0-1 位，**合计 6-10 位即可**，不要按几百章规模铺角色。"
                      "每位都要给：基础信息（姓名/年龄/身份/外貌一句话）· 核心性格 · 说话风格（附 1 句例句）"
                      "· 绝对不做的事 · 专属小动作 · 结局走向。**结局走向必填**（中篇要闭环）。"
                      "★ **取名避坑**（平台实测教训）：主角名**不要用番茄高频名**"
                      "（林渊/林辰/叶辰/江辰/苏辰/周野/李航/陈默 这类已有数十本同名），"
                      "也不要撞知名角色的名字；优先 2-3 字、有辨识度、不与本书同赛道作品重名的名字。"
                      "每位角色名后用一行标注【撞名自评】：常见度（低/中/高）+ 是否与已知作品重名。",
        "golden3": "★ 中篇的黄金三章要在前三章内**把核心设定 + 主角目标 + 主要对手的轮廓**交代完"                   "（中篇没有几十章的铺垫空间），但禁止大段背景说明，全部落到事件里。"
                   "其余要求同通用条款：①第 1 章前 300 字的**完整正文草稿**；②三章各自的冲突、爽点节点、"
                   "章尾钩子（每章 3 个备选钩子句）；③三章内要交代的信息清单（≤5 条）；"
                   "④第 1 章第 1 段的三个不同开头方案（各 100 字左右）+ 钩子逻辑说明。",
        "chapter": "★ 本章篇幅：目标 2100–2400 字，不得低于 1900 字、不得超过 2800 字。"
                   "用具体场景、对话、动作与细节把「本章要点」写足；"
                   "不得注水、不得重复、不得大段抒情，也不要写任何复盘/自检/说明。"
                   "★ 只写这一章，不要越过本章要点写下一章的内容。",
        "finish_audit": "★ 按番茄官方完本签约的**三次审核**逐层过，输出必须能被直接拿去改稿："
                        "【第一关 · 完结审核】故事内容是否完整？主线有没有收干净？有无烂尾/挖坑不填？"
                        "（官方：不通过则无法进入下一关。失败后需间隔 3 天才能重新申请。）"
                        "【第二关 · 安全审核】逐一列出可能触发违禁词、低俗色情、违反公序良俗、"
                        "侵权抄袭嫌疑的具体章节与原文片段。（官方：失败后需间隔 7 天才能重新申请。）"
                        "【第三关 · 质量评估】针对「粗制滥造 / 逻辑混乱 / 结构失常 / 内容空洞或刻意注水」"
                        "逐项给证据（引原文）；再按官方优质内容四标准逐条打分（各 10 分）："
                        "①创新性（题材切入、开篇事件或人物设定是否具有创新性、能否快速激发读者好奇）"
                        "②立意（三观正向、立意深刻、自然融入故事）"
                        "③文笔（清晰流畅、准确生动）"
                        "④结构完整度与长期发展潜力（主线清晰、节奏稳定）。"
                        "★ 最后给【总结论：通过 / 需修改 / 不通过】+ 优先修改的 3 件事（按影响排序）。"
                        "★ 提醒：本平台每部完本作品只有 3 次签约申请机会，这一关是为那 3 次机会做预演，"
                        "宁可现在苛刻。",
    },
}


AUDIT_STEPS = ("scan", "submit_check", "finish_audit", "presubmit", "compliance")


def output_req(book: Dict[str, Any], step: Dict[str, Any]) -> str:
    """输出要求的追加部分：流程体量 + 步骤专属（流程专属优先于通用）。"""
    parts = []
    if step.get("stage") == "book":
        req = FLOW_OUTPUT_REQ.get(book.get("flow_id") or "")
        if req:
            parts.append(req)
    by_flow = STEP_OUTPUT_REQ_BY_FLOW.get(book.get("flow_id") or "") or {}
    extra = by_flow.get(step["id"]) or STEP_OUTPUT_REQ.get(step["id"])
    if extra:
        parts.append(extra)
    # 核验类步骤：实测 submit_check 会把「核验」写成小说续写（输出 1.4 万字正文），
    # 这里加硬约束，只让模型出结论。
    if step.get("id") in AUDIT_STEPS:
        parts.append("★ 本步是检查/核验：**只输出结论**（清单、表格、问题列表）。"
                     "禁止写正文、禁止续写故事、禁止给小说片段；发现问题只写「位置 + 原文摘录 + 修改建议」。")
    return ("\n" + "\n".join(parts)) if parts else ""


def build_prompt(book: Dict[str, Any], step: Dict[str, Any], *,
                 chapter: Optional[Dict[str, Any]] = None,
                 prev_chapter: Optional[Dict[str, Any]] = None,
                 extra: str = "") -> Tuple[str, List[Dict[str, Any]]]:
    """返回 (提示词, 引用的 skill 原文块)。"""
    arts = artifacts_of(book["id"])
    mods = modules_for(step, book)
    # 作品标签（追妻火葬场/真假千金…）也参与知识库检索：
    # 否则带「追妻火葬场」的书，大纲也只会抽到通用的「节奏/冲突」节，抽不到追妻专章。
    _kws = list(step.get("keywords") or [])
    try:
        import json as _json2
        _r2 = store.query_one("SELECT sub_categories FROM short_meta WHERE book_id=?", [book["id"]])
        for _t in (_json2.loads((_r2 or {}).get("sub_categories") or "[]") or []):
            if _t and _t not in _kws:
                _kws.append(str(_t))
    except Exception:  # noqa: BLE001
        pass
    used = kb_blocks.select_blocks(mods, _kws,
                                   limit=step.get("block_limit"),
                                   exclude=exclude_modules(book),
                                   avoid_terms=avoid_terms(book))

    parts: List[str] = [f"# 任务：{step['no']}. {step['name']}\n{step['goal']}"]

    # 短故事的作品标签（二级分类）必须进提示词——否则界面上选了，生成时完全没用上。
    tag_line = ""
    try:
        import json as _json
        _row = store.query_one("SELECT sub_categories FROM short_meta WHERE book_id=?", [book["id"]])
        _subs = _json.loads((_row or {}).get("sub_categories") or "[]")
        if _subs:
            tag_line = "｜作品标签：" + "、".join(str(x) for x in _subs)
    except Exception:  # noqa: BLE001
        tag_line = ""

    parts.append("## 本书资料\n"
                 f"- 书名：{book.get('name') or '（未填）'}｜题材：{book.get('genre') or '（未填）'}"
                 f"｜受众：{AUDIENCE_LABEL.get(book.get('audience') or '', '不限')}"
                 f"｜平台：{book.get('platform') or '番茄小说'}{tag_line}\n"
                 f"- 一句话设定：{book.get('brief') or '（未填）'}")
    if tag_line:
        parts.append("## 标签要求\n"
                     f"- 本书标注的作品标签：{tag_line.replace('｜作品标签：', '')}。"
                     "设定、大纲与正文都要能体现这些标签的卖点"
                     "（例如「追妻火葬场」要有先伤害后追悔的反差，「真假千金」要有身份错位），"
                     "不要只在资料里写、正文里不落地。")
        _rules = [v for k, v in _TAG_RULES.items() if k in tag_line]
        if _rules:
            parts.append("## 标签情节硬要求\n" + "\n".join(f"- {r}" for r in _rules))
    fb = flow_brief(book)
    if fb:
        parts.append(f"## 体裁与体量（本步产出必须匹配）\n- 本书流程："
                     f"{(definitions.get_flow(book['flow_id']) or {}).get('name') or book['flow_id']}\n- {fb}")

    given = []
    for key in step.get("inputs") or []:
        a = arts.get(key)
        if a and (a.get("content") or "").strip():
            s = next((x for x in (definitions.get_flow(book["flow_id"]) or {}).get("steps", [])
                      if x["id"] == key), None)
            given.append(f"【{s['name'] if s else key}】\n{a['content'].strip()}")
    if given:
        parts.append("## 已确定的设定与前序产出\n" + "\n\n".join(given))

    # 全书正文注入：步骤声明 inputs 里带 "__manuscript__" 时，把完本稿全部塞进提示词。
    # 存在的理由：平台原有的"全书级步骤"只带资料+前序产出，**读不到正文** ——
    # 完本三审预检/投稿前自查这类步骤因此只能"看大纲说话"（实测 GLM 还据此凭空造出假矛盾）。
    # 24 万字 ≈ 16–20 万 tokens，需要配大上下文模型（kimi-k3 1M / qwen3.8-max 1M / v4-pro 384K）。
    if "__manuscript__" in (step.get("inputs") or []):
        rows = store.query("SELECT no, title, content FROM chapters WHERE book_id=? ORDER BY no",
                           [book["id"]])
        chs = [r for r in rows if (r.get("content") or "").strip()]
        md = "\n\n".join(f"第{r['no']}章 {r.get('title') or ''}\n{(r.get('content') or '').strip()}"
                         for r in chs)
        parts.append(f"## 全书正文（完本稿，共 {len(chs)} 章 / {len(md)} 字，逐章排列）\n"
                     f"★ 请**逐章核对下面这份正文**再下结论，不要只依据上面的大纲与设定推测。\n\n{md}")

    # 大纲/正文类步骤：把设定表里锁定的人名和关键数字显式列出来。
    # 实测两次「设定表写周明远、大纲改陈屿」「设定表写林晓、大纲改那敏」——只靠一句
    # "沿用设定表"模型会忽略，必须在提示词里把名字直接摆出来。
    if step.get("id") in ("outline", "draft", "chapter", "golden3"):
        setup_txt = (arts.get("setup") or {}).get("content") or ""
        cast = extract_cast(setup_txt)
        if cast.get("names"):
            parts.append("## 锁定的人名（必须完全沿用，一个字都不许改）\n" + "、".join(cast["names"]))
        if cast.get("amounts"):
            parts.append("## 锁定的关键数字（不得改动）\n" + "、".join(cast["amounts"][:10]))

    if used:
        body = "\n\n".join(f"—《{b['module']}》· {b['heading']}\n{b['text']}" for b in used)
        parts.append("## 番茄写作手册 · 本步依据（原文指令，照其执行）\n" + body)

    if chapter:
        info = [f"## 本次要写的内容\n- 第 {chapter['no']} 章《{chapter.get('title') or ''}》"
                f"\n- 本章要点：{chapter.get('brief') or '（未填，按大纲推进）'}"]
        # 跨章连贯（只对"写新正文"的步骤注入；改写类步骤省 token）
        if step["id"] in DRAFT_STEPS:
            prev = _prev_summaries(book, int(chapter["no"]))
            if prev:
                info.append("## 前情摘要（按章顺序，用于保持人设/时间线/伏笔一致）\n" + prev)
        last = (prev_chapter or {}).get("content") or ""
        if last:
            info.append(f"- 上一章结尾节选（承接用，不要重复它写过的情节）：\n{textkit.tail(last, CONTEXT_TAIL_CHARS)}")
        if (chapter.get("content") or "").strip() and step["id"] in ("deai", "polish", "hook", "ooc_fix", "deai_fix"):
            info.append(f"- 本章现有正文（本次在此基础上改写，保留剧情与人设）：\n{chapter['content'].strip()}")
        parts.append("\n".join(info))

    # 伏笔台账：起草时提醒推进/回收；改写时不需要
    if step["id"] in DRAFT_STEPS:
        fh = (book.get("foreshadows") or "").strip()
        parts.append("## 待回收伏笔账本（★本章若有合适时机就推进或回收某条；"
                     "若本章回收了，正文里要写清怎么落实的，别用一句「原来如此」带过）\n"
                     + (fh or "（暂无待回收伏笔）"))

    extra = (extra or "").strip()[:PROMPT_EXTRA_MAX_CHARS]
    lock_hint = ""
    art_before = store.query_one("SELECT locked FROM artifacts WHERE book_id=? AND key=?",
                                 [book["id"], step["id"]]) or {}
    if int(art_before.get("locked") or 0):
        # 产出已锁定 ⇒ 本次生成不会覆盖它。必须提前说清"只输出新增/修改部分"，
        # 否则模型会把已有内容整段重写一遍，人工并入时就会撞出互相矛盾的重复卡片（踩过）。
        lock_hint = ("\n（注意：本步产出已锁定，本次结果**不会覆盖**已存内容。"
                     "若你的意图是补充或局部修改，**只输出新增/需要替换的部分**，"
                     "不要重复已有内容；已有内容以锁定版为准。）")
    parts.append("## 补充要求\n" + (extra or "（无）") + lock_hint)

    # 改写类步骤（去AI味/润色/钩子）额外约束：必须是完整稿，不许"给建议"或压缩篇幅。
    # 实测教训：GLM 强制思考时容易只回一段"改写说明"或大段删减（3178 字 → 883 字）。
    if step["id"] in ("deai", "polish", "hook", "ooc_fix", "deai_fix"):
        # ⚠️ 这里**绝不能写"字数必须落在 85%~120%"之类的数字约束**：实测（GLM-5.3 Flash）
        # 会让模型真的去逐段数字数，把输出预算烧在加法上，正文根本没写完就被截断。
        # 篇幅交给平台的字数保护去判，提示词只管"别增删情节"。
        parts.append("## 输出要求\n"
                     "直接输出**改写后的完整正文**，第一行就是正文本身。\n"
                     "硬性要求：①保留全部情节、信息量与人物动作，对白不删，不新增情节；"
                     "②篇幅与原文基本一致（**不要统计字数、不要做任何计算、不要输出数字**）；"
                     "③**不要输出任何规划、说明、分析、对比、大纲、清单；不要使用英文**；"
                     "④不要标题行、不要 markdown 围栏。\n"
                     "如果你习惯先思考：思考请全部在内部完成，回复的第一行必须是正文。")
        return "\n\n".join(parts), used

    is_chapter_step = step.get("stage") == "chapter"
    if is_chapter_step:
        # ⚠️ 篇幅类要求只给「写新正文」的步骤（chapter/draft）。**绝不能给改写类步骤**——
        # 那会诱导模型去逐段数字数、把输出预算烧在加法上（详见上方 deai 分支的警告）。
        tail = ("## 输出要求\n只输出本章正文：不要写章节标题行、不要写「第X章」、"
                "不要任何解释或复盘、不要 markdown 围栏；直接从正文第一句开始。"
                + (output_req(book, step) if step["id"] in DRAFT_STEPS else ""))
    else:
        tail = (f"## 输出要求\n只输出「{step['name']}」的成品内容本身（可直接落库使用），"
                "不要解释过程、不要复盘、不要 markdown 围栏。"
                + output_req(book, step))
    parts.append(tail)
    return "\n\n".join(parts), used


# ---------------------------------------------------------------- 模型选择

def pick_model(model_id: str = "") -> Optional[Dict[str, Any]]:
    if model_id:
        m = store.query_one("SELECT * FROM models WHERE id=? AND enabled=1", [model_id])
        if m:
            return m
    return (store.query_one("SELECT * FROM models WHERE enabled=1 AND is_default=1")
            or store.query_one("SELECT * FROM models WHERE enabled=1 AND channel='api' "
                               "ORDER BY created_at LIMIT 1")
            or store.query_one("SELECT * FROM models WHERE enabled=1 LIMIT 1"))


def step_model_id(book: Dict[str, Any], step_id: str) -> str:
    """本书该步骤绑定的默认模型 id（没绑则空）。"""
    m = store.jloads((book or {}).get("step_models"), {}) or {}
    return (m.get(step_id) or "").strip()


def bind_step_model(book_id: str, step_id: str, model_id: str) -> Dict[str, Any]:
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    if not book:
        raise LookupError("书不存在")
    m = store.jloads(book.get("step_models"), {}) or {}
    if model_id:
        m[step_id] = model_id
    else:
        m.pop(step_id, None)
    store.update("books", book_id,
                 {"step_models": json.dumps(m, ensure_ascii=False), "updated_at": store.now()})
    return m


def resolve_model_for_step(book: Dict[str, Any], step_id: str,
                           model_id: str = "") -> Optional[Dict[str, Any]]:
    """模型选择顺序：本次指定 → 本书该步骤的默认 → 全局默认。"""
    return pick_model(model_id or step_model_id(book, step_id))


def _internal_model(book: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """内部步骤（章节摘要 / 伏笔台账）的模型：优先自己的绑定，其次章节步骤，最后全局默认。

    为什么单独写：这两个步骤不是流程步骤，早期实现直接按 "chapter" 取模型，
    结果「一键绑定短故事模型」写进 summary / foreshadow 的绑定永远不生效，
    会退到全局默认（没设默认时就是第一个启用的模型，常常是最贵的那个）。
    """
    mid = step_model_id(book, key)
    return pick_model(mid) if mid else resolve_model_for_step(book, "chapter")


# ---------------------------------------------------------------- 并发锁 / 重试

STALE_MINUTES = 45   # running 超过这个时长视为僵尸（服务异常/任务丢失）


def running_gens(chapter_id: str = "", book_id: str = "") -> List[Dict[str, Any]]:
    if chapter_id:
        rows = store.query("SELECT * FROM gens WHERE chapter_id=? AND status='running'", [chapter_id])
    elif book_id:
        rows = store.query("SELECT * FROM gens WHERE book_id=? AND status='running'", [book_id])
    else:
        rows = store.query("SELECT * FROM gens WHERE status='running'")
    # 僵尸过滤：实测有 prompt 为空、0 字、卡 running 20 分钟的记录，会把整本书锁死
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(minutes=STALE_MINUTES)).isoformat(timespec="seconds")
    live = []
    for r in rows:
        if (r.get("started_at") or "") >= cutoff:
            live.append(r)
        else:
            store.update_safe("gens", r["id"], {"status": "failed", "finished_at": store.now(),
                                                "error": f"运行超过 {STALE_MINUTES} 分钟，判定为僵尸任务"})
    return live


def chapter_busy(chapter_id: str) -> bool:
    """本章是否有**正文级**生成任务在跑（防止重复点击把同一章烧两遍）。

    注意排除 summary_foreshadow：它是成稿后的后台重算（只写 summary/foreshadows，
    不碰正文）。实测它会先占住第 1 章，导致逐章改写轮到第 1 章时被拒，
    那一章就永远停在 draft（日志里只有第 2-5 章的 polish 记录）。
    """
    if any(g.get("step_id") != "summary_foreshadow" for g in running_gens(chapter_id=chapter_id)):
        return True
    rows = store.query("SELECT id FROM tasks WHERE chapter_id=? AND status='running'", [chapter_id])
    return bool(rows)


def clear_stale_running() -> int:
    """服务重启后清理僵尸：running 的生成/任务标记为中断（否则会一直"正在跑"）。"""
    n = len(store.query("SELECT id FROM gens WHERE status='running'")) + \
        len(store.query("SELECT id FROM tasks WHERE status='running'"))
    store.execute("UPDATE gens SET status='failed', error='服务重启，任务中断', finished_at=? "
                  "WHERE status='running'", [store.now()])
    store.execute("UPDATE tasks SET status='failed', error='服务重启，任务中断', finished_at=? "
                  "WHERE status='running'", [store.now()])
    return n


def retry_generation(gid: str) -> Dict[str, Any]:
    """按原提示词重试（不改提示词、不重新组装，保证可比）。

    流程步骤（chapter / deai / outline …）由 `_run` 落库；
    内部步骤（summary / foreshadow，不在流程定义里）单独处理，否则会误写章节正文。
    """
    g = store.query_one("SELECT * FROM gens WHERE id=?", [gid])
    if not g:
        raise LookupError("生成记录不存在")
    kind = g.get("kind") or g.get("step_id") or ""
    book = store.query_one("SELECT * FROM books WHERE id=?", [g["book_id"]])
    chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [g["chapter_id"]]) if g.get("chapter_id") else None
    model = pick_model_by_name_or_id(g.get("model_name"), kind, g.get("book_id"))
    ngid = store.new_id("gen")
    base = {"id": ngid, "book_id": g["book_id"], "step_id": g["step_id"],
            "chapter_id": g.get("chapter_id") or "", "kind": kind,
            "model_name": (model or {}).get("name") or "（未配置模型）",
            "prompt": g.get("prompt") or "", "status": "running", "started_at": store.now()}

    if kind in ("summary", "foreshadow"):
        if not model or not book or not chapter:
            store.insert("gens", {**base, "status": "failed", "finished_at": store.now(),
                                  "error": "无法重试：模型/书/章节缺失"})
            return {"ok": False, "gen_id": ngid, "error": "无法重试：模型/书/章节缺失"}
        store.insert("gens", base)
        RUNNING[ngid] = True
        spawn(_retry_internal(ngid, kind, model, base["prompt"], book, chapter))
        return {"ok": True, "gen_id": ngid, "model": model.get("name"), "kind": kind}

    step = definitions.get_step(book["flow_id"], g["step_id"]) if book else None
    if not model or not step or not book:
        store.insert("gens", {**base, "status": "failed", "finished_at": store.now(),
                              "error": "无法重试：模型/步骤/书缺失"})
        return {"ok": False, "gen_id": ngid, "error": "无法重试：模型/步骤/书缺失"}
    store.insert("gens", base)
    RUNNING[ngid] = True
    spawn(_run(ngid, model, base["prompt"], book, step, chapter))
    return {"ok": True, "gen_id": ngid, "model": model.get("name")}


async def _retry_internal(gid: str, kind: str, model: Dict[str, Any], prompt: str,
                          book: Dict[str, Any], chapter: Dict[str, Any]) -> None:
    out, err = await _llm_once(model, prompt, gid)
    if not out:
        return
    if kind == "summary":
        store.update_safe("chapters", chapter["id"], {"summary": out, "updated_at": store.now()})
    else:
        store.update_safe("books", book["id"], {"foreshadows": out, "updated_at": store.now()})
    store.update_safe("gens", gid, {"error": f"[note] 已写回{'章节摘要' if kind == 'summary' else '伏笔台账'}"})
    RUNNING.pop(gid, None)


def pick_model_by_name_or_id(name: str, step_id: str = "", book_id: str = "") -> Optional[Dict[str, Any]]:
    m = store.query_one("SELECT * FROM models WHERE name=? AND enabled=1", [name or ""])
    if m:
        return m
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id]) if book_id else None
    return resolve_model_for_step(book or {}, step_id)


# ---------------------------------------------------------------- 摘要 / 伏笔

async def _llm_once(model: Optional[Dict[str, Any]], prompt: str, gid: str) -> Tuple[str, str]:
    """调一次模型并写 gen 记录；返回 (输出, 错误)。"""
    if not model:
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": "没有可用的模型，请先在「模型」页添加"})
        return "", "没有可用的模型"
    try:
        # 摘要/伏笔是短任务：超时设 300 秒，避免异常时长期卡在 running（会锁住整本书）
        out, meta = await asyncio.wait_for(
            asyncio.to_thread(llm.chat, model, prompt, SYSTEM), timeout=300)
    except asyncio.TimeoutError:
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": "超过 300 秒未结束，已中断"})
        return "", "超时中断"
    except Exception as exc:  # noqa: BLE001
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": str(exc)[:500]})
        return "", str(exc)[:200]
    out = textkit.strip_fences(out)
    _u = (meta or {}).get("usage") or {}
    _pt = int(_u.get("prompt_tokens") or 0)
    _ct = int(_u.get("completion_tokens") or 0)
    _ca = int(_u.get("cache_tokens") or 0)
    store.update_safe("gens", gid, {
        "status": "success", "output": out, "chars": len(out),
        "prompt_tokens": _pt, "completion_tokens": _ct, "cache_tokens": _ca,
        "cost": estimate_cost((model or {}).get("model_name") or "", _pt, _ct, _ca),
        "finished_at": store.now()})
    return out, ""


def _gen_row(book_id: str, step_id: str, kind: str, model: Optional[Dict[str, Any]],
             prompt: str, chapter_id: str = "") -> str:
    gid = store.new_id("gen")
    store.insert("gens", {"id": gid, "book_id": book_id, "step_id": step_id,
                          "chapter_id": chapter_id, "kind": kind,
                          "model_name": (model or {}).get("name") or "（未配置模型）",
                          "prompt": prompt, "status": "running", "started_at": store.now()})
    return gid


async def summarize_chapter(book: Dict[str, Any], chapter: Dict[str, Any]) -> Dict[str, Any]:
    """给一章生成结构化摘要（跨章连贯的载体），存进 chapters.summary。"""
    model = _internal_model(book, "summary")
    prompt = SUMMARY_PROMPT.format(no=chapter["no"], title=chapter.get("title") or "",
                                   content=(chapter.get("content") or "").strip())
    gid = _gen_row(book["id"], "summary", "summary", model, prompt, chapter["id"])
    out, err = await _llm_once(model, prompt, gid)
    if out:
        store.update("chapters", chapter["id"], {"summary": out, "updated_at": store.now()})
    return {"ok": bool(out), "gen_id": gid, "chars": len(out), "error": err}


async def update_foreshadows(book: Dict[str, Any], chapter: Dict[str, Any]) -> Dict[str, Any]:
    """用本章正文更新「待回收伏笔账本」（模型直接给完整账本，平台整段替换）。"""
    model = _internal_model(book, "foreshadow")
    prompt = FORESHADOW_PROMPT.format(current=(book.get("foreshadows") or "").strip() or "（空）",
                                     no=chapter["no"], title=chapter.get("title") or "",
                                     content=(chapter.get("content") or "").strip())
    gid = _gen_row(book["id"], "foreshadow", "foreshadow", model, prompt, chapter["id"])
    out, err = await _llm_once(model, prompt, gid)
    if out:
        store.update("books", book["id"], {"foreshadows": out, "updated_at": store.now()})
    return {"ok": bool(out), "gen_id": gid, "chars": len(out), "error": err}


# ---------------------------------------------------------------- 一键写完本章

def chapter_step_id(flow_id: str, kind: str) -> str:
    """按角色找章节级步骤 id：draft → chapter/draft；deai → deai/polish/hook/deai_fix。"""
    want = (["chapter", "draft"] if kind == "draft"
            else ["deai", "polish", "hook", "deai_fix", "ooc_fix"])
    flow = definitions.get_flow(flow_id) or {}
    for s in flow.get("steps", []):
        if s.get("stage") == "chapter" and s["id"] in want:
            return s["id"]
    return ""


FINISH_STEPS = [("draft", "起草正文"), ("deai", "去 AI 味"), ("summary", "章节摘要"),
                ("foreshadow", "更新伏笔台账"), ("violations", "违禁词扫描")]


def start_finish_chapter(book_id: str, chapter_id: str) -> Dict[str, Any]:
    """一键写完本章：起草 → 去 AI 味 → 摘要 → 伏笔台账 → 违禁词扫描（后台链式执行）。"""
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
    if not book or not chapter:
        raise LookupError("书或章节不存在")
    if chapter_busy(chapter_id):
        return {"ok": False, "error": "本章正在生成中，等它跑完再点"}
    tid = store.new_id("task")
    steps = [{"key": k, "name": n, "status": "pending", "gen_id": "", "msg": ""}
             for k, n in FINISH_STEPS]
    store.insert("tasks", {"id": tid, "book_id": book_id, "chapter_id": chapter_id,
                           "kind": "finish_chapter", "status": "running",
                           "steps": json.dumps(steps, ensure_ascii=False),
                           "created_at": store.now()})
    spawn(_finish_chain(tid, book_id, chapter_id))
    return {"ok": True, "task_id": tid, "steps": steps,
            "message": f"已开始连跑：{' → '.join(n for _, n in FINISH_STEPS)}"}


async def _finish_chain(tid: str, book_id: str, chapter_id: str) -> None:
    from ..checks import checks          # 局部导入，避免与 checks 层循环依赖
    task = store.query_one("SELECT * FROM tasks WHERE id=?", [tid])
    steps = store.jloads(task.get("steps"), []) or []
    failed = ""

    def mark(key: str, status: str, msg: str = "", gen_id: str = "") -> None:
        for s in steps:
            if s["key"] == key:
                s["status"] = status
                if msg:
                    s["msg"] = msg
                if gen_id:
                    s["gen_id"] = gen_id
        store.update("tasks", tid, {"steps": json.dumps(steps, ensure_ascii=False)})

    async def run_flow_step(key: str, step_id: str, extra: str = "") -> str:
        """跑一个流程步骤（生成 → 落库），返回 "" 表示成功、否则返回失败原因。"""
        book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
        chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
        step = definitions.get_step(book["flow_id"], step_id)
        if not step:
            mark(key, "skipped", f"该流程没有步骤 {step_id}")
            return ""
        prev = store.query_one("SELECT * FROM chapters WHERE book_id=? AND no=?",
                              [book_id, int(chapter["no"]) - 1])
        prompt, _ = build_prompt(book, step, chapter=chapter, prev_chapter=prev, extra=extra)
        model = resolve_model_for_step(book, step_id)
        gid = _gen_row(book_id, step_id, step_id, model, prompt, chapter_id)
        mark(key, "running", gen_id=gid)
        if not model:
            store.update("gens", gid, {"status": "failed", "finished_at": store.now(),
                                       "error": "没有可用的模型"})
            mark(key, "failed", "没有可用的模型")
            return "没有可用的模型"
        await _run(gid, model, prompt, book, step, chapter)      # 唯一的模型调用点
        g = store.query_one("SELECT * FROM gens WHERE id=?", [gid]) or {}
        if g.get("status") != "success":
            mark(key, "failed", (g.get("error") or "")[:120])
            return (g.get("error") or "生成失败")[:60]
        # 改写被字数保护拦下时，结果进了生成记录但没覆盖正文 —— 如实反映到进度里
        warn = (g.get("error") or "")
        if warn.startswith("[note] ⚠️"):
            mark(key, "success", warn.replace("[note] ", "")[:90], gid)
        else:
            mark(key, "success", f"{g.get('chars') or 0} 字", gid)
        return ""

    # ① 起草 ② 去 AI 味
    for key, kind in (("draft", "draft"), ("deai", "deai")):
        sid = chapter_step_id((store.query_one("SELECT * FROM books WHERE id=?", [book_id]) or {}).get("flow_id") or "", kind)
        if not sid:
            mark(key, "skipped", "该流程无对应章节步骤")
            continue
        st = await run_flow_step(key, sid, extra="（这是「一键写完本章」的自动流程，按前一步结果继续打磨）")
        if st:
            failed = f"{key} 失败"
            break

    # ③ 摘要 ④ 伏笔台账
    if not failed:
        book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
        chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
        if not (chapter.get("content") or "").strip():
            mark("summary", "skipped", "本章没有正文")
            mark("foreshadow", "skipped", "本章没有正文")
        else:
            # 摘要 + 伏笔合并为一次模型调用（实测 1017 单次即可稳定输出两段；省一次调用）
            mark("summary", "running")
            r = await summarize_and_foreshadow(book, chapter)
            mark("summary", "success" if r["ok"] else "failed",
                 (f"{r['chars']} 字" if r["ok"] else (r.get("error") or "")), r["gen_id"])
            mark("foreshadow", "success" if r["ok"] else "failed",
                 "已合并到上一次调用" if r["ok"] else "随摘要失败", r["gen_id"])
            if not r["ok"]:
                failed = "摘要+伏笔失败"

    # ⑤ 违禁词扫描（本地规则，不用模型）
    if not failed:
        mark("violations", "running")
        try:
            v = checks.violations_check(book_id, chapter_id)
            mark("violations", "success",
                 f"一级命中 {len(v['blocks'])}｜AI 特征词 {len(v['ai_words'])}")
        except Exception as exc:  # noqa: BLE001
            mark("violations", "failed", str(exc)[:120])

    store.update("tasks", tid, {"status": "failed" if failed else "success",
                                "error": failed, "finished_at": store.now(),
                                "steps": json.dumps(steps, ensure_ascii=False)})


def start_summary(book_id: str, chapter_id: str) -> Dict[str, Any]:
    """后台生成一章的结构化摘要（返回 gen_id，前端轮询）。

    ⚠️ gen 记录必须**在返回前**就落库：否则调用方拿到 gen_id 立刻轮询会 404
    （后台协程还没来得及插入）。教训来自真实使用：脚本按返回的 id 查询时炸了 KeyError。
    """
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
    if not book or not chapter:
        raise LookupError("书或章节不存在")
    if not (chapter.get("content") or "").strip():
        return {"ok": False, "error": "本章还没有正文，先生成正文"}
    model = resolve_model_for_step(book, "chapter")
    gid = _gen_row(book_id, "summary", "summary", model,
                   "（摘要与伏笔台账已合并为一次调用）", chapter_id)

    async def _job() -> None:
        # 单独点「重算摘要」也走合并调用：一次同时更新摘要与伏笔，省一次模型调用
        await summarize_and_foreshadow(book, chapter, gid=gid)

    spawn(_job())
    return {"ok": True, "gen_id": gid}


def start_foreshadows(book_id: str, chapter_id: str) -> Dict[str, Any]:
    """后台用本章更新伏笔账本（返回 gen_id）。同样：记录先落库，再跑任务。"""
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
    if not book or not chapter:
        raise LookupError("书或章节不存在")
    if not (chapter.get("content") or "").strip():
        return {"ok": False, "error": "本章还没有正文"}
    model = resolve_model_for_step(book, "chapter")
    gid = _gen_row(book_id, "foreshadow", "foreshadow", model,
                   "（摘要与伏笔台账已合并为一次调用）", chapter_id)

    async def _job() -> None:
        # 单独「更新伏笔」也走合并调用（同时刷新摘要），省一次调用
        await summarize_and_foreshadow(book, chapter, gid=gid)

    spawn(_job())
    return {"ok": True, "gen_id": gid}


def adopt_generation(gid: str) -> Dict[str, Any]:
    """把某条生成结果「采用」为产出/正文。

    用途：① 改写被字数保护拦下后手动采用；② 回滚到上一版（挑一条历史生成记录采用）。
    """
    g = store.query_one("SELECT * FROM gens WHERE id=?", [gid])
    if not g:
        raise LookupError("生成记录不存在")
    if g.get("status") != "success" or not (g.get("output") or "").strip():
        raise RuntimeError("这条记录没有可用输出（失败或空输出）")
    kind = g.get("kind") or g.get("step_id") or ""
    out = textkit.strip_fences(g["output"])
    book = store.query_one("SELECT * FROM books WHERE id=?", [g["book_id"]])
    note = ""
    if kind in ("summary", "foreshadow"):
        if kind == "summary":
            store.update_safe("chapters", g["chapter_id"],
                              {"summary": out, "updated_at": store.now()})
            note = "已采用为本章摘要"
        else:
            store.update_safe("books", g["book_id"], {"foreshadows": out, "updated_at": store.now()})
            note = "已采用为伏笔台账"
    elif g.get("chapter_id"):
        ch = store.query_one("SELECT * FROM chapters WHERE id=?", [g["chapter_id"]])
        if not ch:
            raise LookupError("章节不存在")
        body = textkit.clean_preamble(out)
        done = kind in ("deai", "polish", "hook")
        store.update_safe("chapters", ch["id"], {
            "content": body, "words": textkit.count_words(body),
            "status": "done" if done else "draft",
            "progress": "deai" if done else "draft", "updated_at": store.now()})
        note = f"已采用为第 {ch['no']} 章正文（{textkit.count_words(body)} 字）"
    elif book:
        art = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?",
                              [book["id"], kind])
        if art and int(art.get("locked") or 0):
            raise RuntimeError("该产出已锁定，请先解锁再采用")
        store.upsert_artifact(book["id"], kind, out)
        note = f"已采用为「{kind}」产出"
    store.update_safe("gens", gid, {"error": f"[note] {note}"})
    return {"ok": True, "note": note}


def task_view(tid: str) -> Optional[Dict[str, Any]]:
    t = store.query_one("SELECT * FROM tasks WHERE id=?", [tid])
    if not t:
        return None
    t["steps"] = store.jloads(t.get("steps"), []) or []
    return t


# ---------------------------------------------------------------- 生成

def start_generation(book_id: str, step_id: str, *, chapter_id: str = "",
                     extra: str = "", model_id: str = "", flow_id: str = "") -> Dict[str, Any]:
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    if not book:
        raise LookupError("书不存在")
    if flow_id and flow_id != book.get("flow_id"):
        # 工具箱场景：临时借用另一个流程（如 stuck）的步骤定义
        book = {**book, "flow_id": flow_id}
    step = definitions.get_step(book["flow_id"], step_id)
    if not step:
        raise LookupError(f"该流程没有步骤：{step_id}")
    if chapter_id and chapter_busy(chapter_id):
        return {"ok": False, "error": "本章正在生成中（等它跑完再点，避免重复消耗）"}

    chapter = prev = None
    if chapter_id:
        chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
        if chapter:
            prev = store.query_one(
                "SELECT * FROM chapters WHERE book_id=? AND no=?",
                [book_id, int(chapter["no"]) - 1])

    prompt, used = build_prompt(book, step, chapter=chapter, prev_chapter=prev, extra=extra)
    model = resolve_model_for_step(book, step_id, model_id)
    gid = store.new_id("gen")
    base = {
        "id": gid, "book_id": book_id, "step_id": step_id,
        "chapter_id": chapter_id or "", "kind": step_id,
        "model_name": (model or {}).get("name") or "（未配置模型）",
        "prompt": prompt, "started_at": store.now(), "status": "running",
    }

    if not model:
        store.insert("gens", {**base, "status": "failed", "error": "还没有可用的模型，请先在「模型」页添加",
                              "finished_at": store.now()})
        return {"ok": False, "gen_id": gid, "error": "还没有可用的模型，请先在「模型」页添加"}

    if model.get("channel") == "web":
        # 网页版人工通道：平台不调用，把提示词交给人去网页执行
        store.insert("gens", {**base, "status": "awaiting"})
        return {"ok": True, "gen_id": gid, "awaiting": True, "prompt": prompt,
                "web_url": model.get("web_url") or "", "web_note": model.get("web_note") or "",
                "model": model.get("name"), "blocks_used": len(used),
                "message": "网页版通道：复制提示词 → 在网页 AI 里执行 → 把结果粘回本页保存"}

    store.insert("gens", base)
    RUNNING[gid] = True
    spawn(_run(gid, model, prompt, book, step, chapter))
    return {"ok": True, "gen_id": gid, "model": model.get("name"),
            "blocks_used": len(used),
            "blocks": [{"module": b["module"], "heading": b["heading"]} for b in used],
            "prompt_chars": len(prompt)}


async def _run(gid: str, model: Dict[str, Any], prompt: str, book: Dict[str, Any],
               step: Dict[str, Any], chapter: Optional[Dict[str, Any]]) -> None:
    try:
        out, meta = await asyncio.wait_for(
            asyncio.to_thread(llm.chat, model, prompt, SYSTEM), timeout=TASK_LIMIT)
    except asyncio.TimeoutError:
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": f"单次生成超过 {int(TASK_LIMIT)} 秒未结束，已中断"})
        RUNNING.pop(gid, None)
        return
    except Exception as exc:  # noqa: BLE001
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": str(exc)[:500]})
        RUNNING.pop(gid, None)
        return

    out = textkit.strip_fences(out)
    # 先把用量落库（费用看板 / A/B 对比用）：部分字段更新，不影响后面的正文落库
    _usage = (meta or {}).get("usage") or {}
    _pt = int(_usage.get("prompt_tokens") or 0)
    _ct = int(_usage.get("completion_tokens") or 0)
    _ca = int(_usage.get("cache_tokens") or 0)
    store.update_safe("gens", gid, {
        "prompt_tokens": _pt, "completion_tokens": _ct, "cache_tokens": _ca,
        "cost": estimate_cost((model or {}).get("model_name") or "", _pt, _ct, _ca),
    })
    note = ""
    # 落库整段包 try：任何写入异常都不能让这条记录停在 running（那会让该章被并发锁永久锁死）
    try:
        if chapter:
            body = textkit.clean_preamble(out)
            done = step["id"] in ("deai", "polish", "hook")
            old_words = textkit.count_words(chapter.get("content") or "")
            new_words = textkit.count_words(body)
            # ⚠️ 改写保护：实测 GLM 会"只给改写说明"或大幅增删（0.57 倍 / 4 倍都出现过）。
            # 阈值：缩短到 65% 以下、或膨胀到 1.8 倍以上 → 不覆盖正文，只留生成记录。
            # （曾用 3 倍阈值，一次 2.997 倍的膨胀刚好钻过去，把 3651 字的稿污染成 9207 字。）
            # 字数保护只约束「改写」（deai/polish/hook）：它们应该与原文篇幅相当。
            # 起草（draft）是重新生成正文，长度由本章要点决定；如果拿旧正文去卡它，
            # 重写短章节会被误判成"骤降"而整章拒收（实测：2935 字旧稿重写 1904 字被拦）。
            shrink = done and old_words >= 300 and new_words < old_words * 0.65
            blowup = done and old_words >= 300 and new_words > old_words * 1.8
            # ⚠️ 内容形态保护（2026-10-19 新增）：实测「去 AI 味」会把英文分镜/编号提纲
            # 当正文吐出来（7029 字里 35 行英文、23 行编号），字数保护拦不住（只涨 1.71 倍）。
            # 形态不对就拒收：正文不动，结果留在生成记录里，由人决定是否采用。
            shape_ok, shape_why = textkit.body_health(body)
            if not shape_ok or shrink or blowup:
                # 改写被拦 → 自动换 1017 重试一次（只对改写类步骤；原模型就是 1017 时不重试）
                if done and (model or {}).get("model_name") != "deepseek-v4.1-flash-1017":
                    retry = store.query_one(
                        "SELECT * FROM models WHERE model_name='deepseek-v4.1-flash-1017' AND enabled=1")
                    if retry:
                        try:
                            out2, _meta2 = await asyncio.wait_for(
                                asyncio.to_thread(llm.chat, retry, prompt, SYSTEM), timeout=TASK_LIMIT)
                            body2 = textkit.clean_preamble(textkit.strip_fences(out2))
                            ok2, _why2 = textkit.body_health(body2)
                            w2 = textkit.count_words(body2)
                            if ok2 and old_words * 0.65 <= w2 <= old_words * 1.8:
                                store.update_safe("chapters", chapter["id"], {
                                    "content": body2, "words": w2, "status": "done",
                                    "progress": "deai", "updated_at": store.now(),
                                })
                                note = (f"⚠️ 首次改写被拦，已自动换 1017 重试并写入"
                                        f"（{w2} 字）；原始输出见生成记录。")
                                store.update_safe("gens", gid, {
                                    "status": "success", "output": out, "chars": len(out),
                                    "finished_at": store.now(), "error": f"[note] {note}"})
                                RUNNING.pop(gid, None)
                                return
                        except Exception:  # noqa: BLE001
                            pass
                if not shape_ok:
                    note = (f"⚠️ 输出不像正文（{shape_why}），**未覆盖本章正文**；"
                            f"结果已存生成记录，请在「生成记录」里查看后决定是否手动采用。")
                else:
                    why = "骤降到" if shrink else "暴涨到"
                    note = (f"⚠️ 改写结果字数从 {old_words} {why} {new_words}，"
                            f"疑似被压缩/截断或跑偏，**未覆盖本章正文**；结果已存生成记录，"
                            f"请在「生成记录」里查看后决定是否手动采用。")
                store.update_safe("gens", gid, {"status": "success", "output": out,
                                                "chars": len(out), "finished_at": store.now(),
                                                "error": f"[note] {note}"})
                RUNNING.pop(gid, None)
                return
            # 人名硬校验：正文里出现 ≥5 次、但设定表里没有的名字 → 判定漂移，拒绝落库
            # （实测「设定表周德发 → 正文张德贵 70 次」这种，靠提示词拦不住）
            try:
                cc = check_cast(book, body)
                primary = (cc.get("primary") or "").strip()
                if primary and primary not in body:
                    note = (f"⚠️ 人名漂移：设定表的主角是「{primary}」，但这一章正文里一次都没出现"
                            f"（大概率换了名字），**未覆盖本章正文**；"
                            f"确认无误可在「生成记录」里手动采用。")
                    store.update_safe("gens", gid, {"status": "success", "output": out,
                                                    "chars": len(out), "finished_at": store.now(),
                                                    "error": f"[note] {note}"})
                    RUNNING.pop(gid, None)
                    return
            except Exception:  # noqa: BLE001
                pass
            store.update_safe("chapters", chapter["id"], {
                "content": body, "words": new_words,
                "status": "done" if done else "draft",
                "progress": "deai" if done else "draft",
                "updated_at": store.now(),
            })
            note = f"已写入第 {chapter['no']} 章（{new_words} 字）"
            # 人设/金额自动校验：漂移当场提示（只在正文落库后跑，纯本地正则，不花钱）
            try:
                cc = check_cast(book, body)
                if cc.get("issues"):
                    note += "｜人设校验：" + "；".join(cc["issues"][:2])
            except Exception:  # noqa: BLE001
                pass
        else:
            art = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?",
                                  [book["id"], step["id"]])
            if art and int(art.get("locked") or 0):
                note = "产出物已锁定，未自动覆盖；可在生成结果里手动取用"
            else:
                store.upsert_artifact(book["id"], step["id"], out)
                note = f"已写入「{step['name']}」产出"
                _advance(book, step)
    except Exception as exc:  # noqa: BLE001
        note = f"落库失败（结果仍在生成记录里，可手动复制）：{str(exc)[:120]}"

    store.update_safe("gens", gid, {"status": "success", "output": out, "chars": len(out),
                                    "finished_at": store.now(),
                                    "error": f"[note] {note}" if note else ""})
    RUNNING.pop(gid, None)


def _advance(book: Dict[str, Any], step: Dict[str, Any]) -> None:
    """全书级步骤完成后，把"当前步骤"推到下一个全书级步骤（章节级步骤不自动跳）。"""
    if step.get("stage") != "book":
        store.update("books", book["id"], {"step_id": step["id"], "updated_at": store.now()})
        return
    flow = definitions.get_flow(book["flow_id"]) or {}
    steps = flow.get("steps", [])
    nxt = None
    for i, s in enumerate(steps):
        if s["id"] == step["id"]:
            nxt = next((x for x in steps[i + 1:] if x.get("stage") == "book"), None)
            break
    store.update("books", book["id"],
                 {"step_id": (nxt or step)["id"], "updated_at": store.now()})


def save_web_result(gid: str, output: str) -> Dict[str, Any]:
    """网页版人工通道：把网页 AI 的结果粘回来。

    ⚠️ 必须与 API 通道行为一致：① 本章有生成在跑时拒绝写入（否则两边互相覆盖）；
    ② 按步骤类型同步 status / progress（否则网页通道走完的章节永远停在"草稿"）。
    """
    g = store.query_one("SELECT * FROM gens WHERE id=?", [gid])
    if not g:
        raise LookupError("生成记录不存在")
    out = textkit.strip_fences(output)
    book = store.query_one("SELECT * FROM books WHERE id=?", [g["book_id"]])
    step = definitions.get_step(book["flow_id"], g["step_id"]) if book else None
    note = ""
    if g.get("chapter_id"):
        if chapter_busy(g["chapter_id"]):
            raise RuntimeError("本章正在生成中，等它跑完再贴回结果（避免两边互相覆盖）")
        ch = store.query_one("SELECT * FROM chapters WHERE id=?", [g["chapter_id"]])
        if ch:
            body = textkit.clean_preamble(out)
            done = (step or {}).get("id") in ("deai", "polish", "hook")
            store.update_safe("chapters", ch["id"], {
                "content": body, "words": textkit.count_words(body),
                "status": "done" if done else "draft",
                "progress": "deai" if done else "draft",
                "updated_at": store.now()})
            note = f"已写入第 {ch['no']} 章"
    elif book and step:
        store.upsert_artifact(book["id"], step["id"], out)
        _advance(book, step)
        note = f"已写入「{step['name']}」产出"
    store.update_safe("gens", gid, {"status": "success", "output": out, "chars": len(out),
                                    "finished_at": store.now(), "error": f"[note] {note}"})
    return {"ok": True, "note": note, "chars": len(out)}


def gen_view(gid: str) -> Optional[Dict[str, Any]]:
    g = store.query_one("SELECT * FROM gens WHERE id=?", [gid])
    if not g:
        return None
    g["running"] = gid in RUNNING
    g["prompt_chars"] = len(g.get("prompt") or "")
    return g


# ---------------------------------------------------------------- 一次成稿（短故事）

_FULL_DRAFT_PROMPT = """# 任务：一次性写出整篇番茄短故事（五章全文）

你是一位番茄短故事作者。请严格按下面的设定与大纲，**一次写出完整五章正文**。

## 硬性要求（违反任何一条都算不合格）
1. 正好 5 章。每章用「第N章 标题」单独一行开头（如：第1章 晚饭摊牌）。
2. 整篇 6000–30000 字（只看整篇，不限每章字数；五章都要完整，不能某章只写梗概）。
3. 第 1 章开头 300 字内进入冲突，禁止背景铺垫。
4. 第 3 章必须有一次实质性反击。
5. 第 5 章必须收干净（人物命运闭环），不留续集尾巴、不写总结式结尾。
6. 每章结尾必须有钩子（对话或悬念），不要「从此以后」式收尾。
7. 人名、金额、年龄、时间线**严格按设定表与大纲**，不得改名、不得改数字。
8. 只输出正文：不要解释、不要大纲、不要 markdown 围栏、不要「以下是正文」之类的话。

## 本书资料
{book}

## 三份设定表（第 1 步产出）
{setup}

## 五章大纲（第 2 步产出）
{outline}
"""

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHAPTER_LINE = re.compile(r"^\s*第\s*([0-9一二三四五六七八九十]+)\s*章[^\n]*$", re.M)


def _chapter_no(raw: str) -> int:
    s = (raw or "").strip()
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s, 0)


def split_full_draft(text: str) -> List[Dict[str, Any]]:
    """把一次成稿的全文按「第N章」切成章。"""
    t = textkit.strip_fences(text or "")
    marks = list(_CHAPTER_LINE.finditer(t))
    if not marks:
        return []
    out: List[Dict[str, Any]] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        head = m.group(0).strip()
        body = t[m.end():end].strip()
        title = re.sub(r"^\s*第\s*[0-9一二三四五六七八九十]+\s*章\s*", "", head).strip()
        out.append({"no": _chapter_no(m.group(1)), "title": title, "body": body, "head": head})
    return out


def _full_draft_check(parts: List[Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    if len(parts) != 5:
        problems.append(f"只切出 {len(parts)} 章（要求 5 章）")
    total = 0
    for p in parts:
        w = textkit.count_words(p["body"])
        total += w
        p["words"] = w
        ok, why = textkit.body_health(p["body"])
        if not ok:
            problems.append(f"第{p['no']}章不像正文（{why}）")
    # 只看整篇字数：≥6000（签约机会线）、<20000（番茄短故事上限）。
    # 不再卡每章字数——实测按章卡会把「第 5 章 787 字」这种整篇够量的稿子误拒。
    if total < 6000:
        problems.append(f"全篇只有 {total} 字（门槛 6000）")
    if total > 30000:
        problems.append(f"全篇 {total} 字，超过 30000 字上限")
    return problems


async def _run_full_draft(gid: str, model: Dict[str, Any], prompt: str, book: Dict[str, Any]) -> None:
    try:
        out, meta = await asyncio.wait_for(
            asyncio.to_thread(llm.chat, model, prompt, SYSTEM), timeout=TASK_LIMIT)
    except asyncio.TimeoutError:
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": f"一次成稿超过 {int(TASK_LIMIT)} 秒未结束"})
        RUNNING.pop(gid, None)
        return
    except Exception as exc:  # noqa: BLE001
        store.update_safe("gens", gid, {"status": "failed", "finished_at": store.now(),
                                        "error": str(exc)[:500]})
        RUNNING.pop(gid, None)
        return

    out = textkit.strip_fences(out or "")
    parts = split_full_draft(out)
    problems = _full_draft_check(parts)
    if problems:
        note = "⚠️ " + "；".join(problems) + "，**未落库**；结果已存生成记录，可人工处理。"
        store.update_safe("gens", gid, {"status": "success", "output": out, "chars": len(out),
                                        "finished_at": store.now(), "error": f"[note] {note}"})
        RUNNING.pop(gid, None)
        return

    written = []
    for p in parts:
        ch = store.query_one("SELECT * FROM chapters WHERE book_id=? AND no=?",
                             [book["id"], p["no"]])
        if ch:
            store.update_safe("chapters", ch["id"], {
                "title": p["title"] or ch.get("title") or f"第{p['no']}章",
                "content": p["body"], "words": p["words"],
                "status": "draft", "progress": "draft",
                "summary": "",  # 正文被整篇覆盖：旧摘要已过期，清空等待重算
                "updated_at": store.now()})
        else:
            store.insert("chapters", {
                "id": store.new_id("ch"), "book_id": book["id"], "no": p["no"],
                "title": p["title"] or f"第{p['no']}章", "brief": "", "content": p["body"],
                "summary": "", "progress": "draft", "words": p["words"], "status": "draft",
                "created_at": store.now(), "updated_at": store.now()})
        written.append(p["no"])
    total = sum(p["words"] for p in parts)
    note = f"已切成 {len(parts)} 章并落库（共 {total} 字）：第 " + "、".join(str(n) for n in written) + " 章"
    store.update_safe("gens", gid, {"status": "success", "output": out, "chars": len(out),
                                    "finished_at": store.now(), "error": f"[note] {note}"})
    RUNNING.pop(gid, None)
    # 正文整篇换掉后，摘要/伏笔必须重算——否则后续章节读到的是旧人名与旧账本
    # （实测：一次成稿后摘要仍是「林慧/张桂兰」，正文已是「林珊/陈美凤」）
    spawn(_recalc_after_full_draft(book, parts))


_RECALC_LOCK: set = set()   # 同一本书只允许一个重算任务，避免并发互相覆盖、留下僵尸记录


async def _recalc_after_full_draft(book: Dict[str, Any], parts: List[Dict[str, Any]]) -> None:
    """一次成稿落库后，逐章重算「摘要 + 伏笔台账」（后台，失败不影响正文）。"""
    bid = book.get("id") or ""
    if bid in _RECALC_LOCK:
        return
    _RECALC_LOCK.add(bid)
    try:
        await _recalc_impl(book, parts)
    finally:
        _RECALC_LOCK.discard(bid)


async def _recalc_impl(book: Dict[str, Any], parts: List[Dict[str, Any]]) -> None:
    for p in parts:
        ch = store.query_one("SELECT * FROM chapters WHERE book_id=? AND no=?",
                             [book["id"], p["no"]])
        if not ch or not (ch.get("content") or "").strip():
            continue
        try:
            b2 = store.query_one("SELECT * FROM books WHERE id=?", [book["id"]]) or book
            await summarize_and_foreshadow(b2, ch)
        except Exception:  # noqa: BLE001
            continue


def start_full_draft(book_id: str, model_id: str = "") -> Dict[str, Any]:
    """一次成稿：一次调用写出五章全文，切分校验后落库。返回 gen_id（前端轮询）。"""
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    if not book:
        raise LookupError("书不存在")
    # 后台的摘要/伏笔重算不锁书：它写 summary/foreshadows，不碰正文。
    # 实测它会和一键生成的逐章改写撞车，导致第 1 章改写被「本书有生成正在跑」拒掉。
    if [g for g in running_gens(book_id=book_id) if g["step_id"] != "summary_foreshadow"]:
        return {"ok": False, "error": "本书有生成正在跑，等它结束再试"}
    setup = (store.query_one("SELECT content FROM artifacts WHERE book_id=? AND key=?", [book_id, "setup"]) or {}).get("content") or ""
    outline = (store.query_one("SELECT content FROM artifacts WHERE book_id=? AND key=?", [book_id, "outline"]) or {}).get("content") or ""
    if not setup.strip() or not outline.strip():
        return {"ok": False, "error": "先完成第 1 步设定表和第 2 步大纲，再一次成稿"}
    prompt = _FULL_DRAFT_PROMPT.format(
        book=f"书名：{book.get('name')}｜题材：{book.get('genre')}｜受众：{AUDIENCE_LABEL.get(book.get('audience') or '', '不限')}\n一句话设定：{book.get('brief') or '（未填）'}",
        setup=setup.strip()[:12000], outline=outline.strip()[:12000])
    base = resolve_model_for_step(book, "draft", model_id)
    if not base:
        return {"ok": False, "error": "没有可用模型"}
    model = dict(base)
    model["max_tokens"] = max(int(model.get("max_tokens") or 8192), 24000)   # 一次要吐 1.5 万字
    gid = store.new_id("gen")
    store.insert("gens", {"id": gid, "book_id": book_id, "step_id": "full_draft",
                          "chapter_id": "", "kind": "full_draft",
                          "model_name": (model or {}).get("name") or "（未配置模型）",
                          "prompt": prompt, "status": "running", "started_at": store.now()})
    RUNNING[gid] = True
    spawn(_run_full_draft(gid, model, prompt, book))
    return {"ok": True, "gen_id": gid, "model": model.get("name"), "max_tokens": model["max_tokens"]}


# ---------------------------------------------------------------- 摘要 + 伏笔合并（省一次调用）

COMBINED_SUMMARY_PROMPT = """一次完成两件事（摘要 + 伏笔台账），用固定标记分隔输出。

【本章（第{no}章《{title}》）正文】
{content}

## 任务一：结构化摘要
严格按五节输出，每节 2-4 行，只写事实、不评论：
【关键情节】本章发生了什么（按顺序，含关键数字/物件）
【出场人物】谁出场了、做了什么
【人物状态变化】关系/立场/资源/情绪的变化
【遗留悬念】还没解决、后文要回收的线索
【衔接提示】下一章开头的自然切入点

## 任务二：更新埋伏笔账本
【当前账本】
{current}

规则：本章新埋的追加；本章已回收的删除；只保留未回收条目；拿不准的一律保留。
每行一条：- （第N章）一句话写清线索；没有就写「（暂无）」。

## 输出格式（必须严格遵守，两个标记都要有）
【章节摘要】
（上面的五节内容）

【伏笔台账】
（账本内容）
"""


async def summarize_and_foreshadow(book: Dict[str, Any], chapter: Dict[str, Any],
                                   gid: str = "") -> Dict[str, Any]:
    """一次调用同时产出「章节摘要 + 伏笔台账」，替掉原来的两次调用。

    gid 传入时复用已有生成记录（单独「重算摘要」按钮用）。
    """
    model = _internal_model(book, "summary")
    prompt = COMBINED_SUMMARY_PROMPT.format(
        no=chapter["no"], title=chapter.get("title") or "",
        content=(chapter.get("content") or "").strip(),
        current=(book.get("foreshadows") or "").strip() or "（空）")
    if not gid:
        gid = _gen_row(book["id"], "summary_foreshadow", "summary_foreshadow", model, prompt, chapter["id"])
    out, err = await _llm_once(model, prompt, gid)
    if not out:
        return {"ok": False, "gen_id": gid, "chars": 0, "error": err}
    m1 = out.find("【章节摘要】")
    m2 = out.find("【伏笔台账】")
    if m1 >= 0 and m2 > m1:
        summary = out[m1 + len("【章节摘要】"):m2].strip()
        fores = out[m2 + len("【伏笔台账】"):].strip()
    else:
        # 模型没按格式输出：整段当摘要，伏笔保持不动（宁可不更新，也不能写坏账本）
        summary, fores = out.strip(), ""
    if summary:
        store.update("chapters", chapter["id"], {"summary": summary, "updated_at": store.now()})
    if fores:
        store.update("books", book["id"], {"foreshadows": fores, "updated_at": store.now()})
    return {"ok": bool(summary), "gen_id": gid, "chars": len(out), "error": err or ""}


# ---------------------------------------------------------------- 人设 / 金额校验

_CAST_NAME_RE = re.compile(r"(?:主角|主要反派|反派|配角|次要角色|角色|人物|男主|女主)[：:]\s*([\u4e00-\u9fa5]{2,4})")
_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|元|块)")
_SPEECH_RE = re.compile(r"([\u4e00-\u9fa5]{2,3})(?:说|道|问|喊|叫|笑|低声|开口)")


def extract_cast(text: str) -> Dict[str, Any]:
    """从设定表里提取「人名表 + 关键金额表」。"""
    t = text or ""
    names = []
    for m in _CAST_NAME_RE.finditer(t):
        n = m.group(1).strip()
        if n and n not in names:
            names.append(n)
    # 「周宇，27岁，小叔子」这类人物介绍：姓名 + 年龄，最可靠
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,3})，\s*\d{2}\s*岁", t):
        n = m.group(1)
        if n not in names:
            names.append(n)
    # 「次要角色：周旭，33岁，林珊丈夫…」：只取冒号后到第一个逗号/括号前的那一段
    # （早期实现按 2-3 字切，把「林珊丈夫」切成了「珊丈夫」「格偏软」这种假人名）
    for m in re.finditer(r"(?:次要角色|配角|配角名单)[：:]\s*([^\n，,（(]{2,4})", t):
        cand = m.group(1).strip()
        if not cand or cand in names:
            continue
        if any(x in cand for x in ("丈夫", "妻子", "性格", "父亲", "母亲", "婆婆", "公公", "主角", "角色")):
            continue
        names.append(cand)
    amounts = []
    for m in _MONEY_RE.finditer(t):
        v = f"{m.group(1)}{m.group(2)}"
        if v not in amounts:
            amounts.append(v)
    return {"names": names[:12], "amounts": amounts[:20]}


_PRIMARY_RE = re.compile(r"主角[：:]\s*([\u4e00-\u9fa5]{2,4})")


def check_cast(book: Dict[str, Any], text: str) -> Dict[str, Any]:
    """比对正文与设定表：人名是否漂移、金额是否对不上。

    返回里带 primary（主角名）——调用方用它做硬校验：主角名在正文里一次都没出现，
    基本可以断定这一章换了人写（实测「设定表周德发 → 正文张德贵」就是这么漏过去的）。
    """
    setup = (store.query_one("SELECT content FROM artifacts WHERE book_id=? AND key='setup'", [book["id"]]) or {}).get("content") or ""
    pm = _PRIMARY_RE.search(setup or "")
    primary = (pm.group(1) if pm else "").strip()
    cast = extract_cast(setup)
    body = text or ""
    known = cast["names"]
    missing = [n for n in known if n and n not in body]
    # 正文里像人名的词（"XX说/道/问…"）里、不在设定表中的高频词 → 疑似漂移
    counts: Dict[str, int] = {}
    _STOP = ("去", "来", "回", "在", "把", "被", "让", "给", "对", "朝", "向", "从", "到",
             "跟", "和", "与", "为", "是", "有", "没", "不", "就", "都", "也", "还", "才",
             "又", "再", "很", "太", "更", "最", "别", "再", "刚", "正", "已", "将", "要")
    for m in _SPEECH_RE.finditer(body):
        n = m.group(1)
        if n in known or n.startswith(_STOP):
            continue
        counts[n] = counts.get(n, 0) + 1
    stray = [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1]) if v >= 3][:5]
    body_amounts = [f"{m.group(1)}{m.group(2)}" for m in _MONEY_RE.finditer(body)]
    extra_amounts = [a for a in dict.fromkeys(body_amounts) if a not in cast["amounts"]]
    issues = []
    if known and not any(n in body for n in known):
        issues.append("正文里没有出现设定表中的任何主要人名")
    if missing and len(missing) < len(known):
        issues.append("设定表里这些角色没在正文出现：" + "、".join(missing[:5]))
    if stray:
        issues.append("疑似人名漂移（正文高频但设定表没有）：" + "、".join(f"{s['name']}×{s['count']}" for s in stray))
    if extra_amounts:
        issues.append("金额不在设定表里（需人工确认是否情节新增）：" + "、".join(extra_amounts[:6]))
    return {"ok": not issues, "primary": primary,
            "known_names": known, "known_amounts": cast["amounts"],
            "missing_names": missing, "stray_names": stray,
            "extra_amounts": extra_amounts[:8], "issues": issues}


# ---------------------------------------------------------------- 一键续写 N 章（后端任务链）

async def _auto_chapters(tid: str, book_id: str, count: int) -> None:
    """后台连续写 N 章：每章走完整的「起草 → 改写 → 摘要+伏笔 → 扫描」。"""
    task = store.query_one("SELECT * FROM tasks WHERE id=?", [tid])
    steps = store.jloads(task.get("steps"), []) or []
    failed = ""

    def mark(i: int, status: str, msg: str = "") -> None:
        for s in steps:
            if s["key"] == f"ch{i}":
                s["status"] = status
                if msg:
                    s["msg"] = msg
        store.update_safe("tasks", tid, {"steps": json.dumps(steps, ensure_ascii=False)})

    try:
        for i in range(1, count + 1):
            if failed:
                mark(i, "skipped", "前序章节失败，已停止")
                continue
            mark(i, "running", "建章…")
            row = store.query_one("SELECT MAX(no) m FROM chapters WHERE book_id=?", [book_id])
            no = int((row or {}).get("m") or 0) + 1
            cid = store.new_id("ch")
            store.insert("chapters", {"id": cid, "book_id": book_id, "no": no, "title": "",
                                      "brief": "", "content": "", "summary": "", "progress": "",
                                      "words": 0, "status": "draft",
                                      "created_at": store.now(), "updated_at": store.now()})
            mark(i, "running", f"第 {no} 章：起草 → 改写 → 摘要+伏笔 → 扫描…")
            r = start_finish_chapter(book_id, cid)
            if not r.get("ok"):
                mark(i, "failed", r.get("error") or "启动失败")
                failed = f"第 {no} 章启动失败"
                continue
            sub = r["task_id"]
            for _ in range(1800):                       # 最多等 1 小时
                await asyncio.sleep(2)
                t = store.query_one("SELECT * FROM tasks WHERE id=?", [sub]) or {}
                if t.get("status") in ("success", "failed"):
                    mark(i, t["status"], (t.get("error") or "")[:80] or f"第 {no} 章完成")
                    if t.get("status") == "failed":
                        failed = f"第 {no} 章失败：{(t.get('error') or '')[:60]}"
                    break
            else:
                mark(i, "failed", "等待超时")
                failed = f"第 {no} 章超时"
    except Exception as exc:  # noqa: BLE001
        failed = f"任务异常：{str(exc)[:150]}"

    store.update_safe("tasks", tid, {
        "status": "failed" if failed else "success",
        "error": failed, "finished_at": store.now(),
        "steps": json.dumps(steps, ensure_ascii=False)})


def start_auto_chapters(book_id: str, count: int = 3) -> Dict[str, Any]:
    """一键续写 N 章：返回 task_id（前端轮询 /api/tasks/{id}）。"""
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    if not book:
        raise LookupError("书不存在")
    if running_gens(book_id=book_id):
        return {"ok": False, "error": "本书有生成正在跑，等它结束再试"}
    n = max(1, min(20, int(count or 3)))
    tid = store.new_id("task")
    steps = [{"key": f"ch{i}", "name": f"第 {i} 章", "status": "pending", "gen_id": "", "msg": ""}
             for i in range(1, n + 1)]
    store.insert("tasks", {"id": tid, "book_id": book_id, "chapter_id": "", "kind": "auto_chapters",
                           "status": "running", "steps": json.dumps(steps, ensure_ascii=False),
                           "created_at": store.now()})
    spawn(_auto_chapters(tid, book_id, n))
    return {"ok": True, "task_id": tid, "count": n, "steps": steps}


# ---------------------------------------------------------------- 大纲偏离检测

_DRIFT_PROMPT = """比对「大纲」与「最近章节摘要」，找出正文是否偏离大纲。

## 大纲
{outline}

## 最近章节摘要（按章顺序）
{summaries}

只输出三部分，不要写正文、不要复述剧情：
1. 主要偏离（如有）：从哪一章开始偏、偏离了什么、对后续有什么风险；
2. 是否需要修正大纲或回头改章（给具体章号）；
3. 一句话结论。
"""


def drift_check(book_id: str) -> Dict[str, Any]:
    """用最近 10 章摘要与大纲做一次偏离检测（同步调用，约 10–20 秒）。"""
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id])
    if not book:
        raise LookupError("书不存在")
    outline = (store.query_one("SELECT content FROM artifacts WHERE book_id=? AND key='outline'",
                               [book_id]) or {}).get("content") or ""
    if not outline.strip():
        return {"ok": False, "error": "还没有大纲，先生成大纲"}
    chs = store.query(
        "SELECT no, title, summary FROM chapters WHERE book_id=? AND summary != '' "
        "ORDER BY no DESC LIMIT 10", [book_id])
    chs = sorted(chs, key=lambda x: x["no"])
    if len(chs) < 3:
        return {"ok": False, "error": "至少要有 3 章摘要才能比对"}
    summaries = "\n\n".join(f"第{c['no']}章《{c.get('title') or ''}》\n{(c.get('summary') or '').strip()}"
                            for c in chs)
    model = resolve_model_for_step(book, "outline")
    if not model:
        return {"ok": False, "error": "没有可用模型"}
    prompt = _DRIFT_PROMPT.format(outline=outline.strip()[:6000], summaries=summaries[:8000])
    out, meta = llm.chat(model, prompt, SYSTEM)
    return {"ok": True, "report": out, "chars": len(out),
            "chapters": [c["no"] for c in chs], "usage": (meta or {}).get("usage")}
