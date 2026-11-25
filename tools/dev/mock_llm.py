"""假模型服务（OpenAI 兼容），仅用于冒烟测试：不调真模型、不花钱。

启动： python tools/dev/mock_llm.py    → http://127.0.0.1:8899/v1
它按提示词判断"这次要的是章节正文还是步骤产出"，返回结构可用的中文样例，
足够验证平台全链路（生成 → 落库 → 检查 → 导出）。
"""
import re
import time

import uvicorn
from fastapi import FastAPI

app = FastAPI()

_CHAPTER = """雨点砸在铁皮棚上，噼里啪啦像有人在外头数着数。
“进来。”屋里的声音不高，却让老陈的脚步顿住了。他捏着那张发黄的纸条，指节发白。
“东西呢？”柜台后的人连头都没抬。
“在、在这儿。”老陈把纸条往前推了半寸，又缩回来，“可您得先告诉我，这价钱是怎么算出来的。”
对方这才抬眼。那一眼里没有情绪，只有一种算账的人才有的耐心：“你欠的不是钱，是三年前那天你转身走掉的四分钟。”
老陈的喉结动了动。他忽然明白，自己带来的不是一张票据，是一个选择——认，还是赖。
“我认。”他把纸条按在台面上，掌心全是汗，“但我有个条件。”
“说。”
“那四分钟里发生的事，我要亲耳听一遍。”
柜台后的人笑了一下，那笑意却没到眼底。“可以。”他从抽屉里取出一只老式录音笔，按下播放键——
“不过在听之前，你得先回答我一个问题：那天你为什么要回头？”
录音笔的红灯亮起，像一只不肯闭上的眼睛。"""

_ARTIFACT = """【示例产出 · 由假模型生成，仅用于验证链路】
一句话结论：按手册指令执行本步骤，产出已就位。
要点：
1. 已按skill原文指令完成本步要求；
2. 与本书已确定的设定/前序产出保持一致；
3. 列出可执行的下一步动作，供人工核对后采用。
备注：把上面的样例替换为人工打磨内容即可。"""


def _kind(prompt: str, model: str) -> str:
    """判断这次要"章节正文"还是"步骤产出"。

    判据用「本次要写的内容」这个章节级步骤独有的小标题，**不要**去匹配「输出要求」的措辞——
    那是会随平台改文案而变的（踩过一次：改写步骤换了措辞，假模型就把正文当产出返回了）。
    """
    if "short" in model:            # 专供测试断言用：故意返回远短于原文的"改写稿"
        return "short"
    if "任务二：更新埋伏笔账本" in prompt:   # 摘要+伏笔的合并调用
        return "summary"
    if "本次要写的内容" in prompt:
        return "chapter"
    return "artifact"


@app.get("/v1/models")
async def models():
    """模拟网关的模型列表（id 命名贴近真实门网关，供「一键创建推荐配置」测试用）。"""
    return {"object": "list", "data": [
        {"id": i} for i in [
            "deepseek-flash", "deepseek-v4-flash-0731", "deepseek-v4-pro-0813",
            "glm-5.3-flash", "glm-5.3-flashx", "glm-5.3", "glm-5.2", "doubao-seed-2.1-pro",
            "kimi-k2.6", "kimi-k3", "qwen3.8-flash", "mimo-v2.6-flash",
        ]]}


@app.post("/v1/chat/completions")
async def chat(payload: dict):
    # 模型名里带 sleep 就故意变慢：用来复验"后台任务是否真的不阻塞 HTTP 请求"
    m = str(payload.get("model") or "")          # 模型名（下面循环里别再用 m，会遮蔽它）
    mt = re.search(r"sleep-(\d+)", m)
    if mt:
        time.sleep(int(mt.group(1)))
    user = ""
    for msg in reversed(payload.get("messages") or []):    # ⚠️ 曾用 m 当循环变量 → 遮蔽模型名，
        if msg.get("role") == "user":                      #   导致按模型名分支的判断全部失效
            user = msg.get("content") or ""
            break
    no = re.search(r"第\s*(\d+)\s*章", user)      # 仅用于日志观察，不影响返回结构
    kind = _kind(user, m)
    if kind == "chapter":
        content = _CHAPTER
        _ = no
    elif kind == "short":
        content = "林默进了便利店，看见一行灰字。他愣了一下，买了瓶水就走了。"   # 远短于原文 → 平台应拦下覆盖
    elif kind == "summary":
        # 必须按 COMBINED_SUMMARY_PROMPT 的固定标记返回，否则平台判定"没按格式"→ 伏笔不更新
        content = (
            "【章节摘要】\n"
            "【关键情节】老陈带着一张发黄纸条去找柜台后的人，对方点出他欠的是三年前转身走掉的四分钟。\n"
            "【出场人物】老陈（欠债者）、柜台后的人（债主/放贷方）。\n"
            "【人物状态变化】老陈从试探退让转为承认欠债并提出条件。\n"
            "【遗留悬念】三年前那四分钟里到底发生了什么。\n"
            "【衔接提示】录音笔按下播放键的瞬间。\n\n"
            "【伏笔台账】\n"
            "- （第1章）发黄的纸条：谁写的、写的什么内容\n"
            "- （第1章）三年前走掉的四分钟：具体发生了什么\n"
        )
    else:
        task = re.search(r"#\s*任务：(.+)", user)
        content = f"（任务：{task.group(1).strip() if task else '步骤产出'}）\n" + _ARTIFACT
    return {
        "id": "mock",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": len(user), "completion_tokens": len(content)},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")
