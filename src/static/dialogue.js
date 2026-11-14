/* Durable coordinator questions; only the user submits answers or continuations. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const drafts=new Map();let host=null,taskId=null,data=null,timer=null,loading=false,busy=false,notice='',failed=false,signature='',focus=null;
  try{for(const [id,value] of JSON.parse(sessionStorage.getItem('kaibain.answer-drafts')||'[]'))if(typeof value==='string')drafts.set(id,value.slice(0,2000));}catch(e){}
  function saveDrafts(){try{sessionStorage.setItem('kaibain.answer-drafts',JSON.stringify([...drafts].slice(-20)));}catch(e){}}
  async function request(url,body){
    const response=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(24000)});
    const value=await response.json();if(!response.ok||!value.ok)throw new Error(value.error?.message||'Не удалось обновить диалог.');return value;
  }
  function render(){
    if(!host?.isConnected||busy)return;
    const next=JSON.stringify([data,notice,failed]);if(next===signature)return;signature=next;
    const input=host.querySelector('textarea:focus'),restore=input?{id:input.dataset.answerDraft,start:input.selectionStart,end:input.selectionEnd}:null;
    host.innerHTML=`<div class="panel-heading"><div><div class="native-kicker">ВЫ И КООРДИНАТОР</div><h2>Обсуждение цели</h2></div><span class="native-caption">Требования · v${data?.requirements_version||0}</span></div>${!data?'<p class="native-copy">Загружаем обсуждение…</p>':data.questions.length?`<div class="dialogue-thread">${data.questions.map(q=>`<article class="dialogue-question" data-question-id="${esc(q.id)}"><div class="dialogue-author"><strong>${esc(q.agent_name)}</strong><small>${esc(new Date(q.created_at).toLocaleString('ru-RU'))}</small></div><p class="dialogue-text">${esc(q.question)}</p>${q.status==='answered'?`<div class="dialogue-answer"><div class="dialogue-author"><strong>Вы</strong><small>${esc(new Date(q.answered_at).toLocaleString('ru-RU'))}</small></div><p class="dialogue-text">${esc(q.answer)}</p></div>`:q.status==='cancelled'?'<p class="native-caption">Вопрос закрыт после остановки или ошибки прогона.</p>':q.can_answer?`<form data-answer-question="${esc(q.id)}"><label class="form-field">Ваш ответ<textarea aria-label="Ваш ответ" data-answer-draft="${esc(q.id)}" rows="3" required maxlength="2000" placeholder="Уточните ожидаемый результат…">${esc(drafts.get(q.id)||'')}</textarea></label><p class="native-caption">Ответ сохранится в истории и станет частью требований к результату.</p><button class="primary" type="submit">${q.run_id?'Ответить и продолжить':'Отправить ответ'}</button></form>`:'<p class="native-copy">Исполнитель завершает текущую попытку. Ответ станет доступен после освобождения ресурсов.</p>'}</article>`).join('')}</div>`:'<p class="native-copy">Если координатору понадобятся уточнения, вопрос появится здесь. Все ответы сохраняются вместе с целью.</p>'}${data?.continuation?'<div class="dialogue-continuation"><p class="native-copy">Ответ сохранён. Можно продолжить с исходным коммитом и настройками проекта.</p><button class="primary" type="button" data-continue-dialogue>Продолжить работу</button></div>':''}<p class="dialogue-notice ${failed?'form-error':'native-copy'}" role="status" ${notice?'':'hidden'}>${esc(notice)}</p>`;
    if(restore){const field=host.querySelector(`[data-answer-draft="${CSS.escape(restore.id)}"]`);if(field){field.focus({preventScroll:true});field.setSelectionRange(restore.start,restore.end);}}
  }
  async function poll(){
    if(loading||busy||!host?.isConnected)return;loading=true;const target=host;
    try{const value=await request('/api/goals/'+taskId+'/dialogue');if(host===target){data=value;render();}}
    catch(error){if(host===target){notice=error.message;failed=true;render();}}
    finally{if(host===target)loading=false;}
  }
  async function submit(form){
    if(busy||!data)return;const question=data.questions.find(q=>q.id===form.dataset.answerQuestion);if(!question)return;
    const target=host;busy=true;form.querySelector('button').disabled=true;form.querySelector('textarea').disabled=true;notice='';failed=false;
    try{
      const result=await request('/api/goals/'+taskId+'/answers',{question_id:question.id,expected_version:question.version,
        expected_task_version:data.task_version,answer:drafts.get(question.id)||'',continue_run:Boolean(question.run_id)});
      if(host!==target)return;drafts.delete(question.id);saveDrafts();data=result.dialogue;
      notice=result.continuation_error?'Ответ сохранён. Не удалось продолжить: '+result.continuation_error.message:result.run?'Ответ сохранён. Агенты продолжают работу.':'Ответ сохранён и доступен координатору.';
      failed=Boolean(result.continuation_error);if(result.run)data.continuation=null;
    }catch(error){if(host===target){notice=error.message;failed=true;}}
    finally{if(host===target){busy=false;signature='';render();poll();}}
  }
  async function resume(){
    const run=data?.continuation;if(busy||!run)return;busy=true;const target=host;
    host.querySelector('[data-continue-dialogue]').disabled=true;
    try{await request('/api/agent-runs/'+run.id+'/continue',{expected_version:run.version,expected_task_version:data.task_version});if(host===target){data.continuation=null;notice='Продолжение запущено.';failed=false;}}
    catch(error){if(host===target){notice=error.message;failed=true;}}
    finally{if(host===target){busy=false;signature='';render();poll();}}
  }
  function mount(root,id){
    if(host&&taskId===id){root.querySelector('.task-layout>div>section').after(host);if(focus?.isConnected)focus.focus({preventScroll:true});focus=null;return;}
    unmount();taskId=id;host=document.createElement('section');host.className='panel goal-dialogue';root.querySelector('.task-layout>div>section').after(host);
    host.addEventListener('input',event=>{if(event.target.dataset.answerDraft){drafts.set(event.target.dataset.answerDraft,event.target.value);saveDrafts();}});
    host.addEventListener('submit',event=>{if(event.target.dataset.answerQuestion){event.preventDefault();submit(event.target);}});
    host.addEventListener('click',event=>{if(event.target.closest('[data-continue-dialogue]'))resume();});
    render();poll();timer=setInterval(poll,1500);
  }
  function unmount(){clearInterval(timer);host?.remove();host=null;taskId=null;data=null;loading=false;busy=false;notice='';failed=false;signature='';focus=null;}
  function beforeRender(path){if(!host)return;if(path==='/tasks/'+taskId){focus=host.contains(document.activeElement)?document.activeElement:null;host.remove();}else unmount();}
  window.goalDialogue={mount,beforeRender};
})();
