"""SQLite persistence for Zhiyue AI. Legacy Figma seed data is never reported as real learning.

Existing learning.sqlite3 files are migrated in place. No rows are deleted by migrations;
legacy course rows remain available as archived records but disappear from the active AI plan.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('ZHIYUE_DATA_DIR', ROOT / 'data'))
DB_PATH = DATA_DIR / 'learning.sqlite3'
LEGACY = {'llm', 'prompt', 'function_calling', 'rag', 'evaluation'}
LEGACY_DEFAULT_TOPIC = 'AI 产品经理必备的大模型技术基础'
LEGACY_DEFAULT_OBJECTIVE = '理解大模型核心概念，能够分析典型 AI 应用的实现方式。'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _add_column(con, table, name, definition):
    existing = {r['name'] for r in con.execute('PRAGMA table_info(' + table + ')')}
    if name not in existing:
        con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


def initialize():
    # One-time pre-migration backup. Existing real learning history is never deleted.
    if DB_PATH.exists():
        backup=DB_PATH.with_name('learning.before_v1_5.sqlite3')
        if not backup.exists():
            with sqlite3.connect(DB_PATH) as existing:
                has_table=existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lessons'").fetchone()
                cols={row[1] for row in existing.execute('PRAGMA table_info(lessons)')} if has_table else set()
            if has_table and 'source' not in cols:
                shutil.copy2(DB_PATH,backup)
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS profile (
          id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL,
          topic TEXT NOT NULL, level TEXT NOT NULL, objective TEXT NOT NULL,
          active_lesson TEXT NOT NULL DEFAULT '',
          duration_minutes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lessons (
          slug TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
          progress INTEGER NOT NULL DEFAULT 0, last_score INTEGER,
          description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_slug TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('user','assistant')),
          content TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quizzes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_slug TEXT NOT NULL,
          choice TEXT NOT NULL, short_answer TEXT NOT NULL,
          score INTEGER NOT NULL, dimensions TEXT NOT NULL, feedback TEXT NOT NULL,
          weak_points TEXT NOT NULL, evaluation_mode TEXT NOT NULL,
          is_sample INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS study_notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_slug TEXT NOT NULL,
          note TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tool_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, tool_name TEXT NOT NULL,
          arguments TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quiz_questions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_slug TEXT NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS path_generations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          topic TEXT NOT NULL, level TEXT NOT NULL, objective TEXT NOT NULL,
          focus_question TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_recommendations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          quiz_id INTEGER NOT NULL UNIQUE,
          lesson_slug TEXT NOT NULL,
          plan_id INTEGER NOT NULL,
          action TEXT NOT NULL CHECK(action IN ('review','continue')),
          reason TEXT NOT NULL,
          review_focus TEXT NOT NULL DEFAULT '',
          suggested_steps TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','stale')),
          tool_count INTEGER NOT NULL DEFAULT 0,
          model TEXT NOT NULL,
          created_at TEXT NOT NULL,
          applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS review_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          recommendation_id INTEGER NOT NULL UNIQUE,
          lesson_slug TEXT NOT NULL,
          focus TEXT NOT NULL,
          reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          created_at TEXT NOT NULL
        );
        ''')
        _add_column(con, 'lessons', 'source', "TEXT NOT NULL DEFAULT 'legacy_template'")
        _add_column(con, 'lessons', 'plan_id', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(con, 'lessons', 'position', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(con, 'lessons', 'core', "TEXT NOT NULL DEFAULT ''")
        _add_column(con, 'lessons', 'points_json', "TEXT NOT NULL DEFAULT '[]'")
        if not con.execute('SELECT id FROM profile WHERE id=1').fetchone():
            # A new user hasn't chosen a goal; defaults must not masquerade as user input.
            con.execute('INSERT INTO profile (id,name,topic,level,objective,active_lesson,duration_minutes,updated_at) VALUES(1,?,?,?,?,?,?,?)',
                        ('Ewen','','刚开始了解','','',0,now()))
        # On a legacy DB, clear ONLY the exact stock demo goal before any real path was generated.
        # Leave genuine messages, notes, tool events, quizzes and custom goals untouched.
        if not con.execute('SELECT 1 FROM path_generations LIMIT 1').fetchone():
            profile=con.execute('SELECT topic,objective FROM profile WHERE id=1').fetchone()
            if profile and profile['topic']==LEGACY_DEFAULT_TOPIC and profile['objective']==LEGACY_DEFAULT_OBJECTIVE:
                con.execute("UPDATE profile SET topic='', objective='', active_lesson='', level='刚开始了解', updated_at=? WHERE id=1",(now(),))
        # All old seeded course rows without an AI plan remain archives; they don't count toward mastery.
        current = con.execute("SELECT slug FROM lessons WHERE source='ai' AND plan_id>0 ORDER BY plan_id DESC, position LIMIT 1").fetchone()
        active = con.execute('SELECT active_lesson FROM profile WHERE id=1').fetchone()[0]
        if current and (not active or not con.execute("SELECT 1 FROM lessons WHERE slug=? AND source='ai' AND plan_id>0", (active,)).fetchone()):
            con.execute('UPDATE profile SET active_lesson=? WHERE id=1', (current[0],))


def record_event(con, name, args):
    con.execute('INSERT INTO tool_events (tool_name,arguments,created_at) VALUES (?,?,?)',
                (name,json.dumps(args,ensure_ascii=False),now()))


def lesson_slug(title):
    digest=hashlib.sha256(title.strip().lower().encode('utf-8')).hexdigest()[:16]
    return 'ai_' + digest


def _lesson(row):
    result=dict(row)
    result['subtitle']=result.get('description','')
    result['points']=json.loads(result.get('points_json') or '[]')
    return result


def active_plan(con):
    latest = con.execute('SELECT MAX(id) FROM path_generations').fetchone()[0] or 0
    return latest, [_lesson(row) for row in con.execute(
        "SELECT * FROM lessons WHERE source='ai' AND plan_id=? ORDER BY position",(latest,))]


def get_lesson(slug):
    with connect() as con:
        _id,ls=active_plan(con)
        return next((l for l in ls if l['slug']==slug),None)


def get_messages(slug,limit=12):
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT role,content FROM messages WHERE lesson_slug=? ORDER BY id DESC LIMIT ?', (slug,limit))][::-1]


def get_user_questions(limit=8):
    with connect() as con:
        return [r[0] for r in con.execute("SELECT content FROM messages WHERE role='user' ORDER BY id DESC LIMIT ?",(limit,))][::-1]


def get_state():
    with connect() as con:
        profile=dict(con.execute('SELECT * FROM profile WHERE id=1').fetchone())
        # duration_minutes in legacy DB was a seeded 198-minute placeholder, not a timer.
        profile.pop('duration_minutes',None)
        plan_id,lessons=active_plan(con)
        current={r['slug'] for r in lessons}
        if profile['active_lesson'] not in current:
            profile['active_lesson']=lessons[0]['slug'] if lessons else ''
        # is_sample=1 is always excluded. Old genuine tests remain in the overall quiz history.
        actual=[dict(r) for r in con.execute('SELECT * FROM quizzes WHERE is_sample=0 ORDER BY id')]
        quizrow=actual[-1] if actual else None
        current_quiz=next((r for r in reversed(actual) if r['lesson_slug'] in current),None)
        messages=[dict(r) for r in con.execute(
            'SELECT role,content,mode,created_at FROM messages WHERE lesson_slug=? ORDER BY id DESC LIMIT 40',
            (profile['active_lesson'],))][::-1] if profile['active_lesson'] else []
        notes=[dict(r) for r in con.execute('SELECT lesson_slug,note,created_at FROM study_notes ORDER BY id DESC LIMIT 15')]
        interactions=con.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0]
        practice_count=len(actual)
        study_lesson_count=con.execute("SELECT COUNT(DISTINCT lesson_slug) FROM messages WHERE role='user'").fetchone()[0]
        # Diagnose only the latest actual quiz for each active lesson. A later passed
        # quiz clears prior weaknesses; historical 100-point demos must not trigger review.
        review_items=[]
        for l in lessons:
            if l['status']!='待巩固':continue
            recent=con.execute('SELECT * FROM quizzes WHERE lesson_slug=? AND is_sample=0 ORDER BY id DESC LIMIT 1',(l['slug'],)).fetchone()
            if not recent:continue
            weaknesses=json.loads(recent['weak_points'])
            if weaknesses:
                review_items.append({'slug':l['slug'],'title':l['title'],'point':weaknesses[0],'score':recent['score']})
        last_recommendation=con.execute('SELECT * FROM agent_recommendations ORDER BY id DESC LIMIT 1').fetchone()
        pending_recommendation=con.execute("SELECT * FROM agent_recommendations WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone()
        tasks=[dict(r) for r in con.execute("SELECT * FROM review_tasks WHERE status='open' ORDER BY id DESC LIMIT 20")]
        def rec_row(row):
            if row is None:return None
            d=dict(row);d['suggested_steps']=json.loads(d['suggested_steps']);return d

    return {
      'profile':profile,'plan_id':plan_id,'has_ai_path':bool(lessons),'lessons':lessons,
      'latest_quiz':decode_quiz(quizrow) if quizrow else None,
      'current_quiz':decode_quiz(current_quiz) if current_quiz else None,
      'actual_quiz_count':practice_count,'actual_scores':[int(q['score']) for q in actual],
      'quiz_history':[{'slug':q['lesson_slug'],'score':q['score'],'created_at':q['created_at'],
                       'evaluation_mode':q['evaluation_mode']} for q in actual],
      'interaction_count':interactions,'studied_lesson_count':study_lesson_count,
      'messages':messages,'notes':notes,'review_items':review_items,
      'agent_recommendation':rec_row(pending_recommendation),
      'latest_agent_recommendation':rec_row(last_recommendation),
      'review_tasks':tasks,
      'demo_seed':False,
    }


def decode_quiz(row):
    if not row: return None
    d=dict(row)
    for field in ('dimensions','feedback','weak_points'):
        d[field]=json.loads(d[field])
    d['is_sample']=bool(d['is_sample'])
    return d


def save_generated_plan(topic,level,objective,focus_question,items,model):
    """Atomic plan update: no partial plan on model failure. Preserve actual quiz and message history."""
    with connect() as con:
        con.execute("UPDATE agent_recommendations SET status='stale' WHERE status='pending'")
        cursor=con.execute('INSERT INTO path_generations(topic,level,objective,focus_question,model,created_at) VALUES(?,?,?,?,?,?)',
                           (topic,level,objective,focus_question,model,now()))
        plan_id=cursor.lastrowid
        slugs=[]
        for idx,item in enumerate(items):
            slug=lesson_slug(item['title'])
            slugs.append(slug)
            old=con.execute('SELECT source,status,progress,last_score FROM lessons WHERE slug=?',(slug,)).fetchone()
            # Preserve progress only for the same previously AI-generated unit.
            preserve=old and old['source']=='ai'
            status=old['status'] if preserve else '未开始'
            progress=old['progress'] if preserve else 0
            score=old['last_score'] if preserve else None
            con.execute('''INSERT INTO lessons(slug,title,status,progress,last_score,description,source,plan_id,position,core,points_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET
                title=excluded.title,status=excluded.status,progress=excluded.progress,
                last_score=excluded.last_score,description=excluded.description,
                source='ai',plan_id=excluded.plan_id,position=excluded.position,
                core=excluded.core,points_json=excluded.points_json''',
                (slug,item['title'],status,progress,score,item['subtitle'],'ai',plan_id,idx,
                 item['core'],json.dumps(item['points'],ensure_ascii=False)))
        active=con.execute('SELECT active_lesson FROM profile WHERE id=1').fetchone()[0]
        if active not in slugs:
            active=slugs[0]
        con.execute('UPDATE profile SET topic=?,level=?,objective=?,active_lesson=?,updated_at=? WHERE id=1',
                    (topic,level,objective,active,now()))
        return plan_id


def select_lesson(slug):
    with connect() as con:
        plan_id,ls=active_plan(con)
        if slug not in {l['slug'] for l in ls}:
            return False
        con.execute('UPDATE profile SET active_lesson=?,updated_at=? WHERE id=1',(slug,now()))
        return True


def save_message(slug,role,content,mode):
    with connect() as con:
        con.execute('INSERT INTO messages(lesson_slug,role,content,mode,created_at) VALUES(?,?,?,?,?)',
                    (slug,role,content,mode,now()))
        if role=='user':
            con.execute("UPDATE lessons SET status='进行中', progress=MAX(progress,15) WHERE slug=? AND status='未开始' AND source='ai'",(slug,))


def save_note(slug,note):
    clean=note.strip()[:500]
    if not clean: return
    with connect() as con:
        con.execute('INSERT INTO study_notes(lesson_slug,note,created_at) VALUES(?,?,?)',(slug,clean,now()))
        record_event(con,'save_learning_note',{'lesson_slug':slug,'note':clean})


def store_quiz_question(slug,payload):
    with connect() as con:
        cur=con.execute('INSERT INTO quiz_questions(lesson_slug,payload,created_at) VALUES(?,?,?)',
                        (slug,json.dumps(payload,ensure_ascii=False),now()))
        return cur.lastrowid


def load_quiz_question(question_id,slug):
    with connect() as con:
        row=con.execute('SELECT payload FROM quiz_questions WHERE id=? AND lesson_slug=?',(question_id,slug)).fetchone()
        return json.loads(row['payload']) if row else None


def latest_quiz_question(slug):
    with connect() as con:
        r=con.execute('SELECT id,payload FROM quiz_questions WHERE lesson_slug=? ORDER BY id DESC LIMIT 1',(slug,)).fetchone()
        return (r['id'],json.loads(r['payload'])) if r else (None,None)


def save_quiz(slug, choice, answer,score,dimensions,feedback,weak,mode):
    with connect() as con:
        con.execute('''INSERT INTO quizzes(lesson_slug,choice,short_answer,score,dimensions,feedback,weak_points,evaluation_mode,is_sample,created_at)
            VALUES(?,?,?,?,?,?,?,?,0,?)''',
            (slug,choice,answer,score,json.dumps(dimensions,ensure_ascii=False),
             json.dumps(feedback,ensure_ascii=False),json.dumps(weak,ensure_ascii=False),mode,now()))
        lesson=con.execute("SELECT slug,plan_id,position FROM lessons WHERE slug=? AND source='ai'",(slug,)).fetchone()
        if lesson:
            mastered = score>=80 and not weak
            if mastered:
                con.execute("UPDATE lessons SET status='已掌握',progress=100,last_score=? WHERE slug=?",(score,slug))
                # NEVER move to the next lesson until the user accepts the Agent proposal.
            else:
                con.execute("UPDATE lessons SET status='待巩固',progress=MAX(progress,50),last_score=? WHERE slug=?",(score,slug))
            con.execute('UPDATE profile SET active_lesson=?,updated_at=? WHERE id=1',(slug,now()))
            # Previously accepted advice must not continue masquerading as current.
            con.execute("UPDATE agent_recommendations SET status='stale' WHERE lesson_slug=? AND status='pending'",(slug,))


def clear_demo():
    # No global reset via HTTP; authenticated local-only development explicitly uses this function.
    with connect() as con:
        con.execute('DELETE FROM messages')
        con.execute('DELETE FROM study_notes')
        con.execute('DELETE FROM tool_events')
        con.execute('DELETE FROM quiz_questions')
        con.execute('DELETE FROM quizzes WHERE is_sample=0')
        con.execute("UPDATE lessons SET status='未开始',progress=0,last_score=NULL WHERE source='ai'")
        _,lessons=active_plan(con)
        if lessons: con.execute("UPDATE lessons SET status='进行中' WHERE slug=?",(lessons[0]['slug'],))


def latest_quiz_context():
    """Consistent snapshot for an agent; excludes legacy/sample rows."""
    with connect() as con:
        plan_id,lessons=active_plan(con)
        slugs={x['slug'] for x in lessons}
        row=next((r for r in con.execute('SELECT * FROM quizzes WHERE is_sample=0 ORDER BY id DESC') if r['lesson_slug'] in slugs),None)
        if not row:return None
        lesson=next(x for x in lessons if x['slug']==row['lesson_slug'])
        return {'quiz':decode_quiz(row),'lesson':lesson,'plan_id':plan_id,
                'next_lesson':next((x for x in lessons if x['position']>lesson['position']),None)}


def save_agent_recommendation(quiz_id,lesson_slug,plan_id,action,reason,review_focus,steps,tool_count,model):
    """Persist a *proposal only*. No study plan is updated by this function."""
    with connect() as con:
        existing=con.execute('SELECT id FROM agent_recommendations WHERE quiz_id=?',(quiz_id,)).fetchone()
        if existing:return existing['id']
        con.execute("UPDATE agent_recommendations SET status='stale' WHERE status='pending'")
        row=con.execute("INSERT INTO agent_recommendations (quiz_id,lesson_slug,plan_id,action,reason,review_focus,suggested_steps,tool_count,model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(quiz_id,lesson_slug,plan_id,action,reason,review_focus,
            json.dumps(steps,ensure_ascii=False),tool_count,model,now()))
        return row.lastrowid


def decide_agent_recommendation(rec_id,decision):
    if decision not in ('accept','reject'):raise ValueError('invalid decision')
    # One SQLite transaction: no partially applied recommendation.
    with connect() as con:
        r=con.execute('SELECT * FROM agent_recommendations WHERE id=?',(rec_id,)).fetchone()
        if not r:return {'ok':False,'reason':'not_found'}
        if r['status']!='pending':return {'ok':False,'reason':'already_decided'}
        latest=con.execute('SELECT id FROM quizzes WHERE is_sample=0 ORDER BY id DESC LIMIT 1').fetchone()
        plan_id,_=active_plan(con)
        snapshot_latest=next((x for x in con.execute('SELECT id,lesson_slug FROM quizzes WHERE is_sample=0 ORDER BY id DESC') if x['lesson_slug']==r['lesson_slug']),None)
        if not latest or r['quiz_id']!=latest['id'] or not snapshot_latest or snapshot_latest['id']!=r['quiz_id'] or r['plan_id']!=plan_id:
            con.execute("UPDATE agent_recommendations SET status='stale' WHERE id=?",(rec_id,))
            return {'ok':False,'reason':'stale'}
        if decision=='reject':
            con.execute("UPDATE agent_recommendations SET status='rejected',applied_at=? WHERE id=?",(now(),rec_id))
            return {'ok':True,'action':'rejected'}
        if r['action']=='continue':
            item=con.execute('SELECT position FROM lessons WHERE slug=? AND plan_id=?',(r['lesson_slug'],plan_id)).fetchone()
            nxt=con.execute("SELECT slug FROM lessons WHERE plan_id=? AND source='ai' AND position>? ORDER BY position LIMIT 1",
                (plan_id,item['position'] if item else 9999)).fetchone()
            if nxt:con.execute('UPDATE profile SET active_lesson=?,updated_at=? WHERE id=1',(nxt['slug'],now()))
            action='continue' if nxt else 'completed_path'
        else:
            con.execute("INSERT INTO review_tasks (recommendation_id,lesson_slug,focus,reason,created_at) VALUES (?,?,?,?,?)",(rec_id,r['lesson_slug'],r['review_focus'] or '复习本次测验的薄弱点',r['reason'],now()))
            con.execute('UPDATE profile SET active_lesson=?,updated_at=? WHERE id=1',(r['lesson_slug'],now()))
            action='review'
        con.execute("UPDATE agent_recommendations SET status='accepted',applied_at=? WHERE id=?",(now(),rec_id))
        return {'ok':True,'action':action}
