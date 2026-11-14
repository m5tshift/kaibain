// Fixed executor launch controls. Project inputs never select host executables.
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels={queued:'Запускается',running:'Выполняется',waiting_for_input:'Нужен ответ',cancelling:'Останавливается',cancelled:'Остановлен',completed:'На ревью',failed:'Ошибка',interrupted:'Прерван'};
  const activeStatuses=['queued','running','cancelling'];
  let diagnosticReport=null,diagnosticBusy=false,diagnosticError='',diagnosticKey=null;
  let host=null,timer=null,controller=null,loading=false,sending=false,tasks=[],chosen=null,data=null;
  const drafts=new Map();
  const profiles=new Map(),artifactCache=new Map(),selections=new Map();let taskContext=null,restoreFocus=null,selectedProfile=null;
  try{for(const [id,value] of JSON.parse(sessionStorage.getItem('kaibain.project-drafts')||'[]'))profiles.set(id,value);}catch(e){}
  const coding=()=>data?.kind==='development-v1';
  const projectFields=['repo','commit','files','writable','checks','acceptance'];
  function saveProject(){if(!chosen)return;const value={};projectFields.forEach(key=>value[key]=$(`[data-project="${key}"]`).value);profiles.set(chosen.id,value);while(profiles.size>20)profiles.delete(profiles.keys().next().value);try{sessionStorage.setItem('kaibain.project-drafts',JSON.stringify([...profiles]));}catch(e){}}
  function fillProject(){
    const selectedId=selections.has(chosen?.id)?selections.get(chosen.id):chosen?.project_id;
    selectedProfile=data?.projects?.find(p=>p.id===selectedId)||null;
    if(!selectedId&&!selections.has(chosen?.id)&&data?.projects?.filter(p=>!p.archived).length===1)selectedProfile=data.projects.find(p=>!p.archived);
    const saved=chosen&&(profiles.get(chosen.id)||data?.runs.find(r=>r.task_id===chosen.id&&r.project)?.project);
    projectFields.forEach(key=>{const value=saved?.[key];$(`[data-project="${key}"]`).value=Array.isArray(value)?(key==='checks'?JSON.stringify(value):value.join('\n')):value|| (key==='checks'?'["python", "-m", "unittest", "discover", "-s", ".", "-v"]':'');});
    $('.ag-project').open=Boolean(chosen)&&!saved;
    profileOptions();
  }
  function profileOptions(){
    const select=$('.ag-profile-select');if(!select||document.activeElement===select)return;
    const html='<option value="">Разовые настройки</option>'+(data?.projects||[]).filter(p=>!p.archived||p.id===selectedProfile?.id).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.archived?' · в архиве':''}</option>`).join('');
    if(select.innerHTML!==html)select.innerHTML=html;select.value=selectedProfile?.id||'';
  }
  function readProject(){
    const value={};for(const key of projectFields){const text=$(`[data-project="${key}"]`).value.trim();value[key]=['files','writable','acceptance'].includes(key)?text.split(/\r?\n/).map(s=>s.trim()).filter(Boolean):key==='checks'?JSON.parse(text):text;}
    if(!value.repo||!value.commit||!value.files.length||!value.writable.length||!value.acceptance.length||!Array.isArray(value.checks))throw new Error('Заполните параметры проекта. Команда тестов задаётся JSON-массивом аргументов.');
    return value;
  }
  try{for(const [id,text] of JSON.parse(sessionStorage.getItem('kaibain.review-drafts')||'[]'))if(typeof id==='string'&&typeof text==='string')drafts.set(id,text.slice(0,2000));}catch(e){}
  function saveDrafts(){while(drafts.size>20)drafts.delete(drafts.keys().next().value);try{sessionStorage.setItem('kaibain.review-drafts',JSON.stringify([...drafts]));}catch(e){}}
  const $=selector=>host?.querySelector(selector);
  async function request(url,options={}){
    const response=await fetch(url,options),value=await response.json();
    if(!response.ok||!value.ok){const error=new Error(value.error?.message||'Не удалось выполнить запрос.');error.readiness=value.error?.readiness;throw error;}
    return value;
  }
  function error(message){if(!host)return;$('.ag-run-error').textContent=message;$('.ag-run-error').hidden=!message;}
  function diagnosticInput(){return selectedProfile?{project_id:selectedProfile.id,expected_project_version:selectedProfile.version}:{project:readProject()};}
  function diagnosticControls(){
    let key=null;try{key=JSON.stringify(diagnosticInput());}catch(e){}
    if(key!==diagnosticKey&&!diagnosticBusy){diagnosticKey=key;diagnosticReport=null;diagnosticError='';}
    $('.ag-readiness').hidden=!chosen;
    const button=$('.ag-readiness-check');button.disabled=sending||diagnosticBusy||!chosen;button.textContent=diagnosticBusy?'Проверяем…':'Проверить готовность';
    const box=$('.ag-readiness-report'),html=window.readinessUI.html(diagnosticReport);if(box.dataset.html!==html){box.innerHTML=html;box.dataset.html=html;}
    $('.ag-readiness-error').textContent=diagnosticError;$('.ag-readiness-error').hidden=!diagnosticError;
  }
  async function diagnose(){
    if(diagnosticBusy||!chosen)return;
    let input;try{input=diagnosticInput();}catch(e){diagnosticKey=null;diagnosticError='Выберите проект или заполните разовые настройки, включая команду тестов.';diagnosticControls();return;}
    const target=host;diagnosticBusy=true;diagnosticError='';diagnosticReport=null;diagnosticKey=JSON.stringify(input);controls();
    try{const report=await window.readinessUI.check(input);if(host===target)diagnosticReport=report;}
    catch(e){if(host===target)diagnosticError=e.message;}
    finally{if(host===target){diagnosticBusy=false;controls();}}
  }
  function latest(run){return data.runs.find(r=>r.task_id===run.task_id)?.id===run.id;}
  function stopControls(run){
    if(run.status==='cancelling')return '<div class="ag-stop-controls"><span class="spinner"></span><p>Останавливаем агентов… Новые изменения этого прогона уже запрещены. Сохранённые задачи останутся на доске.</p></div>';
    if(run.can_stop)return `<div class="ag-stop-controls"><button type="button" class="secondary danger" data-run-action="stop" data-run="${esc(run.id)}">Остановить прогон</button><p>Задачи и частичные результаты сохранятся.</p></div>`;
    return '';
  }
  function renderRun(run){
    const reviewable=run.status==='completed'&&run.decision==='pending'&&latest(run);
    const versions=run.result?.task_versions, safe=versions&&versions[run.task_id]&&versions[run.result.child_id]&&run.task_id!==run.result.child_id;
    const decision=run.decision==='accepted'?'Принято':run.decision==='changes_requested'?'На доработке':run.status==='waiting_for_input'&&run.input_answered?(latest(run)?'Ожидает продолжения':'Ответ получен'):labels[run.status];
    if(run.status==='completed'&&run.result&&run.project)return `<div class="ag-run-item-heading"><a data-nav href="/tasks/${encodeURIComponent(run.task_id)}">${esc(run.task_title)}</a><span class="ag-run-status completed">${decision}</span></div><small>${esc(new Date(run.created_at).toLocaleString('ru-RU'))} · ${esc(run.provider)}</small><p class="ag-run-output">${esc(run.result.summary)}</p><a class="primary review-open" data-nav href="/reviews/${encodeURIComponent(run.id)}">${run.decision==='accepted'?'Посмотреть и применить результат':'Открыть приёмку результата'} ↗</a>`;
    return `<div class="ag-run-item-heading"><a data-nav href="/tasks/${encodeURIComponent(run.task_id)}">${esc(run.task_title)}</a><span class="ag-run-status ${esc(run.status)}" role="status">${decision||esc(run.status)}</span></div><small>${esc(new Date(run.created_at).toLocaleString('ru-RU'))} · ${esc(run.provider)}${run.revision_of?' · Доработка':''}</small>${run.error?`<p class="ag-run-failure">${esc(run.error)}</p>`:''}${run.result?`<details class="ag-result-details"><summary>Результат исполнителя</summary><p class="ag-run-output">${esc(run.result.summary)}</p>${run.result.checks.length?`<ul>${run.result.checks.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}<a class="parent-link" data-nav href="/tasks/${encodeURIComponent(run.result.child_id)}">Открыть подзадачу с результатом ↗</a></details>`:''}${run.reviewed_at?`<div class="ag-review-decision"><strong>${run.decision==='accepted'?'Результат принят':'Возвращено на доработку'}</strong><small>${esc(new Date(run.reviewed_at).toLocaleString('ru-RU'))}</small>${run.review_comment?`<p>${esc(run.review_comment)}</p>`:''}</div>`:''}${reviewable&&safe?`<div class="ag-review-controls"><p class="ag-review-hint">Примите результат или укажите, что исправить. Решение принимаете только вы. «Принять» завершит эту задачу и подзадачу исполнителя.</p><label class="form-field">Комментарий к результату<textarea class="ag-review-comment" data-review-comment="${esc(run.id)}" maxlength="2000" rows="3" placeholder="Что нужно уточнить или исправить?">${esc(drafts.get(run.id)||'')}</textarea></label><div class="ag-review-actions"><button type="button" class="primary" data-run-action="accepted" data-run="${esc(run.id)}">Принять результат</button><button type="button" class="secondary" data-run-action="changes_requested" data-run="${esc(run.id)}">Вернуть на доработку</button></div></div>`:reviewable?'<p class="ag-review-hint">Этот прогон сохранён без версий задач для приёмки. Его результат доступен для просмотра.</p>':''}${run.decision==='changes_requested'&&latest(run)?`<div class="ag-review-resume"><p class="ag-review-hint">Агенты получат предыдущие файлы и ваши замечания. Подзадача останется той же.</p><button type="button" class="primary" data-run-action="revise" data-run="${esc(run.id)}">Запустить доработку</button></div>`:''}${stopControls(run)}<p class="form-error ag-review-error" role="alert" hidden></p>`;
  }
  function renderArtifacts(run){
    if(!run.project)return '';
    if(run.status==='completed'&&run.result)return '';
    const base=`/api/agent-runs/${encodeURIComponent(run.id)}/artifacts/`,progress=run.progress;
    const stages={independent_review:'Независимое ревью',queued:'Ожидает зависимости или ресурсы',running:'Запускается исполнитель',completed:'Область проверена',blocked:'Ожидает результат зависимости',preparing:'Подготовка исполнителей',sandbox:'Подготовка microVM',inference:'Запрос к модели',waiting_for_input:'Ожидает ответ пользователя',checks:'Выполняются тесты',plan:'План готов',tool:'Работа с файлами',verification:'Финальная проверка',ready_for_review:'Проверки завершены',failed:'Ошибка',cancelled:'Остановлен'};
    return `<div class="ag-development"><p class="ag-run-progress" role="status">${esc(progress?stages[progress.stage]||progress.stage:'Ожидание исполнителя')}${progress?` · шагов: ${esc(progress.steps)} · прогонов тестов: ${esc(progress.checks_completed)}`:''}</p><small class="ag-project-ref">${esc(run.project.repo)} · ${esc(run.project.commit.slice(0,12))}</small>${run.result?.verification?.status==='passed'?'<p class="ag-tests-passed">Тесты пройдены, включая повтор в чистой microVM</p>':''}<nav class="ag-artifact-links" aria-label="Артефакты прогона"><a target="_blank" rel="noopener" href="${base}report">Отчёт</a><a target="_blank" rel="noopener" href="${base}log">Журнал</a>${run.result?`<a target="_blank" rel="noopener" href="${base}manifest">Хеши файлов</a>`:''}</nav>${run.result?`<details class="ag-diff" data-diff-run="${esc(run.id)}"><summary>Изменения (diff)</summary><pre class="ag-diff-content">${esc(artifactCache.get(run.id)||'Загрузка изменений…')}</pre></details><p class="ag-review-hint">Правки сохранены в артефактах. Принятие результата завершает задачи, но не применяет файлы к репозиторию.</p>`:''}</div>`;
  }
  async function loadDiff(details){
    if(!details.open||artifactCache.has(details.dataset.diffRun))return;
    const run=details.dataset.diffRun,pre=details.querySelector('pre');
    try{const response=await fetch(`/api/agent-runs/${encodeURIComponent(run)}/artifacts/diff`,{signal:AbortSignal.timeout(10000)});if(!response.ok)throw new Error('Не удалось загрузить diff. Откройте журнал прогона.');const text=await response.text();artifactCache.set(run,text||'Файлы не изменились.');pre.textContent=artifactCache.get(run);}
    catch(e){pre.textContent=e.message;}
  }
  function history(){
    const box=$('.ag-run-history'),shown=taskContext?data.runs.filter(r=>r.task_id===taskContext):data.runs;
    for(const [i,run] of shown.entries()){
      let article=Array.from(box.children).find(el=>el.dataset.runId===run.id);
      if(!article){article=document.createElement('article');article.className='ag-run-item';article.dataset.runId=run.id;}
      const signature=JSON.stringify([run,latest(run)]);
      if(article.dataset.signature!==signature){const open=article.querySelector('.ag-result-details')?.open,diffOpen=article.querySelector('.ag-diff')?.open;article.innerHTML=renderRun(run)+renderArtifacts(run)+(window.runRecovery?.html(run)||'');if(open&&article.querySelector('.ag-result-details'))article.querySelector('.ag-result-details').open=true;if(diffOpen&&article.querySelector('.ag-diff'))article.querySelector('.ag-diff').open=true;article.dataset.signature=signature;}
      article.dataset.runState=run.status;article.dataset.runDecision=run.decision;
      const meta=article.querySelector('.ag-run-item-heading+small');if(meta&&!meta.querySelector('.ag-requirements-version'))meta.insertAdjacentHTML('beforeend',`<span class="ag-requirements-version"> · Требования v${Number(run.requirements_version)||0}</span>`);
      if(box.children[i]!==article)box.insertBefore(article,box.children[i]||null);
    }
    Array.from(box.children).forEach(el=>{if(!shown.some(r=>r.id===el.dataset.runId))el.remove();});
    const active=data.runs.some(r=>activeStatuses.includes(r.status));
    box.querySelectorAll('[data-run-action]').forEach(button=>{
      const run=data.runs.find(r=>r.id===button.dataset.run),isRevision=button.dataset.runAction==='revise',isStop=button.dataset.runAction==='stop';
      button.disabled=sending||(isStop?!run.can_stop:isRevision?!data.available||active||!data.tasks.some(t=>t.id===run.task_id&&t.status==='planned'):false);
    });
    box.querySelectorAll('[data-review-comment]').forEach(input=>input.disabled=sending);
  }
  function controls(){
    if(!host||!data)return;
    const active=data.runs.some(r=>activeStatuses.includes(r.status));
    const needsRevision=chosen&&data.runs.find(r=>r.task_id===chosen.id)?.decision==='changes_requested';
    const needsRecovery=chosen&&data.runs.find(r=>r.task_id===chosen.id)?.recovery_candidate;
    const needsContinuation=chosen&&data.runs.find(r=>r.task_id===chosen.id)?.status==='waiting_for_input';
    const currentProfile=data.projects?.find(p=>p.id===selectedProfile?.id),stale=selectedProfile&&currentProfile?.version!==selectedProfile.version;
    $('.ag-run-start').disabled=sending||diagnosticBusy||!data.available||!chosen||!tasks.some(t=>t.id===chosen.id)||active||needsRevision||needsContinuation||Boolean(stale)||Boolean(currentProfile?.archived)||Boolean(needsRecovery);
    $('.ag-run-start').textContent=sending?'Сохраняем…':data.runs.some(r=>r.status==='cancelling')?'Останавливаем агентов…':active?'Агенты работают…':needsRecovery?'Восстановление — в истории ниже':needsRevision?'Доработка — в истории ниже':'Запустить агентов';
    $('.ag-run-select').disabled=sending||!data.available||active;
    if(taskContext)$('.ag-run-select').disabled=true;
    const historyOnly=Boolean(taskContext)&&(!tasks.some(t=>t.id===taskContext)||Boolean(needsRecovery));
    $('.ag-project').hidden=!coding()||!chosen||Boolean(selectedProfile)||historyOnly;
    $('.ag-saved-project').hidden=!chosen||historyOnly;
    $('.ag-profile-select').disabled=sending||active||needsRevision;
    profileOptions();
    $('.ag-saved-summary').textContent=selectedProfile?`${selectedProfile.profile.repo}\n${selectedProfile.profile.files.length} файлов · ${selectedProfile.profile.writable.length} для изменения · ${selectedProfile.profile.commit?'закреплённый коммит '+selectedProfile.profile.commit.slice(0,12):'текущий коммит будет определён при запуске'}`:'Сохраните проект, чтобы использовать его файлы и проверки для новых целей.';
    $('.ag-profile-stale').hidden=!stale&&!currentProfile?.archived;
    $('.ag-profile-stale span').textContent=currentProfile?.archived?'Проект в архиве. Выберите другой или восстановите его в разделе «Проекты».':'Настройки проекта изменились. Проверьте актуальную версию перед запуском.';
    $('.ag-profile-reload').hidden=!stale;
    $('.ag-profile-reload').disabled=sending||active;
    $('.ag-run-form').hidden=historyOnly;$('.ag-run-limit').hidden=historyOnly||!data.available;
    $('.ag-section-heading h2').textContent=historyOnly?'Работа агентов':'Передать цель агентам';
    $('.ag-project').querySelectorAll('input,textarea').forEach(el=>el.disabled=sending||active||!chosen);
    $('.ag-run-limit').textContent='Microsandbox · отдельное Linux-ядро · тесты до и после правок, повтор в чистой microVM · исходный репозиторий не изменяется';
    $('.ag-run-refresh').disabled=sending||!chosen||active;
    $('.ag-readiness .native-caption').textContent=data.diagnostics?'Проверим модели, sandbox и исходные файлы. Перед запуском сервер повторит проверку.':'Для полной диагностики переподключите актуальный встроенный исполнитель из multi-agent-system.';
    $('.ag-run-provider').textContent=data.provider||'Исполнитель не подключён';
    $('.ag-run-hint').textContent=!data.available?data.message:!tasks.length?'Нет целей для запуска. Задайте новую цель.':'Встроенные агенты подготовят план, выполнят задачи и передадут результат на ревью.';
    const preview=$('.ag-run-preview');preview.hidden=!chosen||historyOnly;
    if(chosen){$('.ag-run-task-link').href=`/tasks/${encodeURIComponent(chosen.id)}`;$('.ag-run-task-link').textContent=chosen.title;$('.ag-run-description').textContent=chosen.description||'Описание не задано: агентам будет передано название.';
      const latest=tasks.find(t=>t.id===chosen.id);$('.ag-run-stale').hidden=active||!latest||latest.version===chosen.version;
    }
    if(coding()&&data.available&&tasks.length)$('.ag-run-hint').textContent='Выберите сохранённый проект или задайте разовые настройки. После проверок результат появится здесь для ревью.';
    if(historyOnly)$('.ag-run-hint').textContent='Результаты и история работы над целью. Примите результат или отправьте замечания агентам.';
    diagnosticControls();if(historyOnly)$('.ag-readiness').hidden=true;
    if(historyOnly&&needsRecovery)$('.ag-run-hint').textContent='Восстановление использует сохранённый профиль и снимок цели. Исходный проект недоступен для нового запуска.';
    history();
  }
  function options(){
    const select=$('.ag-run-select');
    if(document.activeElement===select)return;
    const html='<option value="">Выберите цель…</option>'+tasks.map(t=>`<option value="${esc(t.id)}">${esc(t.title)}</option>`).join('');
    if(select.dataset.options!==html){select.innerHTML=html;select.dataset.options=html;select.value=chosen?.id||'';}
  }
  function choose(id){chosen=tasks.find(t=>t.id===id)||null;error('');fillProject();controls();}
  async function poll(){
    if(loading||!host?.isConnected)return;
    loading=true;const target=host,abort=new AbortController();controller=abort;
    const timeout=setTimeout(()=>abort.abort(),10000);
    try{
      const runtime=await request('/api/agent-runtime',{signal:abort.signal});
      if(host!==target)return;
      data=runtime;tasks=runtime.tasks.filter(t=>!t.parent_id&&!t.needs_input&&['planned','in_progress'].includes(t.status));
      if(chosen&&!tasks.some(t=>t.id===chosen.id)){chosen=null;$('.ag-run-select').value='';}
      options();
      if(!chosen){const id=taskContext||new URLSearchParams(location.search).get('task');if(id&&tasks.some(t=>t.id===id)){chosen=tasks.find(t=>t.id===id);$('.ag-run-select').value=id;fillProject();}}
      $('.ag-run-connection').hidden=true;controls();
    }catch(e){if(host===target){$('.ag-run-connection').hidden=false;$('.ag-run-connection').textContent='Не удалось обновить состояние запусков. Повторяем…';}}
    finally{clearTimeout(timeout);if(controller===abort){controller=null;loading=false;}}
  }
  async function start(event){
    event.preventDefault();if(!chosen||sending||$('.ag-run-start').disabled)return;
    let project;
    if(coding()&&!selectedProfile){try{project=readProject();saveProject();}catch(e){error(e instanceof SyntaxError?'Команда тестов: укажите корректный JSON-массив аргументов.':e.message);return;}}
    sending=true;const target=host;controls();error('');
    const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),22000);
    try{
      const result=await request('/api/agent-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:chosen.id,expected_version:chosen.version,...(selectedProfile?{project_id:selectedProfile.id,expected_project_version:selectedProfile.version}:project?{project}:{})}),signal:abort.signal});
      if(host!==target)return;data.runs=[result.run,...data.runs.filter(r=>r.id!==result.run.id)];controls();
    }catch(e){if(host===target){if(e.readiness){diagnosticReport=e.readiness;diagnosticKey=JSON.stringify(diagnosticInput());}error(e.name==='AbortError'?'Ответ о запуске задержался. Проверьте историю ниже перед повторной попыткой.':e.message);}}
    finally{clearTimeout(timeout);if(host===target){sending=false;controls();poll();}}
  }
  async function reviewAction(button){
    if(sending||button.disabled)return;
    const run=data.runs.find(r=>r.id===button.dataset.run),action=button.dataset.runAction,target=host;
    const article=button.closest('[data-run-id]'),notice=article.querySelector('.ag-review-error');notice.hidden=true;
    const comment=drafts.get(run.id)||'';
    if(action==='changes_requested'&&!comment.trim()){notice.textContent='Опишите, что нужно исправить.';notice.hidden=false;article.querySelector('textarea').focus();return;}
    const task=data.tasks.find(t=>t.id===run.task_id);
    const body=action==='stop'?{expected_version:run.version}:action==='revise'?{expected_version:run.version,expected_task_version:task?.version}:{expected_version:run.version,decision:action,comment};
    sending=true;controls();const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),22000);
    try{
      const result=await request(`/api/agent-runs/${encodeURIComponent(run.id)}/${action==='revise'?'revise':action==='stop'?'stop':'review'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:abort.signal});
      if(host!==target)return;
      data.runs=action==='revise'?[result.run,...data.runs]:data.runs.map(r=>r.id===run.id?result.run:r);
      if(['accepted','changes_requested'].includes(action)){drafts.delete(run.id);saveDrafts();}
    }catch(e){if(host===target&&notice.isConnected){notice.textContent=e.name==='AbortError'?'Ответ задержался. Проверьте историю перед повторной попыткой.':e.message;notice.hidden=false;}}
    finally{clearTimeout(timeout);if(host===target){sending=false;controls();poll();}}
  }
  function mount(root,task=null){
    if(host&&taskContext===task){root.querySelector(task?'.task-layout':'.page-heading').after(host);if(restoreFocus?.isConnected)restoreFocus.focus({preventScroll:true});restoreFocus=null;return;}
    unmount();taskContext=task;host=document.createElement('section');host.className='ag-run-panel';host.setAttribute('aria-label','Запуск агентов');
    host.innerHTML='<div class="ag-section-heading"><h2>Передать цель агентам</h2><span class="ag-run-provider">Проверяем исполнителя…</span></div><p class="ag-run-hint">Загружаем задачи…</p><form class="ag-run-form"><label class="form-field">Цель<select class="ag-run-select" aria-label="Цель для агентов" disabled><option value="">Выберите цель…</option></select></label><button type="submit" class="primary ag-run-start" disabled>Запустить агентов</button></form><div class="ag-run-preview" hidden><a class="ag-run-task-link parent-link" data-nav></a><p class="ag-run-description"></p><div class="ag-run-preview-bottom"><small class="ag-run-stale" hidden>Задание изменилось. Обновите его перед запуском.</small><button type="button" class="secondary ag-run-refresh">Обновить задание</button></div></div><p class="ag-run-limit">Выполнение кода — только во встроенном sandbox; результат принимаете вы</p><div class="form-error ag-run-error" role="alert" hidden></div><p class="ag-run-connection" role="status" hidden></p><div class="ag-run-history" aria-label="Последние запуски"></div>';
    $('.ag-run-form').insertAdjacentHTML('afterend',`<details class="ag-project" hidden><summary>Проект и границы выполнения</summary><p class="ag-review-hint">Укажите обычные файлы без секретов. Будут импортированы версии из выбранного коммита; рабочее дерево не передаётся.</p><div class="ag-project-grid"><label class="form-field">Абсолютный путь репозитория<input data-project="repo" aria-label="Путь репозитория" placeholder="/Users/name/project" maxlength="4096" autocomplete="off"></label><label class="form-field">Полный хеш коммита<input data-project="commit" aria-label="Коммит проекта" placeholder="40 или 64 hex-символа" maxlength="64" autocomplete="off"></label><label class="form-field">Импортируемые файлы · по одному на строку<textarea data-project="files" aria-label="Импортируемые файлы" rows="4" placeholder="calculator.py&#10;test_calculator.py"></textarea></label><label class="form-field">Разрешено изменять · по одному на строку<textarea data-project="writable" aria-label="Разрешённые файлы" rows="4" placeholder="calculator.py"></textarea></label><label class="form-field">Команда тестов · JSON-массив<textarea data-project="checks" aria-label="Команда тестов" rows="3"></textarea></label><label class="form-field">Критерии приёмки · по одному на строку<textarea data-project="acceptance" aria-label="Критерии приёмки" rows="3" placeholder="Сохранён публичный API"></textarea></label></div></details>`);
    $('.ag-run-form').insertAdjacentHTML('afterend','<section class="ag-saved-project" hidden><label class="form-field">Проект для запуска<select class="ag-profile-select" aria-label="Проект для запуска"><option value="">Разовые настройки</option></select></label><p class="ag-saved-summary"></p><div class="ag-profile-stale" role="status" hidden><span></span> <button type="button" class="secondary ag-profile-reload">Обновить настройки</button></div><div class="native-actions"><a class="parent-link" href="/projects" data-nav>Управлять проектами ↗</a></div></section>');
    $('.ag-run-history').insertAdjacentHTML('beforebegin','<section class="ag-readiness" hidden><div class="ag-readiness-heading"><h3>Готовность к запуску</h3><button type="button" class="secondary ag-readiness-check">Проверить готовность</button></div><p class="native-caption">Проверим модели, sandbox и исходные файлы. Перед запуском сервер повторит проверку.</p><p class="form-error ag-readiness-error" role="alert" hidden></p><div class="ag-readiness-report"></div></section>');
    $('.ag-readiness-check').addEventListener('click',diagnose);
    fillProject();
    root.querySelector(task?'.task-layout':'.page-heading').after(host);host.querySelector('form').addEventListener('submit',start);
    $('.ag-run-select').addEventListener('change',e=>choose(e.target.value));
    $('.ag-run-refresh').addEventListener('click',()=>{choose(chosen?.id);options();});
    $('.ag-profile-select').addEventListener('change',event=>{selectedProfile=data.projects.find(p=>p.id===event.target.value)||null;selections.set(chosen.id,selectedProfile?.id||'');controls();});
    $('.ag-profile-reload').addEventListener('click',()=>{selectedProfile=data.projects.find(p=>p.id===selectedProfile?.id)||null;controls();});
    host.addEventListener('input',event=>{if(event.target.matches('[data-project]'))saveProject();if(event.target.matches('[data-review-comment]')){drafts.set(event.target.dataset.reviewComment,event.target.value);saveDrafts();}});
    host.addEventListener('toggle',event=>{if(event.target.matches('.ag-diff'))loadDiff(event.target);},true);
    host.addEventListener('click',event=>{const button=event.target.closest('[data-run-action]');if(button)reviewAction(button);});
    poll();timer=setInterval(poll,1500);
  }
  function unmount(){clearInterval(timer);controller?.abort();controller=null;loading=false;sending=false;host?.remove();host=null;data=null;chosen=null;tasks=[];taskContext=null;selectedProfile=null;diagnosticReport=null;diagnosticBusy=false;diagnosticError='';diagnosticKey=null;}
  function beforeRender(path){if(!host||taskContext===null)return;if(path===`/tasks/${taskContext}`){restoreFocus=host.contains(document.activeElement)?document.activeElement:null;host.remove();}else unmount();}
  window.agentRuns={mount,unmount,beforeRender};
})();
