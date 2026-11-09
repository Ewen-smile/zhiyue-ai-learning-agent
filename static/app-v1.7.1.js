(() => {
  'use strict';
  const pages = ['home', 'study', 'quiz', 'feedback', 'growth'];
  const q = (sel, root = document) => root.querySelector(sel);
  const qq = (sel, root = document) => [...root.querySelectorAll(sel)];
  const selected = {page:'home',state:null,busy:false,toastTimer:null,quiz:null,quizBusy:false,lastQuestion:''};
  const SAMPLE_CHAT = '';  // Legacy seed conversation must never be shown as a real user conversation.
  const CORE_LESSON_HTML=q('#view-study .lesson-card')?.innerHTML || '';
  const CORE_GOALS_HTML=q('#view-study .goal-list')?.innerHTML || '';


  // Browser-local greeting. Nothing is hardcoded to 上午好.
  function updateLocalGreeting(){
    const h=new Date().getHours();
    const word=h>=5&&h<11?'早上好':h>=11&&h<13?'中午好':h>=13&&h<18?'下午好':h>=18&&h<23?'晚上好':'夜深了';
    text('#time-greeting',word);
  }
  updateLocalGreeting();
  window.addEventListener('focus',updateLocalGreeting);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)updateLocalGreeting()});
  // Set visible mode using server-returned state; SDK code is not proof of an active model.
  function updateModelStatus(mode, verified=false){
    const el=q('#ai-mode-indicator');
    if(!el)return;
    el.dataset.mode=mode==='ai'?'ai':mode==='error'?'error':'demo';
    if(mode==='ai'){
      el.textContent=verified?'● AI 已连接':'● AI 已配置';
      el.title=verified?'本次请求已成功获取大模型回复':'已配置模型，发送消息后验证能否正常连接';
    }else if(mode==='error'){
      el.textContent='! AI 连接失败';
      el.title='模型请求失败，请检查服务器终端中的报错';
    }else{
      el.textContent='○ 规则演示';
      el.title='当前未连接真实模型，回答由本地规则生成';
    }
  }
  function updateChatDisclaimer(mode, toolCount=null){
    const el=q('#view-study .tutor-disclaimer');
    if(!el)return;
    if(mode==='ai' && toolCount!==null){
      el.textContent=`真实 AI 已连接 · 本次执行 ${Number(toolCount)||0} 次工具调用 · AI 回答请核对`;
    }else if(mode==='ai'){
      el.textContent='已配置 AI 模型 · 发送消息后验证连接和工具调用';
    }else if(mode==='error'){
      el.textContent='AI 模型连接异常 · 请检查后端报错，勿将密钥发给他人';
    }else{
      el.textContent='规则演示模式 · 当前回答不来自实时大模型';
    }
  }

  function toast(message) {
    const box=q('#toast'); if(!box){console.warn('提示：',message);return;} box.textContent=message; box.classList.add('visible');
    clearTimeout(selected.toastTimer);selected.toastTimer=setTimeout(()=>box.classList.remove('visible'),4200);
  }
  async function api(path, opts={}) {
    let res;
    try { res=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...opts}); }
    catch (err) {throw new Error('无法连接后端，请确认已启动 python app.py');}
    const body=await res.json().catch(()=>({}));
    if(!res.ok)throw new Error(typeof body.detail==='string'?body.detail:`请求失败（HTTP ${res.status}）`);
    return body;
  }
  function text(sel,value,root=document){const el=q(sel,root); if(el && value !==undefined && value!==null)el.textContent=String(value);}
  function html(sel,value,root=document){const el=q(sel,root); if(el)el.innerHTML=value;}
  function navigate(pg,opts={}) {
    if(!pages.includes(pg))pg='home';
    if((pg==='study'||pg==='quiz') && !selected.state?.has_ai_path){
      pg='home';toast('请先输入学习目标，让 AI 生成专属学习路径。');
    }
    selected.page=pg;
    qq('.screen').forEach(el=>el.classList.toggle('active',el.dataset.page===pg));
    const area=q('.screen.active .view-area');if(area)area.scrollTop=0;
    const url=new URL(location.href);url.searchParams.set('screen',pg);history.replaceState({},'',url);
    if(pg==='feedback')renderFeedback();
    if(pg==='growth')renderGrowth();
    if(pg==='quiz')renderQuiz();
    if(pg==='study' && opts.review){
      const question='请根据我最近一次真实测验的薄弱点，换个例子讲解。';
      const input=q('#chat-input');
      if(input){input.value=question;input.focus();}
      // The user explicitly started targeted review: provide the explanation in one click.
      // In demo/disconnected mode leave the draft intact; do not claim AI work happened.
      if(selected.state?.mode==='ai' && !selected.busy){
        Promise.resolve().then(()=>sendChat(question));
      }
    }
  }
  document.addEventListener('click',e=>{
    const el=e.target.closest('[data-go]');if(!el)return;
    navigate(el.dataset.go,{review:!!el.closest('#view-feedback') || el.classList.contains('review-btn')});
  });
  const modal=q('#goal-modal');
  function showModal(){navigate('home');fillModal();modal.classList.add('visible'); q('#goal-topic')?.focus();}
  function closeModal(){modal.classList.remove('visible')}
  q('#open-goal')?.addEventListener('click',showModal);
  q('#close-goal')?.addEventListener('click',closeModal);
  modal?.addEventListener('click',(e)=>{if(e.target===modal)closeModal()});
  qq('[data-level]').forEach(b=>b.addEventListener('click',()=>{
    qq('[data-level]').forEach(x=>x.classList.remove('chosen')); b.classList.add('chosen');
  }));
  function fillModal(){
    const state=selected.state;
    const p=state?.profile;
    // A legacy placeholder isn't a user-created goal. Never pre-fill it in the modal.
    const hasPlan=Boolean(state?.has_ai_path && state?.lessons?.length);
    q('#goal-topic').value=hasPlan?(p?.topic||''):'';
    q('#goal-description').value=hasPlan?(p?.objective||''):'';
    q('#goal-focus-question').value='';
    const levels={'刚开始了解':'入门了解','有一些基础':'有一定基础','希望进一步提升':'希望进阶'};
    const target=hasPlan?(levels[p?.level]||'有一定基础'):'入门了解';
    qq('[data-level]').forEach(b=>b.classList.toggle('chosen',b.getAttribute('data-level')===target));
  }
  q('#save-goal')?.addEventListener('click',async ()=>{
    const chosen=q('[data-level].chosen');
    const levels={'入门了解':'刚开始了解','有一定基础':'有一些基础','希望进阶':'希望进一步提升'};
    const payload={topic:q('#goal-topic').value.trim(),level:levels[chosen?.getAttribute('data-level')]||'有一些基础',
       objective:q('#goal-description').value.trim(),focus_question:q('#goal-focus-question')?.value.trim()||''};
    if(payload.topic.length<2||payload.objective.length<3)return toast('请填写学习主题和希望达到的目标。');
    const btn=q('#save-goal');btn.disabled=true;btn.textContent='小跃正在分析目标并生成路线…';
    try{const r=await api('/goal',{method:'POST',body:JSON.stringify(payload)});
      closeModal();selected.quiz=null;await refresh();toast(r.message);
    }catch(err){toast(err.message)}finally{btn.disabled=false;btn.textContent='为我生成专属学习路径 →';}
  });
  function homeSummary(state){
    const {profile,lessons}=state;
    const hasPlan=Boolean(state.has_ai_path && lessons.length);
    const completed=lessons.filter(x=>x.status==='已掌握').length;
    const reviewed=lessons.filter(x=>x.status==='待巩固').length;
    const current=hasPlan?(lessons.find(x=>x.slug===profile.active_lesson)||lessons[0]):null;
    const goalCard=q('#view-home .goal-card');
    goalCard?.classList.toggle('is-empty',!hasPlan);
    const goalProgress=q('#view-home .goal-progress');
    if(goalProgress)goalProgress.style.display=hasPlan?'flex':'none';
    const emptyNote=q('#goal-empty-note');if(emptyNote)emptyNote.hidden=hasPlan;
    text('#goal-card-kicker-label',hasPlan?'当前学习目标':'你的学习目标');
    text('#goal-title',hasPlan?profile.topic:'尚未设置学习目标');
    text('#goal-subtitle',hasPlan?profile.objective:'先设定学习主题与目标，AI 才会生成你的学习路径。');
    text('#open-goal-label',hasPlan?'编辑学习目标':'设置学习目标');
    text('#view-home .page-heading p',hasPlan?'今天也来积累一点新知识吧。':'从感兴趣的问题开始，开启第一条学习路径。');
    const progress=lessons.length?Math.round(100*completed/lessons.length):0;
    text('#home-progress',progress+'%');
    html('#home-completed',`${completed} <small>/ ${lessons.length}</small>`);
    const bar=q('#home-progress-bar');if(bar)bar.style.width=progress+'%';
    const donut=q('#view-home .donut');if(donut)donut.style.background=`conic-gradient(#EFAFD1 ${progress}%,#EAE3DA ${progress}%)`;
    const idx=lessons.findIndex(x=>x.slug===current?.slug);
    // Homepage = today's study actions; quiz scores belong to Feedback/Growth only.
    // These values are derived from the actual current learning path, not fixtures.
    text('#home-today',hasPlan?`${idx+1} / ${lessons.length}`:'—');
    text('#home-review',`${reviewed} 个`);
    text('#home-hours',!hasPlan?'设置学习目标':reviewed?'针对性复习':current?'继续学习':'探索新课');
    const metric=qq('#view-home .metric');
    if(metric[0]){text('small','当前章节',metric[0]);text('span',current?current.title:'先创建学习路径',metric[0]);}
    if(metric[1]){text('small','待巩固知识',metric[1]);text('span',reviewed?'根据真实诊断安排复习':'暂无待巩固模块',metric[1]);}
    if(metric[2]){text('small','下一步行动',metric[2]);text('span',reviewed?'优先巩固薄弱知识':current?'从当前模块继续学习':'创建你的第一条学习路径',metric[2]);}
    text('#home-next-kicker','✦ LEARN & GROW');
    text('#home-next-title','保持好奇，慢慢向前。');
    text('#home-next-desc','每一次探索，都值得被记录。');
    const heroCard=q('#view-home .hero-card');heroCard?.classList.toggle('is-empty',!hasPlan);
    const heroAction=q('#view-home .hero-card .black-button');
    if(heroAction)heroAction.textContent=hasPlan?'继续探索 →':'创建学习路径 →';
    const root=q('#view-home .path-row');if(!root)return;
    root.style.setProperty('--path-count',String(Math.min(6,Math.max(1,lessons.length))));
    if(!lessons.length){
      root.innerHTML='<div class="path-empty">还没有 AI 生成的学习路径。<button type="button" id="path-start" class="black-button">设置目标并生成 →</button></div>';
    }else{
      const colors=['pink-soft','yellow-soft','blue-soft','lavender-soft','sage-soft','pink-soft'];
      root.innerHTML=lessons.map((l,i)=>`<div class="path-step ${colors[i%colors.length]} ${l.status==='未开始'?'locked':''}" role="button" tabindex="0" data-dynamic-lesson="${escapeHtml(l.slug)}">
          <div class="path-top"><span class="path-icon">${i+1}</span><span class="step-status">${escapeHtml(l.status)}</span></div>
          <div class="step-desc"><b title="${escapeHtml(l.title)}">${escapeHtml(l.title)}</b><small title="${escapeHtml(l.subtitle)}">${escapeHtml(l.subtitle)}</small></div>
          <div class="line-track"><span style="width:${Math.max(0,Math.min(l.progress||0,100))}%"></span></div>
        </div>`).join('');
    }
    const tag=q('#view-home .section-title .section-tag');
    if(tag)tag.textContent=current?'AI 已生成 · 可按提问调整':'等待生成';
    const desc=q('#view-home .section-title p');if(desc)desc.textContent='以你的学习目标与提问为依据，生成可调整的路径。';
    let refine=q('#path-refine-button');
    if(!refine){refine=document.createElement('button');refine.id='path-refine-button';refine.type='button';
      refine.className='path-refine-button';q('#view-home .section-title')?.appendChild(refine);}
    refine.textContent='✧ 按最近提问调整路径';refine.hidden=!current;
  }
  function renderFeedback(){
    const state=selected.state,quiz=state?.latest_quiz;
    if(!quiz){
      html('#feedback-score','— <small>/ 100 分</small>');
      text('#feedback-source','暂无真实测验记录');
      text('#feedback-summary','完成一次 AI 测验后，这里将出现评分结果和改进建议。');
      text('#feedback-heading','尚未完成测验');
      ['#feedback-good','#feedback-improve','#feedback-next','#feedback-q1','#feedback-q2'].forEach(k=>text(k,'暂无记录'));
      renderAgentAdvice();return;
    }
    const f=quiz.feedback||{};
    html('#feedback-score',`${quiz.score} <small>/ 100 分</small>`);
    text('#feedback-source',`真实测验 · ${quiz.evaluation_mode==='ai'?'AI 语义评价':'历史规则评分'}`);
    text('#feedback-summary',f.summary);
    qq('#view-feedback .mastery-row').forEach(el=>{
      const v=Number(quiz.dimensions?.[el.dataset.dimension]||0);
      const labels=quiz.feedback?.dimension_labels||{};
      text('label',labels[el.dataset.dimension]||({'concept':'概念理解','flow':'逻辑表达','application':'实践应用'}[el.dataset.dimension]),el);
      const level=v>=85?'扎实':v>=65?'基本理解':'待加强';
      text('b',level,el);const fill=q('.mastery-track span',el);if(fill)fill.style.width=v+'%';
    });
    const oldConflict=quiz.score===100 && (quiz.weak_points||[]).length>0;
    const mastered=quiz.score>=80 && (!(quiz.weak_points||[]).length || oldConflict);
    if(oldConflict)text('#feedback-summary','旧版测验记录存在满分与薄弱点矛盾；原始成绩已保留，请重新完成一次测验获取可信诊断。');
    text('#feedback-heading',mastered?'本次达标，继续探索新知识！':'针对本次遗漏，再巩固一下吧。');
    const keys={good:'feedback-good',improve:'feedback-improve',next:'feedback-next',review_q1:'feedback-q1',review_q2:'feedback-q2'};
    for(const [k,id] of Object.entries(keys))text('#'+id,f[k]||'暂无具体建议');
    const recap=qq('#view-feedback .recap');
    if(recap[0])text('b',f.mcq_score===40?'第 1 题 · 单选题回答正确':'第 1 题 · 单选题需要巩固',recap[0]);
    if(recap[1])text('b',f.short_answer_score===60?'第 2 题 · 简答题得分完整':'第 2 题 · 对照参考答案查漏补缺',recap[1]);
    const improveTitle=q('#feedback-improve')?.previousElementSibling;
    if(improveTitle)improveTitle.textContent=mastered?'进阶建议':'需要加强';

    const action=q('#feedback-next-action');if(action)action.textContent=mastered?'继续学习 →':'针对性复习 →';
    renderAgentAdvice();
    const heading=q('#view-feedback .page-heading p');if(heading)heading.textContent='最近一次真实测验 · AI 反馈';
  }
  function renderAgentAdvice(){
    const mount=q('#agent-advice-card');if(!mount)return;
    const footer=q('#view-feedback .feedback-sticky-actions');
    const defaultActions=q('#view-feedback .feedback-actions');
    footer?.querySelector('.agent-advice-buttons')?.remove();
    footer?.querySelector('#generate-agent-advice')?.remove();
    footer?.classList.remove('has-agent-action');
    if(defaultActions)defaultActions.hidden=false;
    const state=selected.state,rec=state?.agent_recommendation;
    const recent=state?.latest_agent_recommendation;
    const quiz=state?.latest_quiz;
    if(!quiz){mount.innerHTML='<p>完成真实测验后，小跃才能根据学习记录提出建议。</p>';return;}
    if(rec && rec.quiz_id===quiz.id){
      mount.innerHTML=`<div class="agent-advice-header"><b>✧ 小跃的下一步建议</b><span>真实 Agent · 已调用 ${Number(rec.tool_count)||0} 个工具</span></div>
        <h3>${rec.action==='review'?'建议针对性复习':'建议进入下一知识模块'}</h3>
        <p>${escapeHtml(rec.reason)}</p>
        ${rec.review_focus?`<p><b>复习重点：</b>${escapeHtml(rec.review_focus)}</p>`:''}
        <ol>${(rec.suggested_steps||[]).map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ol>
        <div class="agent-advice-buttons"><button type="button" class="black-button" data-agent-decision="accept">同意，更新学习计划</button>
        <button type="button" class="light-button" data-agent-decision="reject">暂不调整</button></div>
        <small>只有你确认后，Agent 建议才会被应用。你可以拒绝。</small>`;
    } else if(recent?.quiz_id===quiz.id && recent?.status!=='pending') {
      mount.innerHTML=`<div class="agent-advice-header"><b>✧ 小跃的下一步建议</b></div><p>这次建议已${recent.status==='accepted'?'确认执行':recent.status==='rejected'?'被你暂缓':'过期'}。重新测验后可获取新建议。</p>`;
    }else{
      mount.innerHTML=`<div class="agent-advice-header"><b>✧ 小跃的下一步建议</b><span>读取真实测验后分析</span></div>
      <p id="agent-advice-note">让小跃结合学习档案，分析下一步是继续学习还是针对性复习。</p>
      <button type="button" class="black-button" id="generate-agent-advice">让小跃分析下一步 →</button>`;
    }
    // Main action stays visible; the explanation itself is scrollable.
    if(footer){
      const primary=mount.querySelector('.agent-advice-buttons') || mount.querySelector('#generate-agent-advice');
      if(primary){
        footer.appendChild(primary);
        footer.classList.add('has-agent-action');
        if(defaultActions)defaultActions.hidden=true;
        text('.action-hint',rec?'读完建议后，确认或暂缓；未确认前计划不会变。':'让小跃分析真实测验，再决定下一步。',footer);
      }else{
        text('.action-hint','✦ 阅读反馈后，再决定继续学习还是回顾成长记录。',footer);
      }
    }
  }
  async function generateAgentAdvice(){
    const btn=q('#generate-agent-advice');if(btn){btn.disabled=true;btn.textContent='正在读取学习记录并分析…';}
    try{await api('/agent/recommend',{method:'POST'});await refresh();toast('已生成学习建议，请确认是否应用。');}
    catch(e){toast(e.message);if(btn){btn.disabled=false;btn.textContent='重试 Agent 分析 →';}}
  }
  q('#view-feedback')?.addEventListener('click',async e=>{
    if(e.target.closest('#generate-agent-advice')){await generateAgentAdvice();return;}
    const btn=e.target.closest('[data-agent-decision]');if(!btn)return;
    const rec=selected.state?.agent_recommendation;if(!rec)return;
    btn.disabled=true;
    try{const res=await api(`/agent/recommendation/${rec.id}/decision`,{method:'POST',
      body:JSON.stringify({decision:btn.dataset.agentDecision})});
      await refresh();toast(res.action==='review'?'已创建针对性复习任务。':res.action==='continue'?'已切换到下一课程。':res.action==='rejected'?'已保留原学习计划。':'学习路径已完成。');
      if(btn.dataset.agentDecision==='accept'){navigate(res.action==='review'?'growth':'home');}
    }catch(e){toast(e.message);btn.disabled=false;}
  });
  function renderGrowth(){
    const state=selected.state;if(!state)return;
    const {lessons,actual_scores,review_items}=state;
    const completed=lessons.filter(l=>l.status==='已掌握').length;
    const pending=lessons.filter(l=>l.status==='待巩固').length;
    text('#growth-knowledge',`${state.studied_lesson_count} 个`);
    text('#growth-hours',`${state.interaction_count} 次`);
    text('#growth-completed',`${completed} / ${lessons.length}`);
    text('#growth-review',`${pending} 个`);
    const m=qq('#view-growth .growth-metrics .metric');
    if(m[0]){text('small','真实互动过的模块',m[0]);text('span','由提问记录统计',m[0]);}
    if(m[1]){text('small','学习提问次数',m[1]);text('span','只统计已发送的提问',m[1]);}
    if(m[2])text('span','按真实测验成绩判定',m[2]);
    if(m[3])text('span','依据当前路径的测验结果',m[3]);
    const activeLesson=lessons.find(l=>l.slug===state.profile.active_lesson);
    const lastRealQuiz=state.latest_quiz;
    const lastLesson=lessons.find(l=>l.slug===lastRealQuiz?.lesson_slug);
    const storyParts=[];
    if(state.actual_quiz_count)storyParts.push(`已完成 ${state.actual_quiz_count} 次实际测验`);
    if(lessons.length)storyParts.push(`当前路径掌握 ${completed} / ${lessons.length} 个模块`);
    if(lastRealQuiz)storyParts.push(`最近测验「${lastLesson?.title||'历史知识点'}」${lastRealQuiz.score} 分`);
    if(pending)storyParts.push(`${pending} 个知识点等待巩固`);
    text('#growth-story-text',storyParts.length?storyParts.join(' · ')+'。':'还没有真实学习记录。完成一次互动学习或测验后，这里才会形成你的成长故事。');

    const titleTag=q('#view-growth .page-heading .section-tag');if(titleTag)titleTag.textContent='SQLite 真实学习记录';
    const tag=q('#growth-trend .mini-tag');if(tag)tag.textContent=actual_scores.length?`${actual_scores.length} 条真实成绩`:'暂无真实测验';
    const intro=q('#growth-trend .chart-intro');if(intro)intro.textContent='只展示真实测验；不同知识点的成绩不连接为进步或退步趋势。';
    const container=q('#growth-trend .linechart');
    if(container){
      if(!actual_scores.length){container.innerHTML='<div class="real-chart-empty">完成第一次智能测验后，这里才会生成成绩趋势。</div>';}
      else{
        const history=(state.quiz_history||[]).slice(-8);
        const scores=history.length?history:actual_scores.slice(-8).map(score=>({score,slug:''}));
        const minX=66,maxX=558;
        const coords=scores.map((item,i)=>({
          x:scores.length===1?Math.round((minX+maxX)/2):minX+i*(maxX-minX)/Math.max(1,scores.length-1),
          y:215-1.78*Math.max(0,Math.min(100,Number(item.score)||0)),
          score:Number(item.score)||0,slug:item.slug||'',
          title:lessons.find(l=>l.slug===item.slug)?.title || '历史知识点'
        }));
        // Connect results only when they are consecutive tests of the SAME lesson.
        const comparable=coords.slice(1).map((p,i)=>({a:coords[i],b:p})).filter(({a,b})=>a.slug && a.slug===b.slug);
        container.innerHTML=`<svg viewBox="0 0 610 272" preserveAspectRatio="xMidYMid meet" class="real-trend-svg" role="img" aria-label="按知识点展示的真实测验成绩，不跨知识点连接曲线">
          ${[0,20,40,60,80,100].map(v=>`<line x1="38" y1="${215-1.78*v}" x2="580" y2="${215-1.78*v}" stroke="#E9DED6" stroke-dasharray="4 6"/><text x="7" y="${220-1.78*v}" font-size="12" fill="#8D8485">${v}</text>`).join('')}
          ${comparable.map(({a,b})=>`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#D78BB5" stroke-width="3.5" stroke-linecap="round"/>`).join('')}
          ${coords.map((p,i)=>`<circle cx="${p.x}" cy="${p.y}" r="6" stroke="#D78BB5" fill="#FFF9F1" stroke-width="3"><title>${escapeHtml(p.title)} · ${p.score} 分</title></circle><text x="${p.x}" y="${Math.max(15,p.y-14)}" font-size="12" font-weight="700" text-anchor="middle" fill="#6C4860">${p.score}分</text><text x="${p.x}" y="243" font-size="12" text-anchor="middle" fill="#93858B">第${state.actual_quiz_count-scores.length+i+1}次</text><text x="${p.x}" y="260" font-size="10" text-anchor="middle" fill="#98848F">${escapeHtml(p.title.length>8?p.title.slice(0,7)+'…':p.title)}</text>`).join('')}
          </svg>`;
      }
    }
    const task=state.review_tasks?.[0];
    const review=q('#growth-review-card');
    if(review){
      const entry=q('.review-entry',review),button=q('.review-btn',review);
      const first=review_items?.[0] || (task?{slug:task.lesson_slug,title:'Agent 已安排的复习',point:task.focus}:null);
      if(entry){
        entry.innerHTML=first
          ? `<div class="review-icon">✦</div><div><b>${escapeHtml(first.title)}</b><span>${escapeHtml(first.point)}</span></div><span class="review-score">待复习</span>`
          : '<span>暂无真实测验诊断出的薄弱点</span>';
      }
      if(button){button.disabled=!first;button.textContent=first?'开始针对性复习 →':'完成测验后推荐复习';}
    }
    const history=q('#view-growth .history-card');
    if(history){
      qq('.history-row',history).forEach(n=>n.remove());
      if(!lessons.length){
        const empty=document.createElement('div');empty.className='history-empty';empty.textContent='请先生成 AI 学习路径，真实学习记录会自动保存。';history.appendChild(empty);
      }else{
        const old=q('.history-empty',history);if(old)old.remove();
        for(const l of lessons){
          const row=document.createElement('div');row.className='history-row';row.dataset.dynamicLesson=l.slug;
          const status=escapeHtml(l.status),score=l.last_score==null?'尚未测验':l.last_score+' 分';
          row.innerHTML=`<span>${escapeHtml(l.title)}</span><span class="state-tag">${status}</span><span>${score}</span><button type="button" data-select-lesson="${escapeHtml(l.slug)}">进入学习 →</button>`;
          history.appendChild(row);
        }
      }
    }
  }
  function renderStudy(){
    const state=selected.state,active=state?.profile.active_lesson;
    const lesson=state?.lessons.find(x=>x.slug===active);
    const card=q('#view-study .lesson-card'),goals=q('#view-study .goal-list');
    if(!card||!goals)return;
    if(!lesson){
      text('#study-title','尚未生成学习路径');text('#study-subtitle','请先前往首页设置学习目标');
      card.innerHTML='<div class="real-chart-empty">AI 生成学习路径后，这里会展示对应课程。</div>';
      goals.innerHTML='';return;
    }
    text('#study-title',lesson.title);text('#study-subtitle',lesson.subtitle);
    const position=state.lessons.findIndex(l=>l.slug===active);
    text('#view-study .study-titlebar .eyebrow',`PERSONAL LEARNING · CHAPTER ${String(position+1).padStart(2,'0')}`);
    if(card.dataset.active!==lesson.slug){card.innerHTML=CORE_LESSON_HTML;card.dataset.active=lesson.slug;}
    text('#study-core-title',lesson.title);
    text('.lesson-card>p',lesson.core,q('#view-study'));
    qq('.flow-node',card).forEach((node,i)=>{
      text('b',lesson.points?.[i]||'核心知识',node);
      text('small',`知识目标 ${i+1}`,node);
    });
    text('.example-box span','这节课根据你的目标由 AI 动态生成。遇到不理解的内容，可以直接问小跃。',card);
    goals.innerHTML=(lesson.points||[]).map(x=>`<span>✓ ${escapeHtml(x)}</span>`).join('');
    const btn=q('#view-study .lesson-actions .black-button');if(btn)btn.textContent='我理解了，开始 AI 测验 →';
    const step=q('#view-study .lesson-progress strong');if(step){
      const i=state.lessons.findIndex(l=>l.slug===active);step.textContent=`${i+1} / ${state.lessons.length}`;
    }
  }
  function escapeHtml(text){return String(text).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
  // Render a safe, self-contained subset of Markdown. Escape raw HTML first;
  // do not load remote Markdown scripts or directly insert model-supplied HTML.
  function renderInlineMarkdown(value){
    const codes=[];
    let s=escapeHtml(value).replace(/`([^`\n]+)`/g,(_,contents)=>{
      const index=codes.push(`<code>${contents}</code>`)-1;
      return `\uE000${index}\uE001`;
    });
    s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
    s=s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,'$1<em>$2</em>');
    return s.replace(/\uE000(\d+)\uE001/g,(_,index)=>codes[Number(index)]||'');
  }
  function renderChatMarkdown(message){
    const lines=String(message??'').replace(/\r\n?/g,'\n').split('\n');
    const output=[];
    let paragraph=[],list=null,code=null;
    const flushParagraph=()=>{if(paragraph.length){output.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`);paragraph=[];}};
    const closeList=()=>{if(list){output.push(`</${list}>`);list=null;}};
    for(const raw of lines){
      const line=raw.trim();
      if(code!==null){
        if(/^```\s*$/.test(line)){
          output.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);code=null;
        }else{code.push(raw);}
        continue;
      }
      if(/^```[a-zA-Z0-9_-]*\s*$/.test(line)){
        flushParagraph();closeList();code=[];continue;
      }
      if(!line){flushParagraph();closeList();continue;}
      const bullet=/^[-*+]\s+(.+)$/.exec(line);
      const numbered=/^\d+[.)]\s+(.+)$/.exec(line);
      if(bullet||numbered){
        flushParagraph();
        const kind=bullet?'ul':'ol';
        if(list!==kind){closeList();output.push(`<${kind}>`);list=kind;}
        output.push(`<li>${renderInlineMarkdown((bullet||numbered)[1])}</li>`);
        continue;
      }
      closeList();
      const heading=/^#{1,3}\s+(.+)$/.exec(line);
      if(heading){flushParagraph();output.push(`<p class="chat-md-heading">${renderInlineMarkdown(heading[1])}</p>`);}
      else{paragraph.push(line);}
    }
    if(code!==null)output.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
    flushParagraph();closeList();
    return output.join('')||'<p></p>';
  }
  function addChat(role,message,busy=false){
    const chat=q('#chat-area');if(!chat)return null;
    const row=document.createElement('div');row.className=`chat-line ${role==='user'?'from-user':'from-tutor'} ${busy?'chat-processing':''}`;
    if(role!=='user'){
      const avatar=document.createElement('div');avatar.className='chat-ava';avatar.textContent='✦';row.appendChild(avatar);
    }
    const b=document.createElement('div');b.className='chat-bubble';
    if(role==='user'||busy)b.textContent=message;
    else b.innerHTML=renderChatMarkdown(message);
    row.appendChild(b);
    chat.appendChild(row);chat.scrollTop=chat.scrollHeight;return row;
  }
  function renderChat(){
    const chat=q('#chat-area');if(!chat)return;
    chat.innerHTML='';
    const slug=selected.state?.profile.active_lesson;
    const lesson=selected.state?.lessons.find(l=>l.slug===slug);
    if(lesson){
      addChat('assistant',`欢迎学习「${lesson.title}」🌷。课程由 AI 根据你的目标生成。哪里不理解，都可以直接问我。`);
      for(const x of selected.state?.messages||[])addChat(x.role,x.content);
    }else addChat('assistant','请先在首页创建专属学习路径，我就能根据你的目标和提问陪你学习。');
    updateChatDisclaimer(selected.state?.mode);
  }
  async function sendChat(textInput){
    const inp=q('#chat-input');const message=String(textInput??inp?.value??'').trim();
    if(!message || selected.busy)return;
    selected.busy=true;const button=q('#chat-send');if(button)button.disabled=true;
    if(inp)inp.value='';addChat('user',message);
    const thinking=addChat('assistant','小跃正在思考…',true);
    try{
      const slug=selected.state?.profile.active_lesson||'';
      const res=await api('/chat',{method:'POST',body:JSON.stringify({message,lesson_slug:slug})});
      thinking?.remove();addChat('assistant',res.reply);
      updateModelStatus(res.mode,res.mode==='ai');
      updateChatDisclaimer(res.mode,res.mode==='ai'?res.tool_count:null);
      if(res.mode==='error')toast('真实模型连接失败，请检查后端环境变量。');
      if(res.mode==='demo')toast('未配置真实模型；本次不提供虚构的 AI 回答。');
      selected.lastQuestion=message;
      if(selected.state){selected.state.messages.push({role:'user',content:message},{role:'assistant',content:res.reply});
        selected.state.interaction_count=(selected.state.interaction_count||0)+1;}
      if(res.mode==='ai')toast('已记录本次真实提问；回到首页可按最近提问调整学习路径。');
    }catch(e){thinking?.remove();addChat('assistant','抱歉，消息发送失败。请确认后端服务正常。');toast(e.message)}
    finally{selected.busy=false;if(button)button.disabled=false;}
  }
  q('#chat-send')?.addEventListener('click',()=>sendChat());
  q('#chat-input')?.addEventListener('keydown',(e)=>{if(e.key==='Enter'){e.preventDefault();sendChat();}});
  q('#ask-example')?.addEventListener('click',()=>sendChat('换一个生活中的例子'));
  q('#quick-ask-1')?.addEventListener('click',()=>sendChat('请再讲简单一点'));
  q('#quick-ask-2')?.addEventListener('click',()=>sendChat('请考考我'));

  async function renderQuiz(){
    if(selected.quizBusy)return;
    const slug=selected.state?.profile.active_lesson;
    const lesson=selected.state?.lessons.find(l=>l.slug===slug);
    if(!lesson)return;
    const root=q('#view-quiz');
    const question=q('.questions-panel .q-card h3',root);
    const options=q('.questions-panel .q-card .options',root);
    const shortCard=q('.q-card.q-short',root);
    const note=q('.quiz-note',root);
    const button=q('#submit-quiz');
    if(!question||!options||!shortCard)return;
    const heading=q('.page-heading p',root);
    if(heading)heading.textContent=`${lesson.title} · AI 动态出题 · 2 道小练习`;
    selected.quizBusy=true;
    selected.quiz=null;
    // Old HTML contains legacy Function Calling sample questions. Never show them
    // while generating a quiz for an unrelated lesson, or after generation fails.
    options.style.display='none';
    shortCard.style.display='none';
    if(note)note.style.display='none';
    q('#quiz-retry')?.remove();
    question.textContent='AI 正在为「'+lesson.title+'」生成测验题目，请稍候…';
    qq('input[name=q1]',root).forEach(el=>el.checked=false);
    const essay=q('#quiz-answer');if(essay)essay.value='';
    text('#word-count','0 / 600');
    if(button)button.disabled=true;
    try{
      const result=await api('/quiz/generate',{method:'POST',body:JSON.stringify({lesson_slug:slug})});
      if(selected.state?.profile.active_lesson!==slug || selected.page!=='quiz')return;
      if(!result.quiz_id || result.lesson_slug!==slug || !Array.isArray(result.options)||result.options.length!==4)
        throw new Error('后端返回了不完整的测验数据，请重试。');
      selected.quiz=result;
      question.textContent=result.question;
      qq('.options label.option',root).forEach((el,i)=>{
        const span=el.querySelectorAll('span');
        if(span.length>1)span[1].textContent=String(result.options[i]).replace(/^[A-D][.．、:：\s-]+/i,'');
        const input=q('input',el);if(input)input.checked=false;
      });
      text('.q-short h3',result.short_question,root);
      qq('.quiz-note-row',root).forEach((el,i)=>text('span',lesson.points?.[i]||'理解课程知识点',el));
      options.style.display='';
      shortCard.style.display='';
      if(note)note.style.display='';
      if(button)button.disabled=false;
    }catch(err){
      if(selected.state?.profile.active_lesson!==slug || selected.page!=='quiz')return;
      selected.quiz=null;
      question.textContent='AI 出题未完成，当前没有可提交的测验。';
      const retry=document.createElement('button');
      retry.type='button';retry.id='quiz-retry';retry.className='light-button';
      retry.textContent='重新生成本知识点测验 ↻';
      retry.style.marginTop='16px';retry.style.maxWidth='100%';
      retry.addEventListener('click',renderQuiz);
      question.after(retry);
      toast(err.message||'出题服务暂时不可用，请稍后重试。');
    }finally{selected.quizBusy=false;}
  }
  q('#quiz-answer')?.addEventListener('input',e=>text('#word-count',e.target.value.length+' / 600'));
  q('#submit-quiz')?.addEventListener('click',async ()=>{
    if(!selected.quiz)return toast('题目还没有生成，请稍后重试。');
    const answer=q('#quiz-answer').value.trim(),choice=q('input[name=q1]:checked')?.value;
    if(!choice)return toast('请先选择单选题答案。');
    if(answer.length<3)return toast('请至少写一句自己的理解。');
    const btn=q('#submit-quiz');btn.disabled=true;btn.textContent='AI 正在评估答案…';
    try{
      const res=await api('/quiz/submit',{method:'POST',body:JSON.stringify({
        quiz_id:selected.quiz.quiz_id,choice,short_answer:answer,lesson_slug:selected.quiz.lesson_slug})});
      selected.quiz=null;await refresh();navigate('feedback');toast('真实 AI 评分已保存，小跃将分析下一步学习建议。');
      await generateAgentAdvice();
    }catch(err){toast(err.message)}finally{btn.disabled=false;btn.textContent='提交测验，查看反馈 →';}
  });
  q('#feedback-next-action')?.addEventListener('click',e=>{
    e.stopPropagation();const quiz=selected.state?.latest_quiz;
    const mastered=quiz?.score>=80 && !(quiz.weak_points||[]).length;
    navigate(mastered?'home':'study',{review:!mastered});
  });
  async function chooseLesson(slug){
    if(!slug)return;
    try{await api('/lesson/select',{method:'POST',body:JSON.stringify({slug})});
      selected.quiz=null;await refresh();navigate('study');
    }catch(err){toast(err.message)}
  }
  q('#view-home .path-row')?.addEventListener('click',e=>{
    const el=e.target.closest('[data-dynamic-lesson]');if(el)chooseLesson(el.dataset.dynamicLesson);
    if(e.target.closest('#path-start'))showModal();
  });
  q('#view-home .path-row')?.addEventListener('keydown',e=>{
    const el=e.target.closest('[data-dynamic-lesson]');if(el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();chooseLesson(el.dataset.dynamicLesson);}
  });
  q('#view-growth .history-card')?.addEventListener('click',e=>{
    const el=e.target.closest('[data-select-lesson]');if(el){e.stopPropagation();chooseLesson(el.dataset.selectLesson);}
  });
  q('#view-home .hero-card .black-button')?.addEventListener('click',e=>{
    if(!selected.state?.has_ai_path){e.stopPropagation();showModal();}
  },true);
  q('#view-home .section-title')?.addEventListener('click',async e=>{
    if(e.target.id!=='path-refine-button')return;
    let focus=selected.lastQuestion || selected.state?.messages?.filter(x=>x.role==='user').at(-1)?.content;
    if(!focus){
      focus=window.prompt('你最近最想弄懂的问题是什么？AI 将据此调整学习路线：')?.trim();
    }
    if(!focus||focus.length<4)return toast('请输入至少 4 个字的具体问题。');
    const btn=q('#path-refine-button');btn.disabled=true;btn.textContent='AI 正在调整路线…';
    try{const res=await api('/path/refine',{method:'POST',body:JSON.stringify({focus_question:focus.slice(0,350)})});
      await refresh();toast(res.message);
    }catch(err){toast(err.message)}finally{btn.disabled=false;btn.textContent='✧ 按最近提问调整路径';}
  });
  async function refresh(){
    try{
      selected.state=await api('/state');
      updateModelStatus(selected.state.mode);homeSummary(selected.state);renderFeedback();renderGrowth();renderStudy();renderChat();
    }catch(e){toast(e.message)}
  }
  // Search interaction: custom overlay remains clickable; no new core screen is introduced.
  const searchBox=qq('.search-box');
  function openSearch(){q('#search-modal').classList.add('visible');q('#search-input').focus();search();}
  function closeSearch(){q('#search-modal').classList.remove('visible')}
  searchBox.forEach(box=>{box.addEventListener('click',openSearch);box.addEventListener('keydown',(e)=>{if(e.key==='Enter')openSearch()})});
  q('#search-close')?.addEventListener('click',closeSearch);
  q('#search-modal')?.addEventListener('click',e=>{if(e.target.id==='search-modal')closeSearch()});
  let searchCounter=0;
  async function search(){
    const term=q('#search-input').value.trim();const version=++searchCounter;
    const root=q('#search-results');
    if(!term){root.innerHTML='<p style="padding:9px;color:#8B7F83">搜索你当前 AI 生成路径中的知识点</p>';return;}
    try{const result=await api('/search?q='+encodeURIComponent(term));if(version!==searchCounter)return;
      root.innerHTML=result.results.length?result.results.map(x=>`<div class="search-result" data-result="${escapeHtml(x.slug)}"><b>${escapeHtml(x.title)}</b><small>查看知识点 →</small></div>`).join(''):'<p style="padding:9px;color:#8B7F83">暂无匹配的知识点</p>';
    }catch(e){toast(e.message)}
  }
  q('#search-input')?.addEventListener('input',search);
  q('#search-results')?.addEventListener('click',e=>{
    const r=e.target.closest('[data-result]');if(!r)return;closeSearch();
    chooseLesson(r.dataset.result);
  });
  document.addEventListener('keydown',e=>{
    if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch()}
    if(e.key==='Escape'){closeSearch();closeModal();}
  });
  qq('.screen .round-tool').forEach(el=>el.addEventListener('click',()=>toast('当前是单人 MVP 演示，通知与账号设置暂未开放。')));
  const params=new URLSearchParams(location.search);
  navigate(params.get('screen')||'home');
  refresh();
  if(params.get('state')==='goal')showModal();
})();
