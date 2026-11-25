"""全局配置：路径、skill 知识库位置、意图路由表、通用限额。

设计要点：平台以 `ai-xiaoshuo-skill` 为核心 —— skill 的内容既是一等公民
（知识库 / 意图路由 / 指令原文），也是各流程步骤的指令来源。
内置副本在 `app/knowledge/data/`，因此平台**独立可运行**；
想把本机 skill 更新直接接进来，设环境变量 `PLATFORM_SKILL_DIR` 指向它的 references 目录即可。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.environ.get("PLATFORM_DB", os.path.join(DATA_DIR, "app.db"))
STATIC_DIR = os.path.join(ROOT, "static")

# skill 知识库目录（默认内置副本；可用 PLATFORM_SKILL_DIR 指向用户级 skill 的 references）
SKILL_DIR = os.environ.get(
    "PLATFORM_SKILL_DIR", os.path.join(ROOT, "app", "knowledge", "data", "references")
)
SKILL_MD = os.path.join(ROOT, "app", "knowledge", "data", "SKILL.md")
# 本机（用户级）skill 的 references 位置：用于对比"内置副本 vs 本机版本"，
# 提醒是否需要把 PLATFORM_SKILL_DIR 指过去（不改也完全可用）。
LOCAL_SKILL_DIR = os.environ.get(
    "PLATFORM_SKILL_LOCAL_DIR",
    os.path.join(os.path.expanduser("~"), ".dsh", "skills", "ai-xiaoshuo-skill", "references"),
)

PORT = int(os.environ.get("PORT", "8788"))
HOST = os.environ.get("HOST", "127.0.0.1")

# ---------------------------------------------------------------- 意图路由表
# 直接对应 SKILL.md 的「按意图路由」表：用户想干什么 → 读哪个模块。
# 知识库页签按它组织；流程步骤也用它来告诉用户"这一步的依据在哪"。
MODULE_ROUTING = [
    ("完全新手 / 不知道从哪开始 / 收益与投入", "00-入门路线与使用说明.md", "入门路线"),
    ("注册、实名、后台操作、流量机制、养号权重", "01-平台规则与合规红线.md", "平台规则"),
    ("违禁词、违规情节、投稿前自查（高风险·必读）", "01-平台规则与合规红线.md", "合规红线"),
    ("AI 工具选型、模型适配；选题/大纲/人物/开篇/续写/润色指令",
     "02a-通用创作流程指令.md", "通用流程指令"),
    ("男频赛道（系统流、末世囤货、高武玄幻、都市爽文、鉴宝捡漏）",
     "02b-男频赛道专属指令.md", "男频赛道"),
    ("女频赛道（重生虐渣、穿书、甜宠、种田）", "02c-女频赛道专属指令.md", "女频赛道"),
    ("去 AI 味、删套话、替换特征词", "02d-去AI味专项指令.md", "去 AI 味"),
    ("选题黑名单、开篇公式、爆款开头、书名/简介公式与 100 例、替换词库",
     "03-选题与开篇模板库.md", "选题与开篇"),
    ("签约、拒稿原因、首秀 7 天清单、数据阈值、稿费与提现",
     "04-签约首秀与稿费.md", "签约与稿费"),
    ("人设锁死、防 OOC、稳人设 8 法、卡文自检", "05-去AI化与稳人设实战.md", "去AI化与稳人设"),
    ("短篇（8000-25000 字）工业化流水线、四大题材模板、终稿核验",
     "06-短篇工业化手册.md", "短篇工业化"),
    ("同人文全流程（版权风险提示：仅供个人练习）", "07-同人创作体系.md", "同人体系"),
    ("从 0 到 1 完整演示、爆款案例拆解、素材库", "08-实操演示与爆款拆解.md", "实操与拆解"),
    ("番茄短故事：6000 字签约线、23 个作品分类、试读比例、发布规格与五章口径",
     "14-番茄短故事.md", "番茄短故事"),
    ("番茄官方课堂：短故事避坑、人设五步法、节奏冲突爽点、追妻火葬场链路、世情元素、长篇时间线",
     "15-官方课堂实战指南.md", "官方课堂实战"),
    ("找原始 PDF/Word 原件、图片型文件说明", "99-原始资料索引.md", "原始资料索引"),
]

# 赛道 → 模块（章节续写时按书的受众自动追加对应赛道指令）
TRACK_MODULES = {"male": "02b-男频赛道专属指令.md", "female": "02c-女频赛道专属指令.md"}

# 注入给模型的「skill 原文指令」上限（字符）。默认给足：这是平台的核心价值所在，
# 但也不至于把上下文撑爆（1150 字 ≈ 0.8k tokens 量级/块，默认最多 3 块）。
BLOCKS_PER_STEP = 2
BLOCK_MAX_CHARS = 12000        # 单个原文块最多截取多少字（有的节很长，如替换词库）
PROMPT_EXTRA_MAX_CHARS = 4000  # 用户"补充要求"的单次上限

# 正文字数：不计标题行 / 摘要段 / 空白（与旧平台口径一致，便于对照）
WORDS_DEFAULT_MIN = 2000
WORDS_DEFAULT_MAX = 3000

# 跨章连贯：起草时注入最近多少章的「结构化摘要」（更早的剧情不再逐章给，省 token）。
# 逐章摘要是长篇防漂移的主力通道：人设状态、已回收伏笔、悬念都靠它传下去。
CONTEXT_SUMMARY_CHAPTERS = 6
# 「上一章结尾节选」注入多少字（语感与承接用，比摘要更贴）。
CONTEXT_TAIL_CHARS = 600


# ---------------------------------------------------------------- 模型价格（元 / 百万 token）
# (输入, 输出, 缓存命中输入)。用于把 usage 换算成费用；没有列出的模型按 0 计（未知价格）。
MODEL_PRICES = {
    "deepseek-v4.1-flash-1017": (1.0, 4.0, 0.02),
    "deepseek-flash": (1.0, 4.0, 0.02),
    "deepseek-v4-flash-0731": (1.0, 4.0, 0.02),
    "deepseek-v4-pro-0813": (4.0, 16.0, 0.4),
    "seed-2.1-pro": (6.0, 30.0, 0.0),
    "seed-2.1-turbo": (2.0, 8.0, 0.0),
    "glm-5.3": (4.0, 16.0, 0.0),
    "glm-5.3-flashx": (2.0, 7.0, 0.0),
    "glm-5.3-flash": (2.0, 7.0, 0.0),
    "kimi-k2.6": (6.5, 27.0, 0.0),
    "kimi-k3": (8.0, 32.0, 0.0),
}


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int,
                  cache_tokens: int = 0) -> float:
    """按 token 估算费用（元）。缓存命中的输入按缓存价算，其余按普通输入价。"""
    price = MODEL_PRICES.get((model_name or "").strip())
    if not price:
        return 0.0
    pin, pout, pcache = price
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    ca = max(0, min(int(cache_tokens or 0), pt))
    normal_in = pt - ca
    return round((normal_in * pin + ca * pcache + ct * pout) / 1_000_000, 6)
