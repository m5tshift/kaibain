(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let host=null,id=null,data=null,busy=false,loading=false,revision=null,notice='',failed=false;
  const $=s=>host?.querySelector(s);
  const key=()=>`kaibain.result-review.${id}`;
  function draft(){try{return sessionStorage.getItem(key())||'';}catch(e){return '';}}
  function save(text){try{sessionStorage.setItem(key(),text.slice(0,2000));}catch(e){}}
  async function request(path,body){
    const response=await fetch(`/api/agent-runs/${encodeURIComponent(id)}/${path}`,{signal:AbortSignal.timeout(60000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error?.message||'Запрос не выполнен.');return result;
  }
  function checks(){
    const evidence=data.evidence;
    if(!evidence)return `<ul>${(data.run.result?.checks||[]).map(c=>`<li>${esc(c)}</li>`).join('')}</ul><p class="native-caption">Сохранённые сообщения исполнителя. Подробный протокол с контрольной суммой для этого прогона недоступен.</p>`;
    return [['Исходные тесты',evidence.baseline],['После изменений',evidence.worker_checks],['В чистой microVM',evidence.clean_check?.checks]].map(([label,result])=>`<details class="review-check"><summary><span>${label}</span><strong class="${result?.exit_code===0?'review-pass':'review-fail'}">${result?.exit_code===0?'Пройдены':result?'Exit '+esc(result.exit_code):'Нет данных'}</strong></summary><pre>${esc(result?JSON.stringify(result,null,2):'Протокол отсутствует')}</pre></details>`).join('');
  }
  function applyPanel(){
    const r=data.receipt;
    if(r)return `<section class="panel review-published"><p class="native-kicker">ИЗМЕНЕНИЯ СОХРАНЕНЫ</p><h2>Ветка готова</h2><dl class="review-meta"><dt>Ветка</dt><dd>${esc(r.branch)}</dd><dt>Коммит</dt><dd>${esc(r.commit)}</dd><dt>Репозиторий</dt><dd>${esc(r.repo)}</dd></dl><p class="native-copy">Текущая ветка и рабочие файлы сохранены. Чтобы посмотреть результат, откройте созданную ветку в своём Git-клиенте.</p></section>`;
    if(data.run.decision!=='accepted')return '';
    return `<section class="panel review-apply"><p class="native-kicker">ОТДЕЛЬНОЕ ДЕЙСТВИЕ</p><h2>Применить результат</h2><p class="native-copy">Создадим ветку с отдельным коммитом из принятых файлов. Текущая ветка, индекс и ваши рабочие файлы сохранятся.</p><dl class="review-meta"><dt>Новая ветка</dt><dd>${esc(data.branch||'Недоступна')}</dd><dt>Репозиторий</dt><dd>${esc(data.run.project?.repo)}</dd><dt>Исходный коммит</dt><dd>${esc(data.source_commit||data.run.project?.commit)}</dd></dl>${data.conflicts.length?`<div class="review-conflicts" role="status"><h3>Нужно устранить конфликты</h3><ul>${data.conflicts.map(c=>`<li>${esc(c.message)}</li>`).join('')}</ul><p>Сохраните правки самостоятельно и обновите проверку. Автоматическое объединение изменений не выполняется.</p></div>`:''}${data.apply_error?`<p class="form-error">${esc(data.apply_error.message)}</p>`:''}<button type="button" class="primary" data-review-action="apply" ${!data.can_apply||busy?'disabled':''}>Применить в новую ветку</button></section>`;
  }
  function render(){
    if(!host||!data)return;
    const focused=$('textarea')===document.activeElement,selection=focused?[$('textarea').selectionStart,$('textarea').selectionEnd]:null;
    const opened=new Set([...host.querySelectorAll('details[open][data-file]')].map(d=>d.dataset.file));
    const run=data.run,pending=run.decision==='pending'&&run.status==='completed'&&data.latest;
    const labels={pending:'Ожидает решения',accepted:'Принято',changes_requested:'На доработке'};
    host.innerHTML=`<a class="parent-link" data-nav href="/tasks/${encodeURIComponent(run.task_id)}">← К цели</a><div class="page-heading review-heading"><div><p class="eyebrow">РЕЗУЛЬТАТ РАБОТЫ · ТРЕБОВАНИЯ V${Number(run.requirements_version)||0}</p><h1>${esc(run.task_title)}</h1><p>${esc(run.provider)} · ${esc(new Date(run.created_at).toLocaleString('ru-RU'))}</p></div><span class="badge">${labels[run.decision]||esc(run.status)}</span></div><div class="review-toolbar"><span>${data.integrity?'Целостность экспорта подтверждена':'Экспорт недоступен для применения'}</span><button class="secondary" type="button" data-review-action="refresh" ${busy?'disabled':''}>Обновить проверку</button></div><p class="review-notice ${failed?'form-error':''}" role="status" ${!notice?'hidden':''}>${esc(notice)}</p>${data.integrity_error?`<p class="form-error review-integrity" role="alert">${esc(data.integrity_error)}</p>`:''}<div class="review-grid"><div class="review-main"><section class="panel"><p class="native-kicker">ЧТО СДЕЛАНО</p><h2>Результат исполнителя</h2><p class="review-summary">${esc(run.result?.summary||'Результат ещё не сохранён.')}</p><h3>Критерии приёмки</h3><ul class="review-criteria">${(run.project?.acceptance||[]).map(c=>`<li><span class="review-criterion-dot">○</span><span>${esc(c)}</span></li>`).join('')}</ul><p class="native-caption">Соответствие каждого критерия оцениваете вы по результату, тестам и изменениям.</p></section><section class="panel"><h2>Проверки</h2>${checks()}<p class="native-caption">Повтор тестов в чистой microVM проверяет воспроизводимость. Независимого LLM-ревью в этом прогоне нет.</p></section>${run.independent_review?`<section class="panel">${window.runRecovery.assessmentHTML(run.independent_review)}</section>`:''}<section class="panel review-files"><div class="review-files-heading"><h2>Изменения по файлам</h2><span class="native-count">${data.files.length}</span></div>${data.files.map(f=>`<details data-file="${esc(f.path)}" ${opened.has(f.path)||data.files.length===1?'open':''}><summary><span>${esc(f.path)}</span><small>${f.kind==='added'?'Новый':'Изменён'} · ${f.bytes} B</small></summary><pre class="review-diff">${f.diff.split('\n').map(line=>`<span class="${line.startsWith('+')&&!line.startsWith('+++')?'diff-add':line.startsWith('-')&&!line.startsWith('---')?'diff-remove':line.startsWith('@@')?'diff-hunk':''}">${esc(line)}\n</span>`).join('')}</pre>${f.truncated?'<p class="native-caption">Предпросмотр сокращён; полный diff доступен в артефактах.</p>':''}</details>`).join('')||'<p class="native-copy">Нет доступных изменений.</p>'}</section></div><aside class="review-side">${pending?`<section class="panel review-decision"><p class="native-kicker">ВАШЕ РЕШЕНИЕ</p><h2>Приёмка результата</h2><p class="native-copy">Принятие завершит цель и подзадачу исполнителя. Применение кода будет отдельным действием.</p><label class="form-field">Комментарий к результату<textarea aria-label="Комментарий к результату" maxlength="2000" rows="5" ${busy?'disabled':''} placeholder="Что нужно исправить или уточнить?">${esc(draft())}</textarea></label><div class="review-actions"><button type="button" class="primary" data-review-action="accepted" ${busy?'disabled':''}>Принять результат</button><button type="button" class="secondary" data-review-action="changes_requested" ${busy?'disabled':''}>Вернуть на доработку</button></div></section>`:`<section class="panel"><h2>${labels[run.decision]||'История результата'}</h2>${run.reviewed_at?`<p class="native-caption">${esc(new Date(run.reviewed_at).toLocaleString('ru-RU'))}</p>`:''}<p class="review-summary">${esc(run.review_comment)}</p>${run.decision==='changes_requested'&&data.latest?`<p class="native-copy">Новая попытка получит сохранённые файлы, исходный проект и ваши замечания.</p><button type="button" class="primary" data-review-action="revise" ${busy?'disabled':''}>Запустить доработку</button>`:''}${!data.latest?'<p class="native-copy">У цели есть более новая попытка. Этот результат доступен как история.</p>':''}</section>`}${applyPanel()}<section class="panel review-artifacts"><h2>Материалы</h2><p class="native-caption">Исходный коммит · <code>${esc(run.project?.commit?.slice(0,12))}</code></p>${['report','diff','manifest','log','dialogue'].map((name,i)=>`<a target="_blank" rel="noopener" href="/api/agent-runs/${encodeURIComponent(id)}/artifacts/${name}">${['Отчёт исполнителя','Полный diff','Хеши файлов','Журнал выполнения','Контекст обсуждения'][i]} ↗</a>`).join('')}</section></aside></div>`;
    if(focused&&$('textarea')){$('textarea').focus({preventScroll:true});$('textarea').setSelectionRange(...selection);}
  }
  async function load(){
    if(loading||!host)return;loading=true;const target=host;
    host.querySelectorAll('[data-review-action]').forEach(button=>button.disabled=true);
    try{const result=await request('review');if(host===target){data=result;render();}}
    catch(e){if(host===target){notice=e.message;failed=true;if(data)render();else host.innerHTML=`<section class="panel"><h1>Результат недоступен</h1><p class="form-error" role="alert">${esc(e.message)}</p><button class="secondary" data-review-action="refresh">Повторить</button></section>`;}}
    finally{if(host===target)loading=false;}
  }
  async function action(name){
    if(busy||loading)return;if(name==='refresh'){notice='';failed=false;await load();return;}if(!data)return;
    const target=host,snapshot=data.run;busy=true;notice='';failed=false;render();
    try{
      if(name==='apply'){
        const result=await request('apply',{expected_version:snapshot.version,preview_token:data.token});
        if(host!==target)return;data.receipt=result.receipt;notice='Изменения сохранены в новой ветке.';
      }else if(name==='revise'){
        const response=await fetch(`/api/tasks/${encodeURIComponent(snapshot.task_id)}`),current=await response.json();
        if(host!==target)return;
        if(!response.ok)throw new Error(current.error?.message||'Цель недоступна.');
        await request('revise',{expected_version:snapshot.version,expected_task_version:current.task.version});
        if(host!==target)return;notice='Доработка запущена. Её ход доступен на странице цели.';
      }else{
        await request('review',{expected_version:snapshot.version,decision:name,comment:draft()});
        if(host!==target)return;save('');notice=name==='accepted'?'Результат принят. Теперь можно отдельно применить файлы.':'Замечания сохранены. Можно запустить доработку.';
      }
      await load();
    }catch(e){if(host===target){notice=e.message;failed=true;render();}}
    finally{if(host===target){busy=false;render();}}
  }
  function mount(root,runId,currentRevision){
    if(host&&id===runId){if(!host.isConnected)root.replaceChildren(host);if(revision!==currentRevision&&!busy){revision=currentRevision;load();}return;}
    unmount();id=runId;revision=currentRevision;host=document.createElement('section');host.className='result-review';root.replaceChildren(host);
    host.innerHTML='<div class="loading"><span class="spinner"></span> Собираем результат и проверяем файлы…</div>';
    host.addEventListener('input',e=>{if(e.target.matches('textarea'))save(e.target.value);});
    host.addEventListener('click',e=>{const button=e.target.closest('[data-review-action]');if(button)action(button.dataset.reviewAction);});load();
  }
  function unmount(){host?.remove();host=null;id=null;data=null;busy=false;loading=false;notice='';failed=false;revision=null;}
  window.resultReview={mount,unmount};
})();
