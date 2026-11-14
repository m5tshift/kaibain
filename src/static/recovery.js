(() => {
 'use strict';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const labels={review:'??????????? ?????: ????? ?????????',stopped:'Остановлено пользователем',model:'Ошибка модели',tests:'Проверки не пройдены',environment:'Проблема окружения',budget:'Исчерпан лимит',conflict:'Изменились данные',execution:'Ошибка выполнения'};
 const previews=new Map(),pending=new Set(),cleanupUncertain=new Set();
 function cleanupContent(value){
  const statuses={running:'Очистка выполняется',completed:'Очистка завершена',failed:'Очистка не выполнена',interrupted:'Очистка прервана'};
  return `<p class="${['failed','interrupted'].includes(value?.status)?'form-error':''}">${esc(value?statuses[value.status]||value.status:'Сохранённых попыток очистки нет.')}</p>${value?.error?`<p class="form-error">${esc(value.error)}</p>`:''}${Number.isInteger(value?.receipt?.records_checked)?`<p>Проверено записей владения: ${esc(value.receipt.records_checked)}${value.receipt.time?` · ${esc(new Date(value.receipt.time*1000).toLocaleString('ru-RU'))}`:''}</p>`:''}`;
 }
 function updateControls(section){
  const id=section.dataset.recoveryRun,blocked=['running','completed'].includes(section.dataset.cleanupStatus)||cleanupUncertain.has(id);
  section.querySelectorAll('button').forEach(button=>button.disabled=pending.has(id)||(button.dataset.recoveryAction==='cleanup'&&blocked)||(button.dataset.recoveryAction==='resume'&&(section.dataset.cleanupStatus==='running'||cleanupUncertain.has(id))));
  section.querySelector('.ag-cleanup-uncertain').hidden=!cleanupUncertain.has(id);
 }
 function content(value,disabled=false){
  const c=value.checkpoint;
  return `${value.error?`<p class="form-error" role="alert">${esc(value.error.message)}</p>`:''}${c?`<p class="ag-checkpoint-title">Checkpoint ${String(c.sequence).padStart(3,'0')} · ${esc(new Date(c.created_at).toLocaleString('ru-RU'))}</p><ul class="ag-checkpoint-files">${value.files.map(f=>`<li>${esc(f.path)} <small>${f.bytes} B</small></li>`).join('')}</ul>`:''}${value.recoverable?`<p>Снимок целый и соответствует версиям задач. Это промежуточные файлы: тесты будут выполнены заново в новой microVM, включая чистый повтор.</p><p>Продолжение использует ту же подзадачу и исходный профиль. Неэкспортированные правки потеряны. Новая попытка получит прежние лимиты времени и токенов; её расход сохранится отдельно.</p><button type="button" class="primary" data-recovery-action="resume" ${disabled?'disabled':''}>Продолжить из checkpoint</button>`:''}`;
 }
 function assessmentHTML(value){
  if(!value)return '';
  return `<section class="ag-model-assessment"><h3>??????????? ?????</h3><p>${esc(value.identities.validator.name)} ? ${esc(value.identities.validator.family_group)} ? ${value.decision==='accept'?'????????? ?? ???????':'????? ?????????'}</p><p>${esc(value.summary)}</p><ul>${value.findings.map(f=>`<li><strong>${esc(f.path)}:${f.line}</strong><p>${esc(f.message)}</p><code>${esc(f.evidence)}</code></li>`).join('')}</ul><p>????? ?????? ????? ????????? ??????. ????????? ??????? ?????????? ??.</p></section>`;
 }
 function html(run){
  const assessment=assessmentHTML(run.independent_review);
  const lineage=run.recovery_of?`<p class="ag-recovery-lineage">Восстановление попытки ${esc(run.recovery_of.slice(0,8))}</p>`:'';
  if(!run.project||!['failed','interrupted','cancelled'].includes(run.status))return lineage+assessment;
  const cached=previews.get(run.id),value=cached?.version===run.version?cached.value:null;
  const busy=pending.has(run.id),uncertain=cleanupUncertain.has(run.id),cleanupBlocked=['running','completed'].includes(run.cleanup?.status)||uncertain;
  const preview=value?content(value,busy||run.cleanup?.status==='running'||uncertain):'';
  return `${lineage}${assessment}<section class="ag-recovery" data-recovery-run="${esc(run.id)}" data-run-version="${esc(run.version)}" data-cleanup-status="${esc(run.cleanup?.status||'')}"><h3>${labels[run.failure_kind]||'Попытка завершилась'}</h3><p>Можно проверить последний полностью сохранённый снимок. Работа возобновляется только по вашему действию.</p><button type="button" class="secondary" data-recovery-action="preview" ${busy?'disabled':''}>Проверить восстановление</button><div class="ag-recovery-preview">${preview}</div><div class="ag-cleanup"><h3>Очистка среды</h3><p>Исходный исполнитель очистит только принадлежащие этой попытке ресурсы. Очистка доступна и без checkpoint, не запускает и не возобновляет цель. Сохранённые артефакты останутся.</p><div class="ag-cleanup-state" role="status">${cleanupContent(run.cleanup)}</div><p class="ag-cleanup-uncertain form-error" ${uncertain?'':'hidden'}>Ответ об очистке не подтверждён. Перед повтором нажмите «Проверить состояние очистки» и проверьте сохранённый результат.</p><div class="ag-cleanup-actions"><button type="button" class="secondary" data-recovery-action="cleanup" ${busy||cleanupBlocked?'disabled':''}>Очистить среду</button><button type="button" class="secondary" data-recovery-action="cleanup-refresh" ${busy?'disabled':''}>Проверить состояние очистки</button></div></div><p class="ag-recovery-message" role="status" hidden></p></section>`;
 }
 async function request(id,path,body){
  const response=await fetch(`/api/agent-runs/${encodeURIComponent(id)}/${path}`,{signal:AbortSignal.timeout(60000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const value=await response.json();if(!response.ok||!value.ok)throw new Error(value.error?.message||'Операция не выполнена.');return value;
 }
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-recovery-action]');if(!button||button.disabled)return;
  let section=button.closest('[data-recovery-run]');const id=section.dataset.recoveryRun,action=button.dataset.recoveryAction;if(pending.has(id))return;
  if((action==='cleanup'&&(['running','completed'].includes(section.dataset.cleanupStatus)||cleanupUncertain.has(id)))||(action==='resume'&&(section.dataset.cleanupStatus==='running'||cleanupUncertain.has(id))))return;
  const currentSection=()=>Array.from(document.querySelectorAll('[data-recovery-run]')).find(s=>s.dataset.recoveryRun===id)||section;
  pending.add(id);updateControls(section);let message=section.querySelector('.ag-recovery-message');message.hidden=false;message.classList.remove('form-error');message.textContent=action==='preview'?'Проверяем сохранённый снимок…':action==='cleanup'?'Очищаем ресурсы прежней попытки…':action==='cleanup-refresh'?'Проверяем сохранённое состояние очистки…':'Очищаем прежнюю среду и проверяем готовность новой…';
  try{
   if(action==='cleanup'||action==='cleanup-refresh'){
    if(action==='cleanup')cleanupUncertain.add(id);
    const value=await request(id,'cleanup',action==='cleanup'?{expected_version:Number(section.dataset.runVersion)}:undefined);
    cleanupUncertain.delete(id);section=currentSection();
    if(section.isConnected){if(value.run_version!==undefined)section.dataset.runVersion=String(value.run_version);section.dataset.cleanupStatus=value.cleanup?.status||'';section.querySelector('.ag-cleanup-state').innerHTML=cleanupContent(value.cleanup);section.querySelector('.ag-recovery-message').hidden=true;}
   }else if(action==='preview'){
    const value=await request(id,'recovery');previews.set(id,{version:value.run_version,value});while(previews.size>30)previews.delete(previews.keys().next().value);
    section=currentSection();message=section.querySelector('.ag-recovery-message');
    if(section.isConnected){section.querySelector('.ag-recovery-preview').innerHTML=content(value);message.hidden=true;}
   }else{
    const value=previews.get(id)?.value;if(!value?.recoverable)throw new Error('Обновите предварительную проверку.');
    await request(id,'resume',{expected_version:value.run_version,expected_task_version:value.task_version,checkpoint_id:value.checkpoint.id});
    section=currentSection();message=section.querySelector('.ag-recovery-message');
    if(section.isConnected){message.textContent='Новая попытка создана. Следите за её ходом в истории и на графе.';section.querySelector('.ag-recovery-preview').innerHTML='';}
    previews.delete(id);
   }
  }catch(error){section=currentSection();if(section.isConnected){message=section.querySelector('.ag-recovery-message');message.hidden=false;message.textContent=error.name==='TimeoutError'?(action.startsWith('cleanup')?'Ответ задержался. Нажмите «Проверить состояние очистки» и проверьте сохранённый результат перед повтором.':'Ответ задержался. Проверьте историю запусков перед повторным действием.'):error.message;message.classList.add('form-error');}}
  finally{pending.delete(id);section=currentSection();if(section.isConnected)updateControls(section);}
 });
 window.runRecovery={html,assessmentHTML};
})();
