"""番茄短故事分类（官方《番茄短故事分类释义（新）_2026.4 起》）。

发布时「作品分类」最多选 8 个，其中**主分类必选**。
频道（女频 / 男频 / 通用）由分类本身决定，用来给书的受众与赛道指令定位。
"""

from __future__ import annotations

from typing import Any, Dict, List

CATEGORIES: List[Dict[str, str]] = [
    {"key": "marriage_family", "name": "婚姻家庭", "channel": "女频",
     "desc": "现代背景，以女性视角，讲述婚姻或家庭生活，围绕伴侣、父母、公婆、子女、姑嫂、亲戚矛盾等关系展开的故事。"},
    {"key": "girl_life", "name": "女生生活", "channel": "女频",
     "desc": "现代背景，以女性视角，围绕真假千金、职场、寝室、同学、师生等关系展开的故事。"},
    {"key": "boy_life", "name": "男生生活", "channel": "男频",
     "desc": "现代背景，以男性视角，围绕职场、家庭、朋友关系等展开的故事，主线非感情线。"},
    {"key": "urban_daily", "name": "都市日常", "channel": "男频",
     "desc": "现代背景，以男性视角，围绕都市生活、赘婿、逆袭、升级流、退婚流等元素展开的故事，主线非感情线。"},
    {"key": "female_growth", "name": "女性成长", "channel": "女频",
     "desc": "现代或古代背景，以女性视角，主线非感情线，主要以女主主观意愿上的成长、女性间的互助、救赎等为主线的故事（不包含复仇、打脸、虐渣类故事）。"},
    {"key": "modern_sweet", "name": "现言甜宠", "channel": "女频",
     "desc": "现代背景，以女性视角，围绕校园、职场、婚恋等展开，讲述男主宠女主、男女主互相暗恋或明恋类的幸福美满类故事。"},
    {"key": "youth_angst", "name": "青春虐恋", "channel": "女频",
     "desc": "现代背景，以女性视角，讲述男女主恋爱、谈婚论嫁阶段的言情故事，情绪基调为虐恋、先虐后甜或先虐后爽。"},
    {"key": "marriage_angst", "name": "虐心婚恋", "channel": "女频",
     "desc": "现代背景，以女性视角，讲述男女主婚姻阶段的言情故事，情绪基调为虐恋、先虐后甜或先虐后爽。"},
    {"key": "boy_romance", "name": "男生情感", "channel": "男频",
     "desc": "现代背景，以男性视角，言情为主线，讲述两性情感的言情类故事。"},
    {"key": "palace_intrigue", "name": "宫斗宅斗", "channel": "女频",
     "desc": "古代背景，以女性视角，背景发生在宫廷或后宅，情节上以宫斗、宅斗、女性权谋为主线推进的故事。"},
    {"key": "ancient_worldly", "name": "古风世情", "channel": "女频",
     "desc": "古代背景，围绕家庭、朋友、师徒、姐妹等关系展开的故事，主线非感情线。"},
    {"key": "ancient_sweet", "name": "古言甜宠", "channel": "女频",
     "desc": "古代背景，以女性视角，言情为主线，讲述男女主互相暗恋或明恋的幸福美满类故事。"},
    {"key": "ancient_angst", "name": "古言虐恋", "channel": "女频",
     "desc": "古代背景，以女性视角，讲述男女主两性情感的言情故事，情绪基调为虐恋、先虐后甜或先虐后爽。"},
    {"key": "history_ancient", "name": "历史古代", "channel": "男频",
     "desc": "古代背景，以男性视角，围绕历史、武侠、权谋、言情展开的故事。"},
    {"key": "era", "name": "年代", "channel": "通用",
     "desc": "四五十年代到九十年代背景的文，且年代画风明显。"},
    {"key": "republic", "name": "民国旧影", "channel": "通用",
     "desc": "二十世纪初到新中国成立前的民国背景文，且民国画风明显。"},
    {"key": "fantasy_xianxia", "name": "玄幻仙侠", "channel": "通用",
     "desc": "背景为东方玄幻、仙侠世界、西方奇幻，主线围绕非现实设定推进，世界观有虚构成分的故事。"},
    {"key": "suspense", "name": "悬疑惊悚", "channel": "通用",
     "desc": "围绕悬疑、推理、规则怪谈、校园怪谈、民俗、诡秘、奇闻、风水、灵异、惊悚类元素展开的故事。"},
    {"key": "female_brainhole", "name": "女频脑洞", "channel": "女频",
     "desc": "女性视角，背景非现实向，故事主线以系统、金手指、空间、弹幕、穿书等脑洞元素来推进剧情的故事。"},
    {"key": "male_brainhole", "name": "男频脑洞", "channel": "男频",
     "desc": "男性视角，背景非现实向，故事主线以系统、金手指、空间、弹幕、穿书等脑洞元素来推进剧情的故事。"},
    {"key": "female_derivative", "name": "女频衍生", "channel": "女频",
     "desc": "明确游戏、动漫、影视作品、公版书籍等既有作品的女频二次创作故事。"},
    {"key": "male_derivative", "name": "男频衍生", "channel": "男频",
     "desc": "明确游戏、动漫、影视作品、公版书籍等既有作品的男频二次创作故事。"},
    {"key": "pure_love", "name": "纯爱", "channel": "女频",
     "desc": "以双男主或双女主之间感情发展作为主线的故事。"},
]

BY_KEY: Dict[str, Dict[str, str]] = {c["key"]: c for c in CATEGORIES}

# 短故事的硬性发布门槛（2026 口径，见番茄作家专区短故事发布页）
MIN_WORDS = 6000
MAX_WORDS = 30000   # 番茄短故事上限（用户口径：整篇 ≤30000 字）
DEFAULT_TRIAL_RATIO = 30   # 试读比例默认 30%
MAX_CATEGORIES = 8         # 作品分类最多选 8 个（含主分类）

CHANNEL_TO_AUDIENCE = {"女频": "female", "男频": "male", "通用": "none"}


def category_names(keys: List[str]) -> List[str]:
    return [BY_KEY[k]["name"] for k in keys if k in BY_KEY]


def main_channel(key: str) -> str:
    return (BY_KEY.get(key) or {}).get("channel", "通用")
