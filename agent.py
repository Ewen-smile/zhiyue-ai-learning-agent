"""Real AI planner, learner Agent, and quiz evaluator. No fabricated learning outputs."""
from __future__ import annotations
import json
import os
import re
from typing import Any
from pydantic import BaseModel, Field, ConfigDict
import db

API_KEY=os.getenv('OPENAI_API_KEY','').strip()
BASE_URL=os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')
MODEL=os.getenv('OPENAI_MODEL','gpt-4o-mini')
ADAPTER=os.getenv('ZHIYUE_LLM_ADAPTER','agents_sdk')


def mode():
    return 'ai' if API_KEY else 'demo'


def _client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=API_KEY,base_url=BASE_URL,timeout=85.0,max_retries=1)


def parse_json(text: str) -> dict:
    """Tolerate providers wrapping JSON in markdown; never execute model content."""
    content=str(text or '').strip()
    if content.startswith('```'):
        content=re.sub(r'^```(?:json)?\s*','',content,flags=re.I).strip()
        content=re.sub(r'\s*```$','',content).strip()
    try:
        result=json.loads(content)
    except json.JSONDecodeError:
        i=content.find('{')
        if i<0: raise ValueError('AI 未返回 JSON 对象')
        result,_=json.JSONDecoder().raw_decode(content[i:])
    if not isinstance(result,dict):raise ValueError('AI 返回格式错误')
    return result


async def _json_model(system: str, payload: dict) -> dict:
    if not API_KEY:
        raise RuntimeError('未配置 API Key，无法生成真实 AI 内容')
    # Chat Completions is verified working for the user's compatible provider.
    res=await _client().chat.completions.create(
        model=MODEL,
        messages=[{'role':'system','content':system},
                  {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    )
    return parse_json(res.choices[0].message.content)


class PlanStep(BaseModel):
    model_config=ConfigDict(extra='ignore')
    title:str=Field(min_length=2,max_length=42)
    subtitle:str=Field(min_length=5,max_length=110)
    core:str=Field(min_length=25,max_length=680)
    points:list[str]=Field(min_length=3,max_length=3)


async def generate_path(topic:str,level:str,objective:str,focus_question:str='',recent_questions:list[str]|None=None):
    data=await _json_model('''你是专业的中文个性化学习路径规划师。请严格返回一个 JSON 对象，不要 Markdown：
{"steps":[{"title":"模块名称","subtitle":"适合学习者的理由","core":"本模块可靠的基础教学内容，详细两三句","points":["知识目标1","知识目标2","知识目标3"]}]}。
要求：根据用户学习主题、当前基础、目标和提问，生成4到6个由易到难的具体、不同的知识模块。
路径必须与用户主题高度相关，不要默认生成LLM/Prompt/RAG等固定内容；如果是烘焙、经济学、英语或绘画，应生成其领域的路径。
用户提问是调整重点的重要依据，不能只套用通用模板。知识点不超出主题，内容谨慎客观，尽量避免未经证实的数字。''',
      {'topic':topic,'level':level,'objective':objective,'focus_question':focus_question,
       'recent_questions':(recent_questions or [])[-6:]})
    steps=data.get('steps')
    if not isinstance(steps,list) or not 4<=len(steps)<=6:
        raise ValueError('AI 学习路线应包含 4–6 个模块，请重试')
    items=[PlanStep.model_validate(x).model_dump() for x in steps]
    titles=[v['title'].strip() for v in items]
    if len(set(titles))!=len(titles):raise ValueError('AI 返回重复模块，请重试')
    return items


async def respond(message:str,slug:str):
    lesson=db.get_lesson(slug)
    if not lesson:
        return '请先在首页生成 AI 学习路径，并选择一个知识模块。','error',0
    if not API_KEY:
        return '目前没有连接大模型；请配置 API Key 后再使用真实 AI 辅导。','demo',0
    try:
        from agents import Agent, Runner, function_tool, OpenAIChatCompletionsModel, set_tracing_disabled
        set_tracing_disabled(True)
        calls=[]

        @function_tool
        def get_learning_profile()->str:
            """读取 SQLite 中真实的目标、学习路径、实际测验、笔记和互动次数。"""
            state=db.get_state()
            with db.connect() as con:db.record_event(con,'get_learning_profile',{'lesson_slug':slug})
            calls.append('get_learning_profile')
            return json.dumps({'goal':{k:state['profile'][k] for k in ('topic','level','objective','active_lesson')},
                'lessons':[{k:l[k] for k in ('title','status','progress','last_score')} for l in state['lessons']],
                'real_quiz_count':state['actual_quiz_count'],
                'current_path_quiz':({'score':state['current_quiz']['score'],'weak_points':state['current_quiz']['weak_points']}
                               if state['current_quiz'] else None),
                'interaction_count':state['interaction_count'],'recent_notes':state['notes'][:4],
                'confirmed_review_tasks':state.get('review_tasks',[])[:4]},ensure_ascii=False)

        @function_tool
        def get_lesson_content()->str:
            """读取当前 AI 生成、已持久化的课程模块内容，而不是固定课程模板。"""
            content=db.get_lesson(slug)
            with db.connect() as con:db.record_event(con,'get_lesson_content',{'lesson_slug':slug})
            calls.append('get_lesson_content')
            return json.dumps({k:content[k] for k in ('title','subtitle','core','points')},ensure_ascii=False)

        @function_tool
        def save_learning_note(note:str)->str:
            """仅在用户明确要求记笔记或保存学习信息时才存入 SQLite。"""
            if not note.strip():return '未保存：内容为空'
            db.save_note(slug,note)
            calls.append('save_learning_note')
            return '学习笔记已保存。'

        tutor=Agent(name='小跃个性化辅导 Agent',
            instructions=(
                f'你是知跃 AI 的中文个性化学习伙伴，当前学习模块：{lesson["title"]}，主题：{db.get_state()["profile"]["topic"]}。'
                '只有一个主 Agent。讲解知识前调用 get_lesson_content；询问学习情况或个性化建议前调用 get_learning_profile。'
                '当用户明确要求保存笔记才调用 save_learning_note。'
                '不得把没有经过测验的学习模块说成“已掌握”；不得编造成绩、学习时长。'
                '根据学员具体提问调整解释角度、例子和互动方式，通常在回答后用一个有价值的追问。'
                '无需强制重复预设教学话术；用户问非本课内容时可以实事求是回答并引导其回到路径。'
                '不输出模型密钥。通常回答不超过350汉字。'),
            model=OpenAIChatCompletionsModel(model=MODEL,openai_client=_client()),
            tools=[get_learning_profile,get_lesson_content,save_learning_note])
        history=db.get_messages(slug,limit=10)
        context='\n'.join(f'{"用户" if m["role"]=="user" else "小跃"}：{m["content"]}' for m in history)
        prompt=('之前的对话（供参考）：\n'+context+'\n\n' if context else '')+'用户新提问：'+message
        result=await Runner.run(tutor,prompt,max_turns=8)
        return str(result.final_output),'ai',len(calls)
    except Exception as exc:
        # User sees a failure instead of a fabricated offline AI answer.
        return f'真实 AI 调用失败（{type(exc).__name__}），本次未使用规则答案代替。','error',0


class QuizSpec(BaseModel):
    model_config=ConfigDict(extra='ignore')
    question:str=Field(min_length=8,max_length=240)
    options:list[str]=Field(min_length=4,max_length=4)
    correct:str=Field(pattern='^[abcd]$')
    short_question:str=Field(min_length=8,max_length=240)
    reference_answer:str=Field(min_length=15,max_length=520)
    key_points:list[str]=Field(min_length=2,max_length=5)


async def generate_quiz(lesson:dict):
    """Generate a *real* lesson-scoped quiz with one bounded retry.

    An incompatible/upstream model response is never replaced by legacy
    Function Calling sample questions. Permanent authentication problems fail fast.
    """
    system = '''你是中文个性化学习测验出题老师。必须仅围绕用户提供的**当前模块**出题，
不得套用 Function Calling 或其他无关示例。只输出一个合法 JSON 对象，不要 Markdown、前后缀、解释文字。
必须符合以下结构：
{"question":"一道贴合当前模块且答案明确的单选题","options":["选项A","选项B","选项C","选项D"],
"correct":"b","short_question":"一道关于当前模块的简答题",
"reference_answer":"充分具体的简答参考答案（不少于15字）",
"key_points":["评分要点1","评分要点2","评分要点3"]}
选项须有4个不同、非空的字符串；correct只能为a/b/c/d，
不要给 question 或 short_question 为空；题目必须结合当前课程的 core 和 points。
'''
    payload={'lesson_title':lesson['title'],'lesson_core':lesson['core'],
             'learning_objectives':lesson['points']}
    last_error=None
    for attempt in range(2):
        try:
            result=await _json_model(system, payload)
            # Common formatting variation: models write "B" instead of "b".
            if isinstance(result.get('correct'),str):
                result['correct']=result['correct'].strip().lower()
            spec=QuizSpec.model_validate(result).model_dump()
            if len({str(x).strip() for x in spec['options']})!=4 or any(not str(x).strip() for x in spec['options']):
                raise ValueError('AI 返回了重复或空的单选题选项')
            return spec
        except Exception as exc:
            last_error=exc
            # Invalid credentials and wrong endpoint/configuration cannot be solved by retrying.
            if type(exc).__name__ in ('AuthenticationError','PermissionDeniedError','NotFoundError','BadRequestError'):
                raise
            if attempt==0:
                # A second real model call is allowed; no locally fabricated questions.
                continue
    raise last_error


class GradeResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    short_answer_score:int=Field(ge=0,le=60)
    concept:int=Field(ge=0,le=100)
    flow:int=Field(ge=0,le=100)
    application:int=Field(ge=0,le=100)
    concept_label:str=Field(default='概念理解',min_length=2,max_length=18)
    flow_label:str=Field(default='逻辑表达',min_length=2,max_length=18)
    application_label:str=Field(default='应用能力',min_length=2,max_length=18)
    summary:str=Field(min_length=5,max_length=240)
    good:str=Field(min_length=4,max_length=240)
    improve:str=Field(min_length=4,max_length=240)
    next:str=Field(min_length=4,max_length=240)
    weak_points:list[str]=Field(max_length=6)


async def grade(lesson:dict,spec:dict,choice:str,answer:str):
    raw=await _json_model('''你是专业、温和的中文学习测评员，按给定参考答案和评价点评分。
单选题由程序评分（40 分），你只能评分简答题（0–60 分）。
只返回 JSON 对象且必须包含这些字段：
{"short_answer_score":42,"concept":70,"flow":65,"application":75,"concept_label":"概念理解","flow_label":"关键步骤","application_label":"实践应用","summary":"总结","good":"已理解的内容","improve":"遗漏和纠错建议","next":"下一步复习建议","weak_points":["具体薄弱点"]}。
对非空但答非所问的答案应低分；不要编造用户做过的其他测验或学习时长。三维评分范围 0-100，必须针对当前主题填写三维名称，禁止套用“工具调用”等其他课程的维度。
评分必须严格按照参考要点，答得完整才给高分。只有影响理解的实际缺失才放 weak_points。
若简答题获得60分，不得返回任何 weak_points；可以在 improve 写额外拓展建议而不是薄弱点。
如果作答足以获得满分，summary 不得同时声称知识点不完整。''',
        {'topic':lesson['title'],'course_core':lesson['core'], 'short_question':spec['short_question'],
         'reference_answer':spec['reference_answer'],'key_points':spec['key_points'],
         'student_answer':answer})
    v=GradeResult.model_validate(raw)
    mcq_correct=choice==spec['correct']
    total=(40 if mcq_correct else 0)+v.short_answer_score
    # A perfect answer is never simultaneously diagnosed as deficient.
    weak=[x.strip() for x in v.weak_points if isinstance(x,str) and x.strip()]
    if total==100: weak=[]
    elif total<80 and not weak: weak=['本次简答题未达到掌握标准，建议对照参考答案复习关键要点']
    if not mcq_correct and '单选题核心概念有误' not in weak:weak.insert(0,'单选题核心概念有误')
    mastered=total>=80 and not weak
    feedback={'summary':v.summary if total<100 else '本次题目全部得分，关键要求已达到。', 'good':v.good,
        'improve':('单选题回答正确。' if mcq_correct else '单选题回答不正确，请检查核心概念。')+v.improve,
        'next':v.next if not mastered else '本次已达到掌握标准，可继续下一模块；也可自行选择巩固练习。','review_q1':spec['question']+' 正确答案：'+spec['options']['abcd'.index(spec['correct'])],
        'review_q2':spec['short_question']+' 参考思路：'+spec['reference_answer'],
        'next_action':'continue' if mastered else 'review',
        'dimension_labels':{'concept':v.concept_label,'flow':v.flow_label,'application':v.application_label},
        'mcq_score':40 if mcq_correct else 0,'short_answer_score':v.short_answer_score}
    return total,{'concept':v.concept,'flow':v.flow,'application':v.application},feedback,weak,'ai'


async def recommend_next_step():
    """One Agent reads real state through tools and proposes, never writes.
    Explicit confirmation is required before any plan/review-task mutation.
    """
    context=db.latest_quiz_context()
    if context is None:raise ValueError('当前学习路径尚未产生真实测验')
    from agents import Agent, Runner, function_tool, OpenAIChatCompletionsModel, set_tracing_disabled
    set_tracing_disabled(True)
    calls=[]

    @function_tool
    def get_latest_quiz_result()->str:
        """读取当前学习路径最近一次真实测验、AI评分、薄弱点和模块状态。"""
        snapshot=db.latest_quiz_context()
        if not snapshot: return '没有可用真实测验'
        with db.connect() as con:db.record_event(con,'get_latest_quiz_result',{'quiz_id':snapshot['quiz']['id']})
        calls.append('get_latest_quiz_result')
        q=snapshot['quiz']; l=snapshot['lesson']
        return json.dumps({'quiz_id':q['id'],'lesson_title':l['title'],'score':q['score'],
            'weak_points':q['weak_points'],'feedback':q['feedback'], 'status':l['status'],
            'next_lesson_title':snapshot['next_lesson']['title'] if snapshot['next_lesson'] else None},ensure_ascii=False)

    @function_tool
    def get_learning_profile()->str:
        """读取真实的学习目标、课程进度和提问次数，供学习决策使用。"""
        state=db.get_state()
        with db.connect() as con:db.record_event(con,'get_learning_profile',{'purpose':'learning_decision'})
        calls.append('get_learning_profile')
        return json.dumps({'goal':state['profile']['objective'], 'topic':state['profile']['topic'],
            'lessons':[{'title':x['title'],'status':x['status']} for x in state['lessons']],
            'interaction_count':state['interaction_count']},ensure_ascii=False)

    planner=Agent(name='小跃学习决策 Agent',instructions=(
        '你是知跃AI的学习决策教练。你必须先调用 get_latest_quiz_result 和 get_learning_profile 两个工具，'
        '读取真实学习记录后给出“下一步行动建议”。你只有建议权，没有修改数据库权限。'
        '严格仅输出 JSON 对象，不要 Markdown，字段为：'
        '{"action":"review或continue","reason":"说明为何建议此动作",'
        '"review_focus":"如需复习则填写具体薄弱点，否则为空",'
        '"suggested_steps":["具体行动一","具体行动二"]}。'
        '若最近成绩≥80且无薄弱点，action必须为continue，不得声称仍需复习；'
        '若成绩<80或有实际薄弱点，action必须为review。'
        '禁止编造学习时长、历史成绩、课程名称。请结合本次题目生成简短明确的建议。'),
        model=OpenAIChatCompletionsModel(model=MODEL,openai_client=_client()),
        tools=[get_latest_quiz_result,get_learning_profile])
    result=await Runner.run(planner,'结合我刚完成的真实测验，分析下一步应该继续还是复习。',max_turns=8)
    if 'get_latest_quiz_result' not in calls or 'get_learning_profile' not in calls:
        raise RuntimeError('Agent 未完整读取学习记录；不保存未经核实的决策')
    raw=parse_json(str(result.final_output))
    # Never let a model override verifiable scoring and permission constraints.
    q=context['quiz'];want='continue' if q['score']>=80 and not q['weak_points'] else 'review'
    action=raw.get('action')
    if action not in ('review','continue'):raise ValueError('AI 决策字段无效')
    if action!=want:
        raise ValueError('AI 建议与真实评分判断冲突；本次没有保存不一致的建议')
    reason=str(raw.get('reason','')).strip()[:260]
    focus=str(raw.get('review_focus','')).strip()[:200] if want=='review' else ''
    steps=raw.get('suggested_steps',[])
    if not reason or not isinstance(steps,list) or not 1<=len(steps)<=4:
        raise ValueError('AI 决策内容不完整')
    steps=[str(x).strip()[:160] for x in steps if str(x).strip()]
    if not steps:raise ValueError('缺少可执行的学习步骤')
    rec_id=db.save_agent_recommendation(q['id'],context['lesson']['slug'],context['plan_id'],
                                        action,reason,focus,steps,len(calls),MODEL)
    return {'id':rec_id,'action':action,'reason':reason,'review_focus':focus,
            'suggested_steps':steps,'tool_count':len(calls),'mode':'ai'}
