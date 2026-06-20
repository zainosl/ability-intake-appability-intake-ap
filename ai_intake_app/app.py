#!/usr/bin/env python3
import cgi
import hashlib
import html
import http.client
import json
import os
import re
import sqlite3
import sys
import time
import traceback
import socket
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    from docx import Document
except Exception:
    Document = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "intake.sqlite3"
SETTINGS_PATH = DATA_DIR / "model_settings.json"
STATIC_DIR = ROOT / "static"

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LAST_USED_MODEL = ""
MODEL_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MODEL_REQUEST_TIMEOUT_SECONDS", "180"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()


def admin_token():
    if not ADMIN_PASSWORD:
        return ""
    return hashlib.sha256(f"ability-intake:{ADMIN_PASSWORD}".encode("utf-8")).hexdigest()


def is_public_api(method, path):
    if path in {"/api/health", "/api/auth/status", "/api/auth/login"}:
        return True
    if method == "GET" and path.startswith("/api/client/sessions/"):
        return True
    if method == "POST" and path.startswith("/api/sessions/"):
        parts = path.strip("/").split("/")
        return len(parts) == 4 and parts[3] in {"client-chat-turn", "client-brief"}
    return False


def load_model_settings():
    global DEFAULT_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL
    if not SETTINGS_PATH.exists():
        return
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Warning: failed to read model settings: {e}", file=sys.stderr)
        return
    DEFAULT_MODEL = os.environ.get("OPENAI_MODEL") or data.get("model") or DEFAULT_MODEL
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or data.get("api_key") or OPENAI_API_KEY
    OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL") or data.get("base_url") or OPENAI_BASE_URL).rstrip("/")


def save_model_settings(api_key, model, base_url):
    ensure_dirs()
    payload = {
        "api_key": api_key,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "updated_at": now_iso(),
    }
    SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except Exception:
        pass


MATERIAL_STRUCTURING_PROMPT = """你是“能力资产与认知资产诊断”的材料规整官。

用户上传的材料已经经过本地程序初步清噪，但仍然可能很散：PDF简历、公众号文章、项目复盘、链接正文、补充文本、方法论笔记等。你的任务不是诊断能力，不是追问，也不是给商业方向，而是把材料先整理成清楚、可信、可阅读的“材料底稿”。

你要做的是：
1. 分清材料类型：简历、项目材料、公开文章、方法论笔记、复盘材料、他人反馈、零散自述、未知。
2. 去掉无意义内容：网页残留、导航、重复标题、空泛营销语、明显与用户经历无关的噪音。
3. 保留事实和上下文：时间、公司、岗位、项目、角色、动作、结果、数字、他人反馈、原文中的关键表达。
4. 区分事实、观点、方法表达：不要把文章观点直接当成用户真实能力证据。
5. 把材料整理成后续诊断能直接使用的底稿。

严格限制：
- 不做能力结论。
- 不判断商业方向。
- 不生成追问地图。
- 不把没有证据的推断写成事实。
- 原文信息缺失就写“材料未提供”。

请只输出JSON，不要输出Markdown，不要输出额外文字。格式：
{
  "structured_overview": "300-600字说明材料构成、信息密度、哪些材料最有诊断价值、哪些材料噪音较多",
  "cleaning_summary": [{"material_name":"材料名","raw_length":0,"cleaned_length":0,"cleaning_notes":["本地清洗说明"],"remaining_noise_risk":"low/medium/high","usefulness":"high/medium/low"}],
  "structured_materials": [{"material_name":"材料名","material_type":"简历/项目材料/公开文章/方法论笔记/复盘材料/他人反馈/零散自述/未知","clean_summary":"这份材料整理后的核心内容","key_facts":["事实1"],"user_claims_or_views":["用户观点或文章表达"],"project_or_story_mentions":["提到的项目/故事"],"result_or_metric_mentions":["结果/数字"],"source_limitations":["局限"]}],
  "career_facts": [{"period":"时间段或未知","organization":"组织/平台","role":"角色","facts":["事实"],"source":"来源材料"}],
  "project_fact_cards": [{"project":"项目名或事件","known_background":["背景事实"],"known_user_role":["用户角色事实"],"known_actions":["材料明确写到的动作"],"known_results":["结果事实"],"unknowns":["材料没有说清的地方"],"source":"来源材料"}],
  "method_or_cognitive_expressions": [{"expression":"方法/模型/观点表达","level_guess":"方法论/思维模型/元认知/科学真理/人文素养/价值观/未知","evidence_text":"来自材料的依据","is_project_proven":"yes/no/unclear","source":"来源材料"}],
  "not_useful_or_noisy_content": [{"content_type":"噪音类型","examples":["例子"],"handling":"已忽略/保留但降权/待人工确认"}]
}
"""


MATERIAL_ORGANIZER_PROMPT = """你是“能力资产与认知资产诊断”的材料分析官。

你会收到两类信息：
1. 已经过本地清噪的原始材料摘录。
2. 上一轮“材料规整官”输出的 structured_materials / project_fact_cards / career_facts。

你的任务是在“规整后的材料底稿”基础上，进一步生成能力诊断前置分析：识别项目线索、能力证据线索、认知资产线索、平台依赖线索，并形成优先追问地图。

你要完成四件事：
1. 给材料分型：简历/项目材料/公开文章/方法论笔记/复盘材料/他人反馈/零散自述/未知。
2. 抽取事实：职业经历、项目、结果、角色、动作、他人反馈、可量化指标。
3. 建立线索：成就故事线索、失败/受挫线索、能力证据线索、认知资产线索、平台依赖线索。
4. 生成追问地图：后续AI应该优先追问哪些缺口。
5. 做材料就绪判断：判断现在是否适合进入 AI 会前访谈，还是应该先让用户补充材料。

整理原则：
- 不做最终判断，不给商业方向。
- 不要把文章观点直接当成用户能力，要标注“观点/方法表达”与“真实项目证据”的区别。
- 简历通常提供职业事实，但动作和平台归因不足，要标缺口。
- 公众号文章/长文通常能体现认知资产，但不一定证明交付能力，要标缺口。
- 纯文字自述通常有主观表达，要追问具体事件。
- 所有推断都必须保守，缺证据就写“待追问”。

材料就绪判断规则：
- ready_for_interview：至少有基本履历/背景 + 1个以上真实项目或成果线索，AI 可以通过追问继续补齐细节。
- suggest_more_materials：已有一些线索，但材料偏薄、偏观点、偏简历摘要、缺少具体项目动作；建议先补材料，但也可以进入会前访谈边问边补。
- must_collect_more_materials：材料严重不足或大部分是噪音，缺少职业经历/真实项目/用户目标；直接进入会前访谈会非常空泛，应先补材料。

判断时要给顾问两个选择：
1. 让用户补充材料：给出可以直接发给用户的补充材料请求文案。
2. 仍然进入会前访谈：说明如果直接进入，AI 应该从哪些基础问题开始追问。

请只输出JSON，不要输出Markdown，不要输出额外文字。格式：
{
  "overview": "300-800字材料总览，说明材料构成、信息密度、最有价值的证据、最大缺口",
  "readiness_assessment": {"status":"ready_for_interview/suggest_more_materials/must_collect_more_materials","reason":"为什么这样判断","missing_materials":["缺少什么材料"],"suggested_user_request":"可以直接发给用户的补充材料请求文案","if_continue_interview_first_questions":["如果仍然进入会前访谈，建议AI先问的问题"],"advisor_recommendation":"给顾问的下一步建议"},
  "material_inventory": [{"name":"材料名","type":"简历/项目材料/公开文章/方法论笔记/复盘材料/他人反馈/零散自述/未知","information_value":"high/medium/low","summary":"这份材料主要包含什么","limitations":["局限1"]}],
  "career_timeline": [{"period":"时间段或未知","role_or_context":"岗位/场景","facts":["事实"],"evidence_source":"来自哪份材料"}],
  "project_clues": [{"project":"项目/事件名","known_facts":["已知事实"],"possible_actions":["用户可能做过的动作"],"known_results":["结果"],"missing_for_star":["缺少的STAR信息"],"evidence_source":"来源"}],
  "achievement_story_candidates": [{"story":"成就故事候选","why_candidate":"为什么值得深挖","missing_questions":["需要追问的问题"]}],
  "failure_or_constraint_candidates": [{"event":"失败/受挫/限制事件候选","why_useful":"为什么对平台剥离有用","missing_questions":["需要追问的问题"]}],
  "ability_evidence_clues": [{"ability_hint":"能力线索","evidence":["证据"],"risk":"证据不足/平台依赖/命名过泛/仅观点表达","next_questions":["下一步追问"]}],
  "cognitive_asset_clues": [{"level":"方法论/思维模型/元认知/科学真理/人文素养/世界观人生观价值观","clue":"认知资产线索","evidence":["证据"],"maturity_guess":"模糊经验/可复用雏形/成型方法论/待验证","next_questions":["下一步追问"]}],
  "platform_dependency_clues": [{"case_or_ability":"案例或能力","possible_platform_factors":["品牌/预算/团队/职位/渠道/客户/行业红利"],"next_questions":["下一步追问"]}],
  "priority_question_map": [{"priority":"P0/P1/P2","question":"后续AI或真人应追问的问题","why":"为什么优先"}]
}
"""

INTAKE_SYSTEM_PROMPT = """你是“能力资产与认知资产诊断”的AI前置采集官。

你的工作不是做最终诊断，也不是安慰、夸奖、包装用户。你的工作是像一个高水平行为事件访谈师、商业顾问助理和方法论研究员一样，通过连续追问，把用户的过往材料补充到足够让真人顾问判断。

你会收到一份上下文 JSON，其中可能包含：
1. session：用户档案和来访目标。
2. materials：经过本地清噪后的原始材料摘录，只用于核对原文和补充事实。
3. prior_reports：前置材料处理结果，尤其是：
   - material_structuring：材料规整底稿，包含规整后材料、职业事实、项目事实卡、方法/认知表达。
   - material_organization：材料分析结果，包含项目线索、能力证据线索、认知资产线索、平台依赖线索、优先追问地图。
4. conversation：用户与 AI 已经发生的问答。
5. interview_progress：当前会前访谈进度，包含已回答轮数、最新收束判断和信息充分度。
6. closure_assessment：如果存在，表示另一个模型刚刚完成的“是否可以收束”判断。

你必须优先使用 prior_reports 里的 material_structuring 和 material_organization 来判断下一问，不要每轮都从原始材料重新开始分析。materials 只用于核对来源和补充细节；conversation 用于判断用户已经回答了什么、哪些缺口已经补上、下一问该问什么。

最终要支持真人顾问回答四个问题：
1. 用户有哪些有证据支撑的能力资产？
2. 哪些能力离开平台、职位、团队、品牌、预算后仍可能成立？
3. 用户做成事情背后，是否存在可复用的方法论、思维模型、元认知和价值底座？
4. 哪些信息还不足，必须在真人咨询中继续追问？

你要使用的方法论：

【A. 行为取证：BEI / STAR / SOAR / CAR】
- 永远优先追问具体事件，而不是抽象自评。
- 一个可用故事至少包含：情境、障碍、用户个人动作、结果、他人反馈。
- 重点死磕 Action：用户本人具体做了什么，说了什么，判断了什么，推动了什么。
- 区分“我做了”和“我们做了”。凡是用户说“我们”，要追问“其中你个人负责的动作是什么”。

【B. 能力分类：KSAO + Data/People/Things】
- K 知识：行业知识、专业知识、流程知识。
- S 技能：可迁移动作能力、方法、工具、交付能力。
- A 天赋/能力倾向：学得快、看得准、组织强、判断快等倾向。
- O 风格/价值观：边界、偏好、动机、沟通方式。
- Data：分析、判断、研究、建模、策略。
- People：沟通、销售、组织、辅导、谈判、影响。
- Things：工具、系统、流程、运营、交付。

【C. 能力真伪检验：3个独立例证】
- 一项能力如果只出现在一个高光事件里，不能算稳定能力。
- 要追问它是否在至少3个互相独立的情境出现过。
- 独立情境可以是不同公司、项目、角色、行业、人生阶段、工作外场景。

【D. 能量检验：SIGN】
- Success：做完是否有成功感。
- Instinct：是否天然想靠近，别人没要求也会做。
- Growth：是否学得快、越做越长。
- Needs：做完是被点燃还是被掏空。
- 注意识别“伪强项”：做得好但长期消耗，不适合作为个人业务核心。

【E. 平台剥离：五桶归因】
对每个关键结果，追问用户把成功因素分到五个桶里，总和100%：
1. 我的能力。
2. 团队协作。
3. 平台资源：公司品牌、预算、流量、客户、工具、职位授权。
4. 运气。
5. 行业红利/外部趋势。
重点追问：
- 如果没有公司品牌背书，还能做到几成？
- 如果没有预算、团队和职位授权，还能做到几成？
- 换到一家没名气的小公司，哪些动作仍然能复用？
- 客户/同事到底是买你的能力，还是买平台背书？

【F. 认知资产分层】
不只看用户做了什么，还要追问用户为什么能反复做成。
从上到下识别：
1. 方法论：用户处理某类问题的成套步骤、SOP、框架、判断标准。
2. 思维模型：用户如何拆问题、找杠杆、做取舍、判断优先级。
3. 元子认知：用户如何观察自己的思考、复盘、识别偏差、调整策略。
4. 科学真理：用户是否重证据、因果、机制、实验、概率。
5. 人文素养：用户是否理解人的动机、关系、组织、叙事、情绪和文化。
6. 世界观/人生观/价值观：用户认为什么重要、什么不做、长期愿意为什么负责。

关键追问路径：
项目结果 -> 关键动作 -> 使用的方法 -> 背后的模型 -> 当时如何判断 -> 为什么认为这件事重要。

【G. 追问状态机】
你每次生成下一问前，先在内部判断当前处于哪个阶段：
0. 基础材料不足：缺少履历、项目、目标、材料。
1. 故事池不足：成就故事少于7个，失败/受挫事件少于2个。
2. 单故事不够深：故事缺情境、障碍、个人动作、结果、反馈。
3. 能力线索不稳：疑似能力没有3个独立例证。
4. 平台归因不清：无法区分个人、团队、平台、运气、红利。
5. 能量状态不清：不知道用户想不想长期做、是否被消耗。
6. 认知资产不清：看不到方法论、模型、元认知和价值底座。
7. 缺口补齐：只剩少数关键信息不足。
8. ready：材料足够生成真人顾问审阅包。

【H. 下一问选择算法】
你只能问一个主问题。选择下一问时按优先级：
1. 如果没有足够具体故事，先要故事，不要问能力标签。
2. 如果有故事但缺个人动作，追问“你本人具体做了什么”。
3. 如果结果看起来依赖平台，追问五桶归因和反事实剥离。
4. 如果能力只出现一次，追问第二、第三个独立例证。
5. 如果能力很强但能量未知，追问SIGN。
6. 如果故事已经完整，追问方法论/思维模型/元认知。
7. 如果所有核心模块都有中等以上信息，再输出 ready。

【I. 提问风格】
- 一次只问一个主问题，最多附带3个短提示。
- 问题要具体、自然、用户容易回答。
- 不要连续问一串审问式问题。
- 不要用术语压用户；术语只在内部使用。
- 每次都告诉用户“为什么问这个”。
- 不要夸用户，不要说“你很厉害”，不要下最终诊断。

【J. 停止条件】
你需要在每次生成下一问前，自己判断是否已经聊够。这里不另开检查点模型，由你在本轮判断。

当满足以下条件时输出 ready：
- 至少有3个较完整成就/项目故事，或材料中已有足够强项目证据。
- 至少有1-3个疑似核心能力，并且每个有一定证据。
- 对平台依赖已有初步判断线索。
- 对失败/约束/不舒服/被掏空的场景已有初步线索。
- 对能量/兴趣/边界已有初步判断线索。
- 至少识别出若干认知资产线索或确认暂时缺失。
- 剩余问题更适合真人顾问在会中追问，或者更适合生成会前整理后由顾问决定下一步。

不要过早 ready。以下情况必须继续追问：
- 只有抽象能力标签，没有具体事件。
- 只有一个高光案例，没有第二、第三个独立例证。
- 个人动作、团队动作、平台资源混在一起分不清。
- 只有成功案例，没有失败/消耗/不适/边界案例。
- 只知道用户做了什么，不知道他怎么判断、怎么拆解、怎么复盘。
- 用户回答明显很短、很泛，不能支撑真人顾问做判断。

如果信息已经很丰富但还没达到 ready，要把下一问升级为更高价值的问题，例如：
- 找一个失败/不舒服/被掏空的反例。
- 要求用户补第二、第三个独立例证。
- 追问一个成果离开平台后还能否复用。
- 追问方法论的适用边界。
- 追问用户不愿意长期承受的工作形态。

每一问都要尽量提高信息密度，避免寒暄、复述和低价值确认。

你必须只输出JSON，不要输出Markdown，不要输出额外文字。格式必须兼容：
{
  "status": "questioning" 或 "ready",
  "question": "如果status=questioning，这里是一段给用户的问题；如果status=ready则为空字符串",
  "why": "为什么问这个问题，给用户看的简短说明",
  "focus": "本轮追问聚焦：基础材料/故事取证/个人动作/能力分类/三例证/平台剥离/能量检验/认知资产/失败约束/迁移边界/ready",
  "missing": ["仍缺少的信息1", "仍缺少的信息2"],
  "confidence": "low/medium/high"
}
"""


CLOSURE_ASSESSMENT_PROMPT = """你是“能力资产与认知资产诊断”的会前访谈收束判断官。

你的任务不是继续追问，也不是生成报告，而是在每轮用户回答后，独立判断：现在是否已经足够进入“会前基本信息整理”，还是还值得继续问一轮。

你会收到上下文 JSON，包含：
1. session：用户档案和来访目标。
2. materials：材料摘录。
3. prior_reports：材料规整和材料分析结果。
4. conversation：用户与 AI 的完整问答。
5. interview_progress：当前已回答轮数和最新状态。

判断原则：
- 不按轮数机械停止。轮数只能帮助你理解用户投入程度，不能作为主要依据。
- 目标不是最终诊断，而是判断“是否足够让真人顾问准备下一次访谈”。
- 如果继续问只能得到边际很低的细节，应该收束。
- 如果缺口会直接影响真人顾问理解用户的关键经历、能力证据、平台依赖、失败约束或能量边界，应该继续问。
- 如果缺口更适合真人顾问在会中追问，而不是异步聊天中问，应该收束。

请按 7 个维度评估，每项给 0-100：
1. story_evidence：关键经历/项目故事是否够具体。
2. personal_action：用户个人动作是否清楚，是否能区分“我”和“我们”。
3. ability_signal：能力线索是否有证据，不只是标签。
4. platform_dependency：平台、职位、团队、品牌、预算等依赖是否已有初步线索。
5. failure_constraints：失败、卡点、不舒服、边界、能量消耗是否已有线索。
6. cognitive_assets：方法论、思维模型、元认知、价值底座是否已有线索。
7. advisor_readiness：真人顾问是否已经能基于现有信息准备一次高质量访谈。

收束标准：
- advisor_readiness >= 75，且没有 P0 级缺口，则 should_close=true。
- 即使某些维度低，只要这些缺口更适合真人顾问会中确认，也可以 should_close=true。
- 如果存在 P0 缺口：例如完全没有具体故事、完全看不出个人动作、完全无法理解用户来访目标，应 should_close=false。

请只输出JSON，不要输出Markdown，不要输出额外文字。格式必须兼容：
{
  "should_close": true或false,
  "readiness_score": 0到100,
  "stage_label": "继续取证/接近收束/可以收束",
  "reason": "给顾问看的判断理由，说明为什么继续或为什么收束",
  "user_visible_hint": "给用户看的简短进度说明，不要使用术语",
  "scores": {
    "story_evidence": 0,
    "personal_action": 0,
    "ability_signal": 0,
    "platform_dependency": 0,
    "failure_constraints": 0,
    "cognitive_assets": 0,
    "advisor_readiness": 0
  },
  "p0_gaps": ["必须继续补，否则无法准备真人访谈的缺口"],
  "p1_gaps": ["重要但可以会中继续确认的缺口"],
  "next_question_focus": "如果继续追问，下一问最应该聚焦什么",
  "close_when": "如果暂不收束，下一步达到什么条件就应该收束"
}
"""


REPORT_SYSTEM_PROMPT = """你是“能力资产与认知资产诊断”的真人顾问会前审阅材料整理官。

你的输出不是给用户看的最终报告，而是给真人顾问做第一次正式诊断前使用的工作底稿。真人顾问要靠你的材料快速理解用户、发现证据、识别疑点、安排会中追问。

你的核心任务：
1. 把用户上传材料、AI追问和用户回答，整理成结构化审阅包。
2. 抽取事实，不要编造。
3. 把事实、推断、待验证分开。
4. 找出疑似能力资产、认知资产、平台依赖、能量风险。
5. 给真人顾问一份高优先级会中追问议程。

整理原则：

【A. 证据先行】
- 每个能力候选、认知资产候选、平台归因判断，都必须尽量附带证据。
- 证据可以来自：上传材料原文、用户回答、具体项目事实、结果数据、他人反馈。
- 如果没有证据，明确标注“待验证”，不要写成结论。

【B. 区分三类信息】
- 事实：用户明确说过或材料中出现过。
- 推断：你根据多个事实做出的判断。
- 待验证：还缺关键证据，需要真人追问。

【C. 不做的事】
- 不下最终商业方向裁决。
- 不承诺用户能变现。
- 不把用户包装成“天生适合某方向”。
- 不把单个高光事件当稳定能力。
- 不把平台成果直接记为个人能力。

【D. 能力候选整理规则】
每个能力候选尽量包含：
- 能力名：用可迁移、可交付的语言命名，不要太泛。
- KSAO：知识/技能/天赋倾向/风格价值观。
- 三轴：Data/People/Things。
- 证据：来自哪些故事。
- 独立例证数量：没有就写0或1。
- 平台依赖风险。
- 能量风险。
- 真人追问建议。

【E. 认知资产整理规则】
按用户的认知体系分层：
1. 方法论：成套步骤、流程、SOP、框架、判断标准。
2. 思维模型：拆问题、做取舍、找杠杆、判断优先级的模型。
3. 元子认知：复盘、识别偏差、自我修正、学习策略。
4. 科学真理：证据、因果、机制、实验、概率意识。
5. 人文素养：人性、关系、组织、叙事、情绪、文化理解。
6. 世界观/人生观/价值观：长期偏好、边界、不做什么、认为重要的事。

不要把“用户提到一个词”就当认知资产。要判断成熟度：
- 模糊经验：能说感受，但没有步骤和证据。
- 可复用雏形：能说出若干步骤或判断标准，有1-2个案例。
- 成型方法论：能拆成流程，有多个案例，可教给别人。

【F. 平台剥离整理规则】
对关键案例和能力，整理五桶归因线索：
- 个人能力。
- 团队协作。
- 平台资源：品牌、预算、流量、客户、工具、职位授权。
- 运气。
- 行业红利。
如果用户没有给百分比，不要编数字；写成“倾向/疑点/待追问”。

【G. 会中议程】
真人顾问时间有限，你要按优先级给议程：
P0：必须追问，否则无法判断能力真伪或平台依赖。
P1：重要追问，有助于能力命名和认知资产确认。
P2：可选追问，用于补充背景或表达。

输出质量要求：
- human_review_brief 要像一份会前 briefing，不要泛泛总结。
- 表格项要具体，不要写“沟通能力”“学习能力”这种空标签，除非有进一步定义。
- 对风险要尖锐：平台依赖、证据不足、能量消耗、方法论不成型。
- 明确告诉真人：下一次最值得死磕哪3-5个问题。

请只输出JSON，不要输出Markdown之外的额外解释。格式必须兼容：
{
  "human_review_brief": "给真人顾问的1000-2000字审阅摘要。包含：用户背景、主要材料、疑似能力主线、认知资产线索、平台依赖疑点、会中优先判断点。",
  "material_digest": [{"title":"材料/经历摘要标题","facts":["事实1","事实2"],"notes":"备注"}],
  "story_evidence_table": [{"story":"事件名","situation":"情境","obstacle":"障碍","actions":["用户个人动作"],"results":["结果"],"feedback":["反馈"],"evidence_strength":"low/medium/high","gaps":["缺口"]}],
  "ability_candidates": [{"ability":"能力名","ksao":"K/S/A/O","axis":"Data/People/Things","evidence":["证据"],"independent_examples_count":0,"confidence":"low/medium/high","risk":"平台依赖/证据不足/能量消耗/命名过泛等风险"}],
  "cognitive_asset_map": [{"level":"方法论/思维模型/元认知/科学真理/人文素养/世界观人生观价值观","asset":"认知资产名","evidence":["证据"],"maturity":"模糊经验/可复用雏形/成型方法论","questions":["待追问"]}],
  "platform_attribution_notes": [{"case_or_ability":"案例或能力","personal":"个人能力贡献线索","team":"团队因素","platform":"平台/职位/品牌/预算因素","luck_or_trend":"运气/行业红利","questions":["需要真人追问"]}],
  "energy_notes": [{"ability_or_story":"能力或事件","signals":["SIGN线索"],"energy_risk":"能量风险"}],
  "missing_information": ["缺口1","缺口2"],
  "next_live_session_agenda": [{"topic":"真人咨询议题","questions":["问题1","问题2"],"priority":"high/medium/low"}]
}
"""

ABILITY_DELIVERY_PROMPT = """你是“能力资产与认知资产诊断”的阶段性交付报告整理官。

你的输出是给用户看的第一阶段交付，不是商业方向报告，也不是最终变现方案。此阶段只交付“能力层面”的诊断结果：用户到底沉淀了哪些能力资产，哪些能力还只是负债/风险/待验证项，哪些证据支撑这些判断，下一阶段商业切口验证前还需要补什么。

请基于用户上传材料、AI追问、用户回答、材料整理结果和真人会谈补充，输出一份保守、证据优先、用户能看懂的能力资产交付包。

核心原则：
1. 不承诺变现，不直接给商业模式结论。
2. 不把平台成果等同于个人能力。
3. 能力必须有案例证据；没有证据就放入待验证或负债。
4. 用“资产/负债/待验证”的语言帮助用户建立清晰自我认知。
5. 语言要清楚、具体、克制，避免夸张包装。

能力资产负债表定义：
- 能力资产：有证据、可迁移、用户能相对稳定复用，未来可能用于个人业务验证的能力。
- 能力负债：用户以为自己有但证据不足；或者依赖平台/团队/职位/预算；或者做得好但高消耗；或者不可独立交付。
- 待验证资产：有潜力，但还缺独立例证、客户反馈、平台剥离证据或能量验证。

请只输出JSON，不要输出Markdown，不要输出额外文字。格式：
{
  "delivery_summary": "给用户看的300-600字阶段性总结：这次诊断看到了什么、还不能判断什么、下一阶段应怎么理解",
  "ability_asset_map": [{"asset_name":"能力资产名称","plain_language":"用用户能懂的话解释这个能力是什么","evidence_cases":["证据案例1"],"ksao_type":"K知识/S技能/A能力倾向/O风格价值观","data_people_things":"Data/People/Things","transferability":"high/medium/low","confidence":"high/medium/low","why_it_matters":"为什么这是资产"}],
  "ability_balance_sheet": [{"item":"能力/资源/风险项","side":"asset/liability/to_verify","current_judgment":"当前判断","evidence":["证据"],"risk_or_gap":"风险或缺口","next_validation":"下一步如何验证"}],
  "platform_dependency_table": [{"case_or_ability":"案例或能力","personal_part":"个人能力部分","platform_part":"平台/职位/团队/品牌/预算部分","judgment":"离开平台后能否成立的初步判断","missing_evidence":["缺失证据"]}],
  "cognitive_asset_map": [{"level":"方法论/思维模型/元认知/科学真理/人文素养/世界观人生观价值观","asset":"认知资产名称","evidence":["证据"],"maturity":"模糊经验/可复用雏形/成型方法论/待验证","how_to_strengthen":"如何继续沉淀"}],
  "energy_and_fit_notes": [{"ability_or_activity":"能力或活动","energy_signal":"点燃/中性/消耗/未知","evidence":["证据"],"judgment":"是否适合长期作为个人业务能力底座"}],
  "not_yet_conclusions": ["现在还不能下的结论"],
  "recommended_next_step": "下一阶段建议：通常是进入商业切口验证，或继续补能力证据",
  "user_facing_closing": "给用户看的结束语，克制、清楚、有下一步"
}
"""


CLIENT_PRE_SESSION_BRIEF_PROMPT = """你是“能力资产与认知资产诊断”的会前信息整理官。

你的输出是在用户完成 AI 会前追问后，给用户和真人顾问共同查看的“下一次真人访谈前基本信息整理”。它不是最终诊断报告，不是能力资产交付包，也不是商业方向判断。

这份材料要解决两个问题：
1. 让用户看到：AI 已经把他的材料和回答整理好了，下一次真人访谈会围绕哪些真实经历继续深入。
2. 让真人顾问看到：用户已经提供了哪些事实、能力线索、仍需会中确认的缺口，并形成“访谈导航”，而不是一份逐字照问的问题清单。

整理原则：
- 只整理已经出现的信息，不编造。
- 把“事实”“线索”“待确认”分开。
- 不下最终能力结论，不承诺变现，不给商业模式。
- 对用户可见的表达要清楚、克制、安心，但不要夸张包装。
- 对顾问可见的备注要指出下一次访谈最值得判断的主线、验证点和风险，不要把顾问变成照稿提问的机器人。
- 如果材料中有隐私、公司敏感信息、未经验证数字，只概括，不扩散细节。
- 如果某些缺口更适合 AI 继续异步追问，请单独标注；如果需要真人现场判断、追问和收敛，也要明确标注。

重点整理内容：
1. 用户当前来访目标和主要困惑。
2. 已收集到的材料类型和信息密度。
3. 已出现的关键项目/经历线索。
4. 已出现的能力线索，但只称为“线索”，不要称为结论。
5. 已出现的认知资产线索：方法论、思维模型、元认知、科学真理、人文素养、世界观/人生观/价值观。
6. 下一次真人访谈导航：不是逐字问题，而是 3-5 条高价值判断主线，例如个人能力归因、平台依赖、三例证、能量状态、认知资产成熟度。
7. 用户下一步需要准备什么。

请只输出JSON，不要输出Markdown，不要输出额外文字。格式：
{
  "user_summary": "给用户看的300-600字会前整理摘要：你已提供了什么、AI初步看到了哪些经历线索、下一次真人访谈会继续确认什么。",
  "confirmed_information": [{"title":"已确认信息标题","details":["事实1","事实2"],"source":"来自材料/用户回答/目标描述"}],
  "key_story_clues": [{"story":"项目或经历线索","known_facts":["已知事实"],"why_discuss_next":"为什么下一次真人访谈值得深入","missing_details":["还缺什么"]}],
  "ability_clues": [{"clue":"疑似能力线索","evidence":["已有证据"],"status":"仅线索/证据较强/待验证","risk_or_gap":"平台依赖/证据不足/动作不清/能量未知等"}],
  "cognitive_asset_clues": [{"level":"方法论/思维模型/元认知/科学真理/人文素养/世界观人生观价值观","clue":"认知资产线索","evidence":["已有证据"],"next_probe":"真人访谈中如何继续确认"}],
  "open_questions_for_live_session": [{"question":"真人访谈导航议题，不要写成审问式长问题","why":"这个议题要帮助顾问判断什么","priority":"P0/P1/P2"}],
  "what_to_prepare_next": ["用户下一次访谈前可以准备的材料或例子"],
  "user_facing_next_step": "给用户看的下一步说明：信息已基本够进入真人访谈，接下来等待顾问约时间/确认访谈。",
  "advisor_scheduling_note": "给顾问看的约访备注：建议约多久、会中优先判断哪3-5条主线、哪些判断暂时不能做、哪些缺口可以交给AI继续追问。"
}
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs():
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    ensure_dirs()
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                contact TEXT,
                goal TEXT,
                status TEXT NOT NULL DEFAULT 'intake',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                size INTEGER NOT NULL,
                extracted_text TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                content_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
            """
        )


def touch_session(conn, session_id, status=None):
    if status:
        conn.execute(
            "UPDATE sessions SET updated_at=?, status=? WHERE id=?",
            (now_iso(), status, session_id),
        )
    else:
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def send_openai_json(system_prompt, user_payload, temperature=0.2, max_tokens=None):
    global LAST_USED_MODEL
    if not OPENAI_API_KEY:
        raise RuntimeError("还没有设置 OPENAI_API_KEY。请用 OPENAI_API_KEY=你的key ./run.sh 启动服务后再使用AI整理/追问。")

    payload = call_openai_json(system_prompt, user_payload, temperature=temperature, max_tokens=max_tokens)
    LAST_USED_MODEL = payload.get("model") or DEFAULT_MODEL
    text = extract_response_text(payload)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model did not return valid JSON: {text[:1000]}") from e


def call_openai_json(system_prompt, user_payload, temperature=None, max_tokens=None):
    if not prefers_responses_api():
        try:
            return call_openai_chat_completions(system_prompt, user_payload, temperature=temperature, max_tokens=max_tokens)
        except RuntimeError as e:
            if not should_fallback_to_responses(str(e)):
                raise
            return call_openai_responses(system_prompt, user_payload, temperature=temperature, max_tokens=max_tokens)
    try:
        return call_openai_responses(system_prompt, user_payload, temperature=temperature, max_tokens=max_tokens)
    except RuntimeError as e:
        if not should_fallback_to_chat_completions(str(e)):
            raise
        return call_openai_chat_completions(system_prompt, user_payload, temperature=temperature, max_tokens=max_tokens)


def prefers_responses_api():
    host = urlparse(OPENAI_BASE_URL).netloc.lower()
    return host.endswith("openai.com")


def should_fallback_to_responses(message):
    text = message.lower()
    return any(
        marker in text
        for marker in [
            "not found",
            "404",
            "chat/completions",
            "unsupported url",
            "unsupported endpoint",
            "invalid request",
        ]
    )


def call_openai_responses(system_prompt, user_payload, temperature=None, max_tokens=None):
    body = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_output_tokens"] = max_tokens
    return request_openai(body, retry_without_temperature=True)


def call_openai_chat_completions(system_prompt, user_payload, temperature=None, max_tokens=None):
    body = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return request_openai_chat(body, retry_without_temperature=True)


def should_fallback_to_chat_completions(message):
    text = message.lower()
    return any(
        marker in text
        for marker in [
            "not found",
            "404",
            "responses",
            "unsupported url",
            "unsupported endpoint",
            "invalid request",
            "response_format",
        ]
    )


def request_openai(body, retry_without_temperature=False):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OPENAI_BASE_URL}/responses",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=MODEL_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if retry_without_temperature and is_unsupported_temperature_error(detail) and "temperature" in body:
            clean_body = dict(body)
            clean_body.pop("temperature", None)
            return request_openai(clean_body, retry_without_temperature=False)
        if is_unsupported_token_limit_error(detail):
            clean_body = dict(body)
            clean_body.pop("max_output_tokens", None)
            return request_openai(clean_body, retry_without_temperature=retry_without_temperature)
        raise RuntimeError(format_openai_error(e.code, detail)) from e
    except urllib.error.URLError as e:
        reason = str(e.reason) if getattr(e, "reason", None) else str(e)
        if "timed out" in reason or "Operation timed out" in reason:
            raise RuntimeError("连接 OpenAI 超时：请检查网络或稍后重试。") from e
        if "SSL" in reason or "EOF" in reason:
            raise RuntimeError("连接 OpenAI 时 SSL 连接中断：通常是网络代理/网络波动导致，请稍后重试。") from e
        raise RuntimeError(f"连接 OpenAI 失败：{reason}") from e
    except http.client.RemoteDisconnected as e:
        raise RuntimeError("连接 OpenAI 被远端断开：通常是网络代理、网络波动或上游接口临时中断导致，请稍后重试。") from e
    except socket.timeout as e:
        raise RuntimeError("连接 OpenAI 超时：请检查网络或稍后重试。") from e


def request_openai_chat(body, retry_without_temperature=False):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=MODEL_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if retry_without_temperature and is_unsupported_temperature_error(detail) and "temperature" in body:
            clean_body = dict(body)
            clean_body.pop("temperature", None)
            return request_openai_chat(clean_body, retry_without_temperature=False)
        if is_unsupported_token_limit_error(detail):
            clean_body = dict(body)
            clean_body.pop("max_tokens", None)
            clean_body.pop("max_completion_tokens", None)
            return request_openai_chat(clean_body, retry_without_temperature=retry_without_temperature)
        if retry_without_temperature and is_unsupported_response_format_error(detail) and "response_format" in body:
            clean_body = dict(body)
            clean_body.pop("response_format", None)
            clean_body["messages"] = force_json_instruction(clean_body["messages"])
            return request_openai_chat(clean_body, retry_without_temperature=False)
        raise RuntimeError(format_openai_error(e.code, detail)) from e
    except urllib.error.URLError as e:
        reason = str(e.reason) if getattr(e, "reason", None) else str(e)
        if "timed out" in reason or "Operation timed out" in reason:
            raise RuntimeError("连接模型接口超时：请检查网络或稍后重试。") from e
        if "SSL" in reason or "EOF" in reason:
            raise RuntimeError("连接模型接口时 SSL 连接中断：通常是网络代理/网络波动导致，请稍后重试。") from e
        raise RuntimeError(f"连接模型接口失败：{reason}") from e
    except http.client.RemoteDisconnected as e:
        raise RuntimeError("连接模型接口被远端断开：通常是网络代理、网络波动或上游接口临时中断导致，请稍后重试。") from e
    except socket.timeout as e:
        raise RuntimeError("连接模型接口超时：请检查网络或稍后重试。") from e


def force_json_instruction(messages):
    updated = list(messages)
    updated[0] = dict(updated[0])
    updated[0]["content"] = updated[0]["content"] + "\n\n无论如何，你必须只输出一个合法 JSON 对象，不要输出 Markdown。"
    return updated


def is_unsupported_response_format_error(detail):
    try:
        payload = json.loads(detail)
        message = (payload.get("error", {}).get("message") or "").lower()
    except Exception:
        message = detail.lower()
    return "response_format" in message and ("unsupported" in message or "not support" in message)


def is_unsupported_temperature_error(detail):
    try:
        payload = json.loads(detail)
        message = (payload.get("error", {}).get("message") or "").lower()
    except Exception:
        message = detail.lower()
    return "temperature" in message and "unsupported" in message


def is_unsupported_token_limit_error(detail):
    try:
        payload = json.loads(detail)
        message = (payload.get("error", {}).get("message") or "").lower()
    except Exception:
        message = detail.lower()
    return ("max_tokens" in message or "max_output_tokens" in message or "max_completion_tokens" in message) and (
        "unsupported" in message or "not support" in message or "unknown parameter" in message
    )


def format_openai_error(status_code, detail):
    try:
        payload = json.loads(detail)
        err = payload.get("error", {})
        code = err.get("code") or ""
        message = err.get("message") or detail
        if code == "insufficient_quota":
            return "OpenAI 额度不足或账单未开通：请检查这个 Key 的 billing/额度，或换一个可用 Key。"
        if is_unsupported_temperature_error(detail):
            return "当前模型不支持 temperature 参数，系统已调整为自动去掉该参数；请再试一次。"
        if code in {"model_not_found", "invalid_model"}:
            return f"模型不可用：当前配置模型是 {DEFAULT_MODEL}，请换成你的账号可调用的模型。OpenAI 返回：{message}"
        if status_code == 401:
            return "OpenAI API Key 无效或没有权限：请重新设置 Key。"
        if status_code == 429:
            return f"OpenAI 请求被限流或额度不足：{message}"
        return f"OpenAI API error {status_code}: {message}"
    except Exception:
        return f"OpenAI API error {status_code}: {detail[:1000]}"


def extract_response_text(payload):
    if "output_text" in payload:
        return payload["output_text"]
    if payload.get("choices"):
        message = payload["choices"][0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text") or item.get("content") or "")
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content).strip()
    chunks = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
            elif "text" in content:
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip()


def extract_text(path, filename):
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".html"}:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        if pdfplumber:
            parts = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    parts.append(page.extract_text() or "")
            return "\n\n".join(parts).strip()
        return "[PDF text extraction unavailable]"
    if suffix == ".docx":
        if Document:
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return "[DOCX text extraction unavailable]"
    return Path(path).read_text(encoding="utf-8", errors="replace")


def html_to_text(raw_html):
    raw_html = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    raw_html = re.sub(r"(?is)<script\b.*$", " ", raw_html)
    raw_html = re.sub(r"(?is)<style.*?>.*?</style>", " ", raw_html)
    raw_html = re.sub(r"(?is)<style\b.*$", " ", raw_html)
    raw_html = re.sub(r"(?is)<br\s*/?>", "\n", raw_html)
    raw_html = re.sub(r"(?is)</p\s*>", "\n", raw_html)
    text = re.sub(r"(?is)<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def extract_wechat_article_text(raw_html):
    title = extract_html_text_by_pattern(raw_html, r'(?is)<h1[^>]+id=["\']activity-name["\'][^>]*>(.*?)</h1>')
    author = extract_html_text_by_pattern(raw_html, r'(?is)<span[^>]+id=["\']js_name["\'][^>]*>(.*?)</span>')
    content = ""
    start_match = re.search(r'(?is)<div[^>]+id=["\']js_content["\'][^>]*>', raw_html)
    if start_match:
        fragment = raw_html[start_match.end():]
        end_match = re.search(r'(?is)<script\b|<div[^>]+id=["\']js_pc_qr_code["\']|<div[^>]+class=["\'][^"\']*rich_media_tool', fragment)
        if end_match:
            fragment = fragment[: end_match.start()]
        content = html_to_text(fragment)
    if not content:
        content = html_to_text(raw_html)
    parts = []
    if title:
        parts.append(title)
    if author:
        parts.append(author)
    parts.append(content)
    return "\n".join(part for part in parts if part).strip()


def extract_html_text_by_pattern(raw_html, pattern):
    match = re.search(pattern, raw_html)
    if not match:
        return ""
    return html_to_text(match.group(1))


NOISE_LINE_PATTERNS = [
    r"^去阅读$",
    r"^在.*阅读器.*阅读$",
    r"^在小说阅读器读本章$",
    r"^微信扫一扫",
    r"^分享到",
    r"^收藏$",
    r"^点赞$",
    r"^在看$",
    r"^阅读原文$",
    r"^展开全文$",
    r"^收起$",
    r"^复制链接$",
    r"^打开.*app$",
    r"^广告$",
    r"^相关推荐$",
    r"^免责声明",
    r"^版权归.*所有$",
]


def clean_material_text(raw_text):
    text = html.unescape(raw_text or "")
    text = re.split(r"(?m)^var __INLINE_SCRIPT__\b|^function _|^var Vue__default\b", text, maxsplit=1)[0]
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    cleaned_lines = []
    previous = ""
    seen_short = {}
    dropped_noise = 0
    dropped_duplicates = 0
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if is_noise_line(line):
            dropped_noise += 1
            continue
        normalized = line.lower()
        if normalized == previous.lower():
            dropped_duplicates += 1
            continue
        if len(line) <= 28:
            seen_short[normalized] = seen_short.get(normalized, 0) + 1
            if seen_short[normalized] > 2:
                dropped_duplicates += 1
                continue
        cleaned_lines.append(line)
        previous = line

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    notes = []
    if dropped_noise:
        notes.append(f"已过滤疑似网页/格式噪音 {dropped_noise} 行")
    if dropped_duplicates:
        notes.append(f"已过滤重复短行/连续重复 {dropped_duplicates} 行")
    if len(cleaned) < len(raw_text or ""):
        notes.append(f"清洗后约为原文本 {round(len(cleaned) / max(len(raw_text or ''), 1) * 100)}%")
    return cleaned, notes


def is_noise_line(line):
    if len(line) <= 1:
        return True
    if re.fullmatch(r"[\W_]+", line):
        return True
    for pattern in NOISE_LINE_PATTERNS:
        if re.search(pattern, line, re.I):
            return True
    if len(line) <= 12 and re.fullmatch(r"[\d\s:/.\-]+", line):
        return True
    return False


def fetch_url_text(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 AbilityIntake/0.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read(2_000_000)
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    text = raw.decode(charset, errors="replace")
    if "html" in content_type or "<html" in text[:1000].lower():
        if "mp.weixin.qq.com" in url:
            return extract_wechat_article_text(text)
        return html_to_text(text)
    return text.strip()


def get_session_context(
    session_id,
    material_excerpt_chars=20000,
    total_material_chars=80000,
    include_report_content=True,
):
    with db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise KeyError("session not found")
        files = conn.execute(
            "SELECT id, original_name, extracted_text, created_at FROM files WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        messages = conn.execute(
            "SELECT role, content, meta_json, created_at FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        reports = conn.execute(
            "SELECT report_type, content_json, created_at FROM reports WHERE session_id=? ORDER BY id DESC LIMIT 5",
            (session_id,),
        ).fetchall()
    materials = []
    remaining_chars = total_material_chars
    for f in files:
        raw_text = f["extracted_text"] or ""
        text, cleaning_notes = clean_material_text(raw_text)
        excerpt_limit = max(0, min(material_excerpt_chars, remaining_chars))
        excerpt = text[:excerpt_limit]
        remaining_chars -= len(excerpt)
        notes = list(cleaning_notes)
        if len(text) > len(excerpt):
            notes.append(f"送入模型时已截取前 {len(excerpt)} 字，原清洗后全文 {len(text)} 字")
        materials.append(
            {
                "file_id": f["id"],
                "name": f["original_name"],
                "excerpt": excerpt,
                "length": len(text),
                "raw_length": len(raw_text),
                "cleaning_notes": notes,
            }
        )
    conversation = [row_to_dict(m) for m in messages]
    report_context = []
    for r in reports:
        content = r["content_json"] or "{}"
        try:
            parsed_content = json.loads(content)
        except Exception:
            parsed_content = {"raw": content[:16000]}
        item = {
            "report_type": r["report_type"],
            "created_at": r["created_at"],
            "content_excerpt": content[:16000],
        }
        if include_report_content:
            item["content"] = parsed_content
        report_context.append(item)
    return {
        "session": row_to_dict(session),
        "materials": materials,
        "conversation": conversation,
        "interview_progress": build_interview_progress(conversation, row_to_dict(session)),
        "prior_reports": report_context,
    }


def parse_message_meta(meta_text):
    if not meta_text:
        return {}
    try:
        return json.loads(meta_text)
    except Exception:
        return {}


def count_ai_questions(conversation):
    count = 0
    for message in conversation:
        if message.get("role") != "assistant":
            continue
        meta = parse_message_meta(message.get("meta_json"))
        if meta.get("status") == "questioning" or meta.get("question") or meta.get("focus"):
            count += 1
    return count


def count_user_answers(conversation):
    return sum(1 for message in conversation if message.get("role") == "user")


def latest_interview_focus(conversation):
    for message in reversed(conversation):
        if message.get("role") != "assistant":
            continue
        meta = parse_message_meta(message.get("meta_json"))
        if meta.get("focus"):
            return meta.get("focus")
    return ""


def latest_closure_assessment(conversation):
    for message in reversed(conversation):
        meta = parse_message_meta(message.get("meta_json"))
        assessment = meta.get("closure_assessment")
        if isinstance(assessment, dict):
            return assessment
    return {}


def build_interview_progress(conversation, session=None):
    answered = count_user_answers(conversation)
    asked = count_ai_questions(conversation)
    status = (session or {}).get("status") or ""
    is_done = status in {"ready_for_report", "client_brief_ready", "ability_delivered", "report_ready"}
    assessment = latest_closure_assessment(conversation)
    readiness = int(assessment.get("readiness_score") or (100 if is_done else 0))
    return {
        "answered_rounds": answered,
        "asked_rounds": asked,
        "percent": max(0, min(readiness, 100)),
        "focus": latest_interview_focus(conversation),
        "is_done": is_done,
        "closure_assessment": assessment,
        "can_wrap_soon": bool(assessment.get("stage_label") in {"接近收束", "可以收束"} or assessment.get("should_close")),
    }


def organize_materials(session_id):
    context = get_session_context(
        session_id,
        material_excerpt_chars=50000,
        total_material_chars=250000,
        include_report_content=False,
    )
    context["prior_reports"] = []
    context["conversation"] = []
    structured = structure_materials_by_item(context)
    analysis_context = dict(context)
    analysis_context["structured_material_draft"] = structured
    result = send_openai_json(MATERIAL_ORGANIZER_PROMPT, analysis_context, temperature=0.1)
    with db() as conn:
        conn.execute(
            "INSERT INTO reports(session_id, report_type, content_json, created_at) VALUES(?,?,?,?)",
            (session_id, "material_structuring", json.dumps(structured, ensure_ascii=False), now_iso()),
        )
        conn.execute(
            "INSERT INTO reports(session_id, report_type, content_json, created_at) VALUES(?,?,?,?)",
            (session_id, "material_organization", json.dumps(result, ensure_ascii=False), now_iso()),
        )
        touch_session(conn, session_id, "materials_organized")
    return result


def structure_materials_by_item(context):
    materials = context.get("materials", [])
    if not materials:
        return send_openai_json(MATERIAL_STRUCTURING_PROMPT, context, temperature=0.1)

    parts = []
    for material in materials:
        item_context = {
            "session": context.get("session", {}),
            "materials": [material],
            "conversation": [],
            "prior_reports": [],
        }
        parts.append(send_openai_json(MATERIAL_STRUCTURING_PROMPT, item_context, temperature=0.1))
    return merge_material_structuring(parts, materials)


def merge_material_structuring(parts, materials):
    overview_bits = []
    merged = {
        "structured_overview": "",
        "cleaning_summary": [],
        "structured_materials": [],
        "career_facts": [],
        "project_fact_cards": [],
        "method_or_cognitive_expressions": [],
        "not_useful_or_noisy_content": [],
    }
    for idx, part in enumerate(parts):
        if part.get("structured_overview"):
            overview_bits.append(f"材料{idx + 1}：{part.get('structured_overview')}")
        for key in [
            "cleaning_summary",
            "structured_materials",
            "career_facts",
            "project_fact_cards",
            "method_or_cognitive_expressions",
            "not_useful_or_noisy_content",
        ]:
            value = part.get(key)
            if isinstance(value, list):
                merged[key].extend(value)
            elif value:
                merged[key].append(value)
    merged["structured_overview"] = (
        f"已按单份材料完成深度规整，共处理 {len(materials)} 份材料。"
        "每份材料均使用完整材料规整方法处理，以下为合并后的材料底稿。"
        + ("\n" + "\n".join(overview_bits) if overview_bits else "")
    )
    return merged


def build_local_material_structuring(context):
    materials = context.get("materials", [])
    structured_materials = []
    cleaning_summary = []
    for material in materials:
        name = material.get("name") or "未命名材料"
        excerpt = material.get("excerpt") or ""
        material_type = guess_material_type(name, excerpt)
        cleaning_summary.append(
            {
                "material_name": name,
                "raw_length": material.get("raw_length", 0),
                "cleaned_length": material.get("length", 0),
                "cleaning_notes": material.get("cleaning_notes", []),
                "remaining_noise_risk": "high" if material.get("raw_length", 0) > 100000 else "medium",
                "usefulness": "medium" if excerpt else "low",
            }
        )
        structured_materials.append(
            {
                "material_name": name,
                "material_type": material_type,
                "clean_summary": excerpt[:800] if excerpt else "材料未提供可用文本",
                "key_facts": [],
                "user_claims_or_views": [],
                "project_or_story_mentions": [],
                "result_or_metric_mentions": [],
                "source_limitations": material.get("cleaning_notes", []),
            }
        )
    return {
        "structured_overview": f"本地已完成材料抽取和初步清洗，共 {len(materials)} 份材料。由于部分链接正文体量较大，送入模型前已按材料和总量预算截取，后续AI分析应优先把这些材料作为线索来源，而不是最终事实结论。",
        "cleaning_summary": cleaning_summary,
        "structured_materials": structured_materials,
        "career_facts": [],
        "project_fact_cards": [],
        "method_or_cognitive_expressions": [],
        "not_useful_or_noisy_content": [],
    }


def guess_material_type(name, text):
    source = f"{name}\n{text[:500]}".lower()
    if any(word in source for word in ["简历", "resume", "教育经历", "工作经历"]):
        return "简历"
    if any(word in source for word in ["复盘", "项目", "sop", "方案", "交付"]):
        return "项目材料"
    if any(word in source for word in ["公众号", "来源链接", "阅读", "文章"]):
        return "公开文章"
    if any(word in source for word in ["方法论", "模型", "框架"]):
        return "方法论笔记"
    if "用户补充文本" in name:
        return "零散自述"
    return "未知"


def create_question(session_id):
    context = get_session_context(session_id)
    result = send_openai_json(INTAKE_SYSTEM_PROMPT, context)
    role_content = result.get("question") or build_ready_user_reply(result)
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, meta_json, created_at) VALUES(?,?,?,?,?)",
            (session_id, "assistant", role_content, json.dumps(result, ensure_ascii=False), now_iso()),
        )
        if result.get("status") == "ready":
            touch_session(conn, session_id, "ready_for_report")
        else:
            touch_session(conn, session_id, "questioning")
    return result


def build_ready_user_reply(result):
    reason = result.get("why") or "你已经补充了足够多的关键经历、个人动作和判断线索。"
    missing = result.get("missing") or []
    parts = [
        "收到，这一轮会前访谈的信息已经基本够用了。",
        reason,
    ]
    if missing:
        parts.append("后面如果继续深入，顾问会重点确认：" + "；".join(str(item) for item in missing[:3]) + "。")
    parts.append("接下来你不需要继续在这里回答问题了。可以先点击“生成会前整理”，查看系统整理出的会前基本信息；之后顾问会基于完整记录和你继续做真人访谈。")
    return "\n\n".join(parts)


def should_assess_interview_closure(context):
    return False


def assess_interview_closure(context):
    result = send_openai_json(CLOSURE_ASSESSMENT_PROMPT, context, temperature=0.1, max_tokens=2200)
    if "should_close" not in result:
        result["should_close"] = False
    if "readiness_score" not in result:
        result["readiness_score"] = 0
    return result


def generate_report(session_id):
    context = get_session_context(session_id)
    result = send_openai_json(REPORT_SYSTEM_PROMPT, context, temperature=0.1)
    with db() as conn:
        conn.execute(
            "INSERT INTO reports(session_id, report_type, content_json, created_at) VALUES(?,?,?,?)",
            (session_id, "human_review_pack", json.dumps(result, ensure_ascii=False), now_iso()),
        )
        touch_session(conn, session_id, "report_ready")
    return result


def generate_client_brief(session_id):
    context = get_session_context(session_id)
    result = send_openai_json(CLIENT_PRE_SESSION_BRIEF_PROMPT, context, temperature=0.1)
    with db() as conn:
        conn.execute(
            "INSERT INTO reports(session_id, report_type, content_json, created_at) VALUES(?,?,?,?)",
            (session_id, "client_pre_session_brief", json.dumps(result, ensure_ascii=False), now_iso()),
        )
        touch_session(conn, session_id, "client_brief_ready")
    return result


def client_chat_turn(session_id, content):
    content = content.strip()
    if not content:
        raise ValueError("content required")
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, meta_json, created_at) VALUES(?,?,?,?,?)",
            (session_id, "user", content, None, now_iso()),
        )
        touch_session(conn, session_id, "questioning")
    question = create_question(session_id)
    return {"question": question, "brief": None}


MANUAL_AI_TASKS = {
    "material_structuring": {
        "title": "材料规整",
        "prompt": MATERIAL_STRUCTURING_PROMPT,
        "report_type": "material_structuring",
        "task": "请阅读客户材料，先做材料规整，只清理和整理材料，不做能力判断。",
    },
    "material_organization": {
        "title": "材料分析",
        "prompt": MATERIAL_ORGANIZER_PROMPT,
        "report_type": "material_organization",
        "task": "请基于客户材料和已有规整底稿，完成能力诊断前置分析、材料就绪判断和追问地图。",
    },
    "next_question": {
        "title": "生成下一问",
        "prompt": INTAKE_SYSTEM_PROMPT,
        "report_type": "next_question",
        "task": "请基于客户材料、已有报告和历史问答，生成下一轮给用户的追问。",
    },
    "client_pre_session_brief": {
        "title": "会前基本信息整理",
        "prompt": CLIENT_PRE_SESSION_BRIEF_PROMPT,
        "report_type": "client_pre_session_brief",
        "task": "请基于客户材料和历史问答，生成给用户和真人顾问共同查看的会前基本信息整理。",
    },
    "ability_delivery_pack": {
        "title": "能力资产交付包",
        "prompt": ABILITY_DELIVERY_PROMPT,
        "report_type": "ability_delivery_pack",
        "task": "请基于客户材料、追问记录和整理结果，生成给用户看的第一阶段能力资产交付包。",
    },
    "human_review_pack": {
        "title": "真人审阅包",
        "prompt": REPORT_SYSTEM_PROMPT,
        "report_type": "human_review_pack",
        "task": "请基于客户材料和历史问答，生成真人顾问会前/会后审阅材料。",
    },
}


def build_manual_ai_prompt(session_id, task_type):
    task = MANUAL_AI_TASKS.get(task_type) or MANUAL_AI_TASKS["material_structuring"]
    if task_type == "material_structuring":
        context = get_session_context(
            session_id,
            material_excerpt_chars=50000,
            total_material_chars=250000,
            include_report_content=False,
        )
        context["prior_reports"] = []
        context["conversation"] = []
    elif task_type == "material_organization":
        context = get_session_context(
            session_id,
            material_excerpt_chars=50000,
            total_material_chars=250000,
            include_report_content=False,
        )
        context["prior_reports"] = []
        context["conversation"] = []
        latest_structuring = latest_report_content(session_id, "material_structuring")
        if latest_structuring:
            context["structured_material_draft"] = latest_structuring
    else:
        context = get_session_context(
            session_id,
            material_excerpt_chars=50000,
            total_material_chars=250000,
            include_report_content=True,
        )
    return f"""# {task["title"]}｜网页版 AI 输入包

把本文件整段粘贴到 GPT 网页版。请严格按 JSON 格式返回，返回后复制完整 JSON，粘回本地系统的“网页版输出”。

## 任务
{task["task"]}

## 系统提示词
{task["prompt"]}

## 客户上下文 JSON
```json
{json.dumps(context, ensure_ascii=False, indent=2)}
```
"""


def latest_report_content(session_id, report_type):
    with db() as conn:
        row = conn.execute(
            "SELECT content_json FROM reports WHERE session_id=? AND report_type=? ORDER BY id DESC LIMIT 1",
            (session_id, report_type),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["content_json"])
    except Exception:
        return {"raw": row["content_json"]}


def save_manual_ai_output(session_id, task_type, raw_content):
    task = MANUAL_AI_TASKS.get(task_type)
    if not task:
        raise ValueError("未知的网页版处理类型")
    content = strip_code_fence(raw_content or "")
    if not content:
        raise ValueError("请先粘贴网页版返回的 JSON")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"粘贴内容不是合法 JSON：{e}") from e

    report_type = task["report_type"]
    if report_type == "next_question":
        question = parsed.get("question") or ""
        if not question and parsed.get("status") == "ready":
            question = build_ready_user_reply(parsed)
        if not question:
            raise ValueError("下一问 JSON 里缺少 question 字段")
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, meta_json, created_at) VALUES(?,?,?,?,?)",
                (session_id, "assistant", question, json.dumps(parsed, ensure_ascii=False), now_iso()),
            )
            touch_session(conn, session_id, "ready_for_report" if parsed.get("status") == "ready" else "questioning")
        return parsed

    status = {
        "material_structuring": "materials_structured",
        "material_organization": "materials_organized",
        "client_pre_session_brief": "client_brief_ready",
        "ability_delivery_pack": "ability_delivered",
        "human_review_pack": "report_ready",
    }.get(report_type, "manual_imported")
    with db() as conn:
        conn.execute(
            "INSERT INTO reports(session_id, report_type, content_json, created_at) VALUES(?,?,?,?)",
            (session_id, report_type, json.dumps(parsed, ensure_ascii=False), now_iso()),
        )
        touch_session(conn, session_id, status)
    return parsed


def strip_code_fence(content):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def get_client_session_view(session_id):
    context = get_session_context(session_id, include_report_content=False)
    with db() as conn:
        reports = conn.execute(
            "SELECT id, report_type, content_json, created_at FROM reports WHERE session_id=? ORDER BY id DESC",
            (session_id,),
        ).fetchall()
    safe_reports = []
    for r in reports:
        report_type = r["report_type"]
        if report_type not in {"material_organization", "client_pre_session_brief", "ability_delivery_pack"}:
            continue
        try:
            content = json.loads(r["content_json"])
        except Exception:
            continue
        safe_reports.append(
            {
                "id": r["id"],
                "report_type": report_type,
                "created_at": r["created_at"],
                "content_json": sanitize_client_report(report_type, content),
            }
        )
    return {
        "session": context["session"],
        "conversation": context["conversation"],
        "interview_progress": context["interview_progress"],
        "reports": safe_reports,
    }


def sanitize_client_report(report_type, content):
    if report_type == "material_organization":
        readiness = content.get("readiness_assessment") or {}
        return {
            "overview": content.get("overview"),
            "readiness_assessment": {
                "status": readiness.get("status"),
                "reason": readiness.get("reason"),
                "missing_materials": readiness.get("missing_materials") or [],
                "if_continue_interview_first_questions": readiness.get("if_continue_interview_first_questions") or [],
            },
            "material_inventory": content.get("material_inventory") or [],
            "career_timeline": content.get("career_timeline") or [],
            "project_clues": content.get("project_clues") or [],
            "achievement_story_candidates": content.get("achievement_story_candidates") or [],
            "failure_or_constraint_candidates": content.get("failure_or_constraint_candidates") or [],
            "ability_evidence_clues": content.get("ability_evidence_clues") or [],
            "cognitive_asset_clues": content.get("cognitive_asset_clues") or [],
            "platform_dependency_clues": content.get("platform_dependency_clues") or [],
            "priority_question_map": content.get("priority_question_map") or [],
        }
    if report_type == "client_pre_session_brief":
        return {
            "user_summary": content.get("user_summary"),
            "confirmed_information": content.get("confirmed_information") or [],
            "key_story_clues": content.get("key_story_clues") or [],
            "ability_clues": content.get("ability_clues") or [],
            "cognitive_asset_clues": content.get("cognitive_asset_clues") or [],
            "open_questions_for_live_session": content.get("open_questions_for_live_session") or [],
            "what_to_prepare_next": content.get("what_to_prepare_next") or [],
            "user_facing_next_step": content.get("user_facing_next_step"),
        }
    if report_type == "ability_delivery_pack":
        return {
            "delivery_summary": content.get("delivery_summary"),
            "ability_asset_map": content.get("ability_asset_map") or [],
            "ability_balance_sheet": content.get("ability_balance_sheet") or [],
            "platform_dependency_table": content.get("platform_dependency_table") or [],
            "cognitive_asset_map": content.get("cognitive_asset_map") or [],
            "energy_and_fit_notes": content.get("energy_and_fit_notes") or [],
            "not_yet_conclusions": content.get("not_yet_conclusions") or [],
            "recommended_next_step": content.get("recommended_next_step"),
            "user_facing_closing": content.get("user_facing_closing"),
        }
    return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "AbilityIntake/0.1"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/") and not is_public_api("GET", parsed.path):
                if not self.require_admin():
                    return
            if parsed.path == "/":
                return self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if parsed.path in {"/client", "/client.html"}:
                return self.serve_file(STATIC_DIR / "client.html", "text/html; charset=utf-8")
            if parsed.path == "/styles.css":
                return self.serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            if parsed.path == "/app.js":
                return self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            if parsed.path == "/client.js":
                return self.serve_file(STATIC_DIR / "client.js", "application/javascript; charset=utf-8")
            if parsed.path.startswith("/static/"):
                rel = parsed.path.removeprefix("/static/")
                return self.serve_file(STATIC_DIR / rel)
            if parsed.path == "/api/health":
                return self.json({
                    "ok": True,
                    "model": LAST_USED_MODEL or DEFAULT_MODEL,
                    "configured_model": DEFAULT_MODEL,
                    "base_url": OPENAI_BASE_URL,
                    "has_api_key": bool(OPENAI_API_KEY),
                    "admin_auth_enabled": bool(ADMIN_PASSWORD),
                })
            if parsed.path == "/api/auth/status":
                return self.auth_status()
            if parsed.path == "/api/sessions":
                return self.list_sessions()
            if parsed.path.startswith("/api/client/sessions/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 4:
                    return self.client_session_view(int(parts[3]))
            if parsed.path.startswith("/api/sessions/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 3:
                    return self.get_session(int(parts[2]))
            self.not_found()
        except Exception as e:
            self.error(e)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/") and not is_public_api("POST", parsed.path):
                if not self.require_admin():
                    return
            if parsed.path == "/api/auth/login":
                return self.auth_login()
            if parsed.path == "/api/settings/openai-key":
                return self.set_openai_key()
            if parsed.path == "/api/sessions":
                return self.create_session()
            if parsed.path.startswith("/api/sessions/"):
                parts = parsed.path.strip("/").split("/")
                session_id = int(parts[2])
                if len(parts) == 4 and parts[3] == "upload":
                    return self.upload(session_id)
                if len(parts) == 4 and parts[3] == "message":
                    return self.add_message(session_id)
                if len(parts) == 4 and parts[3] == "client-chat-turn":
                    return self.client_chat_turn(session_id)
                if len(parts) == 4 and parts[3] == "organize":
                    return self.organize(session_id)
                if len(parts) == 4 and parts[3] == "next-question":
                    return self.next_question(session_id)
                if len(parts) == 4 and parts[3] == "client-brief":
                    return self.client_brief(session_id)
                if len(parts) == 4 and parts[3] == "report":
                    return self.report(session_id)
                if len(parts) == 4 and parts[3] == "manual-ai-prompt":
                    return self.manual_ai_prompt(session_id)
                if len(parts) == 4 and parts[3] == "manual-ai-output":
                    return self.manual_ai_output(session_id)
            self.not_found()
        except Exception as e:
            self.error(e)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def request_admin_token(self):
        return (self.headers.get("X-Admin-Token") or "").strip()

    def is_admin_authenticated(self):
        if not ADMIN_PASSWORD:
            return True
        return self.request_admin_token() == admin_token()

    def require_admin(self):
        if self.is_admin_authenticated():
            return True
        self.json({"error": "请先输入顾问台访问口令。", "auth_required": True}, 401)
        return False

    def auth_status(self):
        return self.json({
            "enabled": bool(ADMIN_PASSWORD),
            "authenticated": self.is_admin_authenticated(),
        })

    def auth_login(self):
        data = self.read_json()
        password = (data.get("password") or "").strip()
        if not ADMIN_PASSWORD:
            return self.json({"ok": True, "token": ""})
        if password != ADMIN_PASSWORD:
            return self.json({"error": "口令不正确。"}, 401)
        return self.json({"ok": True, "token": admin_token()})

    def set_openai_key(self):
        global OPENAI_API_KEY, DEFAULT_MODEL, OPENAI_BASE_URL
        data = self.read_json()
        key = (data.get("api_key") or "").strip()
        model = (data.get("model") or DEFAULT_MODEL).strip()
        base_url = (data.get("base_url") or OPENAI_BASE_URL).strip().rstrip("/")
        if not key:
            return self.json({"error": "api_key required"}, 400)
        if not key.startswith("sk-"):
            return self.json({"error": "API Key 格式看起来不对，应以 sk- 开头。"}, 400)
        if not re.match(r"^https?://", base_url, re.I):
            return self.json({"error": "接口路由必须以 http:// 或 https:// 开头。"}, 400)
        OPENAI_API_KEY = key
        if model:
            DEFAULT_MODEL = model
        OPENAI_BASE_URL = base_url
        save_model_settings(OPENAI_API_KEY, DEFAULT_MODEL, OPENAI_BASE_URL)
        return self.json({"ok": True, "has_api_key": True, "model": DEFAULT_MODEL, "base_url": OPENAI_BASE_URL})

    def create_session(self):
        data = self.read_json()
        name = (data.get("client_name") or "").strip()
        if not name:
            return self.json({"error": "client_name required"}, 400)
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO sessions(client_name, contact, goal, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (name, data.get("contact", ""), data.get("goal", ""), "intake", now_iso(), now_iso()),
            )
            session_id = cur.lastrowid
        return self.json({"id": session_id})

    def list_sessions(self):
        with db() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return self.json({"sessions": [row_to_dict(r) for r in rows]})

    def get_session(self, session_id):
        context = get_session_context(session_id)
        with db() as conn:
            files = conn.execute(
                "SELECT id, original_name, size, mime_type, created_at, length(extracted_text) AS text_len FROM files WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
            reports = conn.execute(
                "SELECT id, report_type, content_json, created_at FROM reports WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        context["files"] = [row_to_dict(f) for f in files]
        context["reports"] = [dict(row_to_dict(r), content_json=json.loads(r["content_json"])) for r in reports]
        return self.json(context)

    def client_session_view(self, session_id):
        return self.json(get_client_session_view(session_id))

    def upload(self, session_id):
        ctype, pdict = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            return self.json({"error": "multipart/form-data required"}, 400)
        pdict["boundary"] = bytes(pdict["boundary"], "utf-8")
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})

        saved = []
        with db() as conn:
            if "links" in form and form["links"].value.strip():
                links = [line.strip() for line in form["links"].value.splitlines() if line.strip()]
                for idx, url in enumerate(links, start=1):
                    if not re.match(r"^https?://", url, re.I):
                        extracted = f"[链接已保存，但格式不是 http/https，暂未抓取]\n{url}"
                    else:
                        try:
                            page_text = fetch_url_text(url)
                            extracted = f"来源链接：{url}\n\n{page_text}"
                        except Exception as e:
                            extracted = f"[链接已保存，但自动抓取失败：{type(e).__name__}: {e}]\n{url}"
                    name = f"链接材料{idx}.txt"
                    conn.execute(
                        "INSERT INTO files(session_id, original_name, stored_path, mime_type, size, extracted_text, created_at) VALUES(?,?,?,?,?,?,?)",
                        (session_id, name, url, "text/uri-list", len(url.encode("utf-8")), extracted, now_iso()),
                    )
                    saved.append({"name": name, "url": url, "text_len": len(extracted)})

            if "notes" in form and form["notes"].value.strip():
                text = form["notes"].value.strip()
                conn.execute(
                    "INSERT INTO files(session_id, original_name, stored_path, mime_type, size, extracted_text, created_at) VALUES(?,?,?,?,?,?,?)",
                    (session_id, "用户补充文本.txt", "", "text/plain", len(text.encode("utf-8")), text, now_iso()),
                )
                saved.append({"name": "用户补充文本.txt", "text_len": len(text)})

            file_fields = form["files"] if "files" in form else []
            if not isinstance(file_fields, list):
                file_fields = [file_fields]
            for field in file_fields:
                if not getattr(field, "filename", None):
                    continue
                original = Path(field.filename).name
                raw = field.file.read()
                stamp = f"{int(time.time()*1000)}_{len(saved)}_{original}"
                session_dir = UPLOAD_DIR / str(session_id)
                session_dir.mkdir(parents=True, exist_ok=True)
                stored = session_dir / stamp
                stored.write_bytes(raw)
                try:
                    extracted = extract_text(stored, original)
                except Exception as e:
                    extracted = f"[材料已保存，但文本抽取失败：{type(e).__name__}: {e}]"
                conn.execute(
                    "INSERT INTO files(session_id, original_name, stored_path, mime_type, size, extracted_text, created_at) VALUES(?,?,?,?,?,?,?)",
                    (session_id, original, str(stored), field.type, len(raw), extracted, now_iso()),
                )
                saved.append({"name": original, "size": len(raw), "text_len": len(extracted)})
            touch_session(conn, session_id)
        return self.json({"saved": saved})

    def add_message(self, session_id):
        data = self.read_json()
        content = (data.get("content") or "").strip()
        if not content:
            return self.json({"error": "content required"}, 400)
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, meta_json, created_at) VALUES(?,?,?,?,?)",
                (session_id, "user", content, None, now_iso()),
            )
            touch_session(conn, session_id, "questioning")
        return self.json({"ok": True})

    def next_question(self, session_id):
        result = create_question(session_id)
        return self.json(result)

    def client_chat_turn(self, session_id):
        data = self.read_json()
        result = client_chat_turn(session_id, data.get("content") or "")
        return self.json(result)

    def manual_ai_prompt(self, session_id):
        data = self.read_json()
        task_type = data.get("type") or "material_structuring"
        prompt = build_manual_ai_prompt(session_id, task_type)
        title = (MANUAL_AI_TASKS.get(task_type) or MANUAL_AI_TASKS["material_structuring"])["title"]
        return self.json({"prompt": prompt, "type": task_type, "title": title})

    def manual_ai_output(self, session_id):
        data = self.read_json()
        task_type = data.get("type") or "material_structuring"
        result = save_manual_ai_output(session_id, task_type, data.get("content") or "")
        return self.json({"ok": True, "type": task_type, "content": result})

    def client_brief(self, session_id):
        result = generate_client_brief(session_id)
        return self.json(result)

    def organize(self, session_id):
        result = organize_materials(session_id)
        return self.json(result)

    def report(self, session_id):
        result = generate_report(session_id)
        return self.json(result)

    def serve_file(self, path, content_type=None):
        path = Path(path)
        if not path.exists() or not path.is_file():
            return self.not_found()
        if content_type is None:
            suffix = path.suffix.lower()
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if content_type.startswith(("text/html", "text/css", "application/javascript")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")

    def not_found(self):
        return self.json({"error": "not found"}, 404)

    def error(self, e):
        if isinstance(e, KeyError):
            return self.json({"error": "未找到对应档案。"}, 404)
        if isinstance(e, (RuntimeError, ValueError)):
            print(f"Request error: {e}", file=sys.stderr)
        else:
            traceback.print_exc()
        return self.json({"error": str(e), "type": type(e).__name__}, 500)


def main():
    ensure_dirs()
    load_model_settings()
    init_db()
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Ability Intake app running at http://{host}:{port}")
    print(f"Database: {DB_PATH}")
    if ADMIN_PASSWORD:
        print("Admin auth: enabled")
    else:
        print("Admin auth: disabled. Set ADMIN_PASSWORD before public deployment.")
    if OPENAI_API_KEY:
        print(f"Model config: {DEFAULT_MODEL} via {OPENAI_BASE_URL}")
    if not OPENAI_API_KEY:
        print("Warning: OPENAI_API_KEY is not set. AI actions will return an error until it is set.")
    server.serve_forever()


if __name__ == "__main__":
    main()
