/* Read-only environment checks. Results describe prerequisites at the shown time. */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const globalState={report:null,busy:false,error:''};
  function html(report){
    if(!report)return '';
    return `<div class="readiness-result ${report.ready?'is-ready':'is-blocked'}" role="status"><div class="readiness-heading"><strong>${report.ready?'Окружение готово':'Требуется настройка'}</strong><small>${esc(new Date(report.checked_at).toLocaleTimeString('ru-RU'))}</small></div><ul>${report.checks.map(row=>`<li class="readiness-${esc(row.status)}" data-readiness-check="${esc(row.id)}"><span class="readiness-icon" aria-hidden="true">${row.status==='passed'?'✓':row.status==='failed'?'!':'○'}</span><div><strong>${esc(row.label)}</strong><p>${esc(row.message)}</p>${row.hint?`<p class="readiness-hint">${esc(row.hint)}</p>`:''}${row.details?.model?`<code>${esc(row.details.model)}</code>`:''}${row.details?.endpoint?`<code>${esc(row.details.endpoint)}</code>`:''}${row.details?.commit?`<code>${esc(row.details.commit.slice(0,12))}</code>`:''}${row.details?.reason||row.details?.probe?`<details><summary>Подробности</summary><pre>${esc(row.details.reason||row.details.probe)}</pre></details>`:''}</div></li>`).join('')}</ul><p class="native-caption">Проверено наличие компонентов и доступ к данным. Запуск модели, создание VM и выполнение тестов проверяются в самом прогоне.</p></div>`;
  }
  async function check(body={}){
    const response=await fetch('/api/agent-readiness',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(22000)});
    const value=await response.json();if(!response.ok||!value.ok)throw new Error(value.error?.message||'Не удалось проверить окружение.');return value;
  }
  function mount(container){
    function render(){
      container.innerHTML=`<div class="native-kicker">ГОТОВНОСТЬ К РАБОТЕ</div><h2>Окружение агентов</h2><p class="native-copy">Проверим сервер Ollama, выбранные модели и локальный sandbox. Модели могут работать на отдельном PC; sandbox выполняется на устройстве с Kaibain.</p><button class="secondary" data-readiness-global ${globalState.busy?'disabled':''}>${globalState.busy?'Проверяем…':'Проверить окружение'}</button><p class="form-error" role="alert" ${globalState.error?'':'hidden'}>${esc(globalState.error)}</p>${html(globalState.report)}`;
      container.querySelector('button').addEventListener('click',async()=>{
        globalState.busy=true;globalState.error='';render();
        try{globalState.report=await check();}catch(error){globalState.report=null;globalState.error=error.message;}
        finally{globalState.busy=false;if(container.isConnected)render();}
      });
    }
    render();
  }
  window.readinessUI={html,check,mount};
})();
