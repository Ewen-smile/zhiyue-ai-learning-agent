"""Zhiyue AI V1.5: dynamic AI learning plans and truthful SQLite analytics."""
from __future__ import annotations
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import agent
import db

ROOT=Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(app):
    db.initialize()
    yield

app=FastAPI(title='知跃 AI — 真实学习数据与动态路径',version='1.7.3',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

class GoalInput(BaseModel):
    topic:str=Field(min_length=2,max_length=150)
    level:Literal['刚开始了解','有一些基础','希望进一步提升']
    objective:str=Field(min_length=3,max_length=400)
    focus_question:str=Field(default='',max_length=350)

class PathRefineInput(BaseModel):
    focus_question:str=Field(min_length=4,max_length=350)

class LessonAction(BaseModel):
    slug:str=Field(min_length=3,max_length=90)

class ChatInput(BaseModel):
    message:str=Field(min_length=1,max_length=1500)
    lesson_slug:str=Field(min_length=3,max_length=90)

class QuizAction(BaseModel):
    lesson_slug:str=Field(min_length=3,max_length=90)

class QuizSubmission(BaseModel):
    quiz_id:int=Field(gt=0)
    choice:Literal['a','b','c','d']
    short_answer:str=Field(min_length=3,max_length=600)
    lesson_slug:str=Field(min_length=3,max_length=90)

@app.get('/')
def home():
    return FileResponse(ROOT/'static'/'index.html')

@app.get('/api/health')
def health():
    return {'ok':True,'mode':agent.mode(),'version':'1.7.3',
       'llm_adapter':agent.ADAPTER if agent.mode()=='ai' else None,
       'model':agent.MODEL if agent.mode()=='ai' else None}

@app.get('/api/state')
def state():
    return db.get_state()|{'mode':agent.mode()}

@app.post('/api/goal')
async def update_goal(payload:GoalInput):
    if agent.mode()!='ai':raise HTTPException(503,'未连接 AI 模型，不能假装生成学习路径。请配置 API Key。')
    topic,level,objective=payload.topic.strip(),payload.level,payload.objective.strip()
    questions=db.get_user_questions()
    try:
        plan=await agent.generate_path(topic,level,objective,payload.focus_question.strip(),questions)
    except Exception as e:
        raise HTTPException(502,f'生成路径失败（{type(e).__name__}），旧学习路径未被覆盖。请重试。')
    pid=db.save_generated_plan(topic,level,objective,payload.focus_question.strip(),plan,agent.MODEL)
    return {'ok':True,'plan_id':pid,'message':f'已依据你的学习目标与提问生成 {len(plan)} 个专属知识模块。','lessons':plan}

@app.post('/api/path/refine')
async def refine_path(payload:PathRefineInput):
    if agent.mode()!='ai':raise HTTPException(503,'请先连接真实 AI 模型')
    profile=db.get_state()['profile']
    try:
        plan=await agent.generate_path(profile['topic'],profile['level'],profile['objective'],
                                       payload.focus_question.strip(),db.get_user_questions())
    except Exception as e:
        raise HTTPException(502,f'更新学习路径失败（{type(e).__name__}），原路径已保留。')
    pid=db.save_generated_plan(profile['topic'],profile['level'],profile['objective'],payload.focus_question.strip(),plan,agent.MODEL)
    return {'ok':True,'plan_id':pid,'message':'已根据你的提问重新规划学习路径。','lessons':plan}

@app.post('/api/lesson/select')
def select_lesson(payload:LessonAction):
    if not db.select_lesson(payload.slug):raise HTTPException(404,'此知识点不在当前学习路径中')
    return {'ok':True,'active_lesson':payload.slug}

@app.get('/api/lesson/{slug}')
def get_lesson(slug:str):
    lesson=db.get_lesson(slug)
    if not lesson:raise HTTPException(404,'未找到该知识点，请先生成学习路径')
    return lesson

@app.post('/api/chat')
async def chat(payload:ChatInput):
    if not db.get_lesson(payload.lesson_slug):raise HTTPException(400,'请先选择由 AI 生成的知识点')
    msg=payload.message.strip()
    if not msg:raise HTTPException(422,'请输入问题')
    answer,mode,tool_count=await agent.respond(msg,payload.lesson_slug)
    # Persist only real user interactions; errors aren't silently reclassified as successful learning.
    db.save_message(payload.lesson_slug,'user',msg,mode)
    db.save_message(payload.lesson_slug,'assistant',answer,mode)
    return {'reply':answer,'mode':mode,'tool_count':tool_count}

@app.post('/api/quiz/generate')
async def generate_quiz(payload:QuizAction):
    lesson=db.get_lesson(payload.lesson_slug)
    if not lesson:raise HTTPException(404,'没有该知识模块')
    if agent.mode()!='ai':raise HTTPException(503,'测验题目需要连接真实 AI 才能生成')
    try:
        spec=await agent.generate_quiz(lesson)
    except Exception as e:
        raise HTTPException(502,f'AI 测验出题失败（{type(e).__name__}）；已尝试恢复但没有获得有效的新题目。请检查模型连接后点击重试。')
    question_id=db.store_quiz_question(payload.lesson_slug,spec)
    # Never expose the answer key, reference answer or rubric to the client.
    return {'quiz_id':question_id,'lesson_slug':payload.lesson_slug,
            'question':spec['question'],'options':spec['options'],
            'short_question':spec['short_question'],'mode':'ai'}

@app.post('/api/quiz/submit')
async def submit(payload:QuizSubmission):
    lesson=db.get_lesson(payload.lesson_slug)
    if not lesson:raise HTTPException(404,'没有该知识模块')
    spec=db.load_quiz_question(payload.quiz_id,payload.lesson_slug)
    if not spec:raise HTTPException(404,'找不到本次测验，请重新生成题目')
    # Prevent duplicate scores for one generated quiz.
    with db.connect() as con:
        exists=con.execute("SELECT id FROM quizzes WHERE choice LIKE ?",(f'quiz:{payload.quiz_id}:%',)).fetchone()
    if exists:raise HTTPException(409,'本次测验已经提交，请重新生成题目后再测')
    if agent.mode()!='ai':raise HTTPException(503,'当前无法获得真实 AI 评分')
    try:
        score,dimensions,feedback,weak,eval_mode=await agent.grade(lesson,spec,payload.choice,payload.short_answer.strip())
    except Exception as e:
        raise HTTPException(502,f'AI 评分失败（{type(e).__name__}），本次没有保存虚构成绩。请重试。')
    db.save_quiz(payload.lesson_slug,f'quiz:{payload.quiz_id}:{payload.choice}',payload.short_answer.strip(),
                 score,dimensions,feedback,weak,eval_mode)
    return {'ok':True,'score':score,'dimensions':dimensions,'feedback':feedback,
            'weak_points':weak,'mode':eval_mode,'next_action':feedback['next_action']}

class AgentDecision(BaseModel):
    decision:Literal['accept','reject']

@app.post('/api/agent/recommend')
async def agent_recommend():
    if agent.mode()!='ai':raise HTTPException(503,'真实 Agent 尚未连接')
    snapshot=db.latest_quiz_context()
    if not snapshot:raise HTTPException(404,'当前路径没有真实测验，请先完成一份测验')
    if snapshot['quiz']['score']==100 and snapshot['quiz']['weak_points']:
        raise HTTPException(409,'旧版测验存在“100分但有薄弱点”的矛盾记录。为避免错误建议，请先用新版完成一次测验。')
    # Idempotent: don't bill the model again for an already-generated proposal.
    with db.connect() as con:
        old=con.execute('SELECT * FROM agent_recommendations WHERE quiz_id=?',(snapshot['quiz']['id'],)).fetchone()
    if old:
        if old['status']=='pending':return {'ok':True,'id':old['id'],'reused':True}
        raise HTTPException(409,'本次测验已有处理结果；完成新测验后再获取下一步建议')
    try:
        return {'ok':True,**(await agent.recommend_next_step())}
    except Exception as e:
        raise HTTPException(502,f'Agent 学习建议生成失败（{type(e).__name__}），已有测验成绩不会丢失；可重试。')

@app.post('/api/agent/recommendation/{recommendation_id}/decision')
def recommendation_decision(recommendation_id:int,payload:AgentDecision):
    outcome=db.decide_agent_recommendation(recommendation_id,payload.decision)
    if not outcome['ok']:raise HTTPException(409,'建议已处理或不属于当前测验，不能重复应用')
    return outcome

@app.get('/api/search')
def search(q:str=''):
    term=q.strip().lower()
    if not term:return {'results':[]}
    state=db.get_state()
    return {'results':[{'slug':l['slug'],'title':l['title'],'description':l['subtitle']}
                      for l in state['lessons'] if any(term in str(l.get(k,'')).lower() for k in ('title','subtitle','core'))][:8]}

@app.post('/api/demo/reset')
def reset():
    if os.getenv('ZHIYUE_ENABLE_RESET')!='1':raise HTTPException(403,'未开启本地开发重置功能')
    db.clear_demo()
    return {'ok':True}

if __name__=='__main__':
    import uvicorn
    uvicorn.run('app:app',host='127.0.0.1',port=int(os.getenv('PORT','8000')),reload=False)
