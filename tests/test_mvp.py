"""Automated smoke tests. Run: python -m pytest -q tests/"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import db
from app import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'test.sqlite3')
    with TestClient(app) as c:
        yield c

def test_initial_state_matches_pdf(client):
    state=client.get('/api/state').json()
    assert len(state['lessons'])==5
    assert state['profile']['name']=='Ewen'
    assert state['profile']['active_lesson']=='function_calling'
    assert state['latest_quiz']['is_sample'] is True
    assert state['latest_quiz']['score']==78
    assert client.get('/api/health').status_code==200

def test_goal_save(client):
    goal={'topic':'AI 产品经理必备的大模型技术基础','level':'有一些基础','objective':'能够独立解释大模型工具调用流程'}
    assert client.post('/api/goal',json=goal).status_code==200
    assert client.get('/api/state').json()['profile']['objective']==goal['objective']

def test_persistent_chat_demo(client):
    res=client.post('/api/chat',json={'message':'再讲简单一点','lesson_slug':'function_calling'})
    assert res.status_code==200,res.text
    assert res.json()['mode']=='demo'
    state=client.get('/api/state').json()
    assert state['messages'][-2]['role']=='user'
    assert state['messages'][-1]['role']=='assistant'
    assert len(state['messages'])==2

def test_quiz_review_loop(client):
    r=client.post('/api/quiz/submit',json={'choice':'a','short_answer':'模型自己决定然后直接执行','lesson_slug':'function_calling'})
    assert r.status_code==200,r.text
    obj=r.json()
    assert obj['score']<80
    assert obj['next_action']=='review'
    state=client.get('/api/state').json()
    assert state['latest_quiz']['is_sample'] is False
    assert state['profile']['active_lesson']=='function_calling'
    assert state['lessons'][2]['status']=='待巩固'

def test_quiz_mastery_unlocks_next_lesson(client):
    answer='模型先选择工具并生成参数，应用程序执行工具，再把结果返回给模型，模型根据结果生成最终答复。'
    res=client.post('/api/quiz/submit',json={'choice':'b','short_answer':answer,'lesson_slug':'function_calling'})
    assert res.status_code==200,res.text
    assert res.json()['score']>=80
    state=client.get('/api/state').json()
    assert state['lessons'][2]['status']=='已掌握'
    assert state['lessons'][3]['status']=='进行中'
    assert state['profile']['active_lesson']=='rag'
    assert state['actual_quiz_count']==1

def test_unsupported_quiz_lesson_rejected(client):
    res=client.post('/api/quiz/submit',json={'choice':'b','short_answer':'认真学习知识','lesson_slug':'rag'})
    assert res.status_code==400
