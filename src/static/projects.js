/* Persistent, user-owned project profiles. No runtime executable comes from this form. */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const checks=new Map(),reports=new Map(),checking=new Set();let context=null,showArchived=false,busy=false;
  const active=()=>context.projects.filter(p=>!p.archived);
  function mount(root,options){
    context={...options,root};
    const shown=context.projects.filter(p=>showArchived||!p.archived);
    root.innerHTML=`<div class="page-heading"><div><div class="eyebrow">ПРОЕКТЫ / СОХРАНЁННЫЕ НАСТРОЙКИ</div><h1>Место для вашей следующей цели.</h1><p class="subtitle">Настройте проект один раз. Агенты получат выбранные файлы и проверки при каждом запуске.</p></div><button class="primary" data-new-project>+ Новый проект</button></div><div class="projects-intro"><div><span class="native-kicker">ВАШ РАБОЧИЙ ПРОЦЕСС</span><p>Выберите проект → задайте цель → запустите агентов.</p></div><span class="projects-total">${active().length} активных</span></div><div class="projects-toolbar"><h2>Ваши проекты <span class="muted">${shown.length}</span></h2><button class="secondary" data-project-archives aria-pressed="${showArchived}">${showArchived?'Скрыть архив':'Показать архив'}</button></div><div class="projects-grid">${shown.map(projectCard).join('')||'<div class="native-empty"><span aria-hidden="true">▣</span><h2>Сохраните первый проект</h2><p>Укажите репозиторий, файлы и тесты. Затем выбирайте этот проект для новых целей.</p><button class="primary" data-new-project>Создать проект</button></div>'}</div><p class="native-caption">Выполнение — в microVM. Запуск закрепляет коммит и копию настроек; история прогонов сохраняется при редактировании и архивировании проекта. Модели задаются в конфигурации встроенного исполнителя.</p>`;
    root.querySelectorAll('[data-project-card]').forEach(card=>{
      const project=context.projects.find(p=>p.id===card.dataset.projectCard),entry=reports.get(project.id),result=document.createElement('div');result.className='project-diagnostics';
      if(entry?.version===project.version)result.innerHTML=entry.error?`<p class="form-error" role="alert">${esc(entry.error)}</p>`:window.readinessUI.html(entry.report);
      card.querySelector('.project-actions').after(result);
      const button=document.createElement('button');button.className='secondary';button.dataset.checkReadiness=project.id;button.textContent=checking.has(project.id)?'Проверяем…':'Проверить готовность';button.disabled=project.archived||checking.has(project.id);card.querySelector('.project-actions').prepend(button);
    });
  }
  function projectCard(p){
    const result=checks.get(p.id),current=result?.version===p.version;
    return `<article class="panel project-card ${p.archived?'is-archived':''}" data-project-card="${esc(p.id)}"><div class="project-card-top"><span class="project-mark" aria-hidden="true">▣</span><span class="native-caption">${p.archived?'В архиве':'ПРОЕКТ'} · v${p.version}</span></div><h2><button type="button" data-edit-project="${esc(p.id)}">${esc(p.name)}</button></h2><p class="native-copy project-description">${esc(p.description||'Готовые настройки для работы агентов.')}</p><p class="project-repo">${esc(p.profile.repo)}</p><div class="project-facts"><span>${p.profile.files.length} файлов для чтения</span><span>${p.profile.writable.length} для изменения</span><span>${p.profile.commit?'Коммит '+esc(p.profile.commit.slice(0,12)):'Текущий коммит при запуске'}</span></div><p class="project-check-result ${current&&result.error?'error':''}" role="status">${current?esc(result.message):'Git проверяется перед запуском. Изменения рабочего дерева не импортируются.'}</p><div class="project-actions"><button class="primary" data-project-goal="${esc(p.id)}" ${p.archived?'disabled':''}>Новая цель ↗</button><button class="secondary" data-edit-project="${esc(p.id)}">Настроить</button><button class="secondary" data-check-project="${esc(p.id)}" ${p.archived?'disabled':''}>Проверить Git</button><button class="project-archive" data-archive-project="${esc(p.id)}">${p.archived?'Восстановить':'В архив'}</button></div></article>`;
  }
  function open(id=null){
    const project=id?context.projects.find(p=>p.id===id):null,profile=project?.profile||{};
    const modal=document.querySelector('#modal');
    modal.innerHTML=`<div class="modal-heading"><h2 id="modal-title">${project?'Настройки проекта':'Новый проект'}</h2><button type="button" class="icon-button" data-close-project aria-label="Закрыть">×</button></div><p class="dialog-copy">Эти настройки сохранятся для новых целей. Разрешённые изменения выполняются в sandbox.</p><form id="project-form"><label class="form-field">Название проекта<input name="name" required maxlength="120" value="${esc(project?.name||'')}" placeholder="Например, Мой сервис"></label><label class="form-field">Описание проекта<textarea name="description" rows="2" maxlength="2000" placeholder="Над чем будут работать агенты?">${esc(project?.description||'')}</textarea></label><label class="form-field">Путь репозитория<input name="repo" required maxlength="4096" value="${esc(profile.repo||'')}" placeholder="/Users/name/project" autocomplete="off"></label><div class="form-grid"><label class="form-field">Импортируемые файлы<textarea name="files" required rows="4" placeholder="app.py&#10;test_app.py">${esc(profile.files?.join('\n')||'')}</textarea></label><label class="form-field">Разрешённые файлы<textarea name="writable" required rows="4" placeholder="app.py">${esc(profile.writable?.join('\n')||'')}</textarea></label></div><p class="native-caption">Пути относительно репозитория, по одному на строку. До 20 файлов в каждом списке; тесты включите в импорт.</p><label class="form-field">Команда проверки<textarea name="checks" required rows="2" spellcheck="false">${esc(JSON.stringify(profile.checks||['python','-m','unittest','discover','-s','.','-v']))}</textarea></label><p class="native-caption">JSON-массив аргументов Python внутри sandbox. Текущий профиль использует стандартную библиотеку, без установки зависимостей проекта.</p><label class="form-field">Критерии приёмки<textarea name="acceptance" required rows="3" placeholder="Сохранён публичный API.&#10;Тесты проходят.">${esc(profile.acceptance?.join('\n')||'')}</textarea></label><details class="project-advanced" ${profile.commit?'open':''}><summary>Закрепить конкретный коммит</summary><p class="native-caption">По умолчанию перед каждым новым запуском определяется HEAD. Для доработок всегда используется коммит исходного прогона.</p><label class="form-field">Закреплённый коммит<input name="commit" maxlength="64" value="${esc(profile.commit||'')}" placeholder="Необязательно: полный хеш коммита" autocomplete="off"></label></details><p class="form-error" role="alert" hidden></p><div class="native-actions"><button class="secondary" type="button" data-close-project>Отмена</button><button class="primary" type="submit">Сохранить проект</button></div></form>`;
    modal.querySelectorAll('#project-form label').forEach(label=>label.querySelector('input,textarea')?.setAttribute('aria-label',label.firstChild.textContent.trim()));
    modal.showModal();modal.querySelector('[name="name"]').focus();
    modal.querySelectorAll('[data-close-project]').forEach(button=>button.addEventListener('click',()=>{if(!busy)modal.close();}));
    modal.querySelector('form').addEventListener('submit',async event=>{
      event.preventDefault();if(busy)return;
      const form=event.target,notice=form.querySelector('.form-error'),button=form.querySelector('[type="submit"]');notice.hidden=true;
      try{
        const fields=Object.fromEntries(new FormData(form)),list=key=>fields[key].split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
        const body={name:fields.name,description:fields.description,profile:{repo:fields.repo.trim(),commit:fields.commit.trim()||null,
          files:list('files'),writable:list('writable'),checks:JSON.parse(fields.checks),acceptance:list('acceptance')}};
        busy=true;button.disabled=true;
        await context.api(project?'/api/projects/'+project.id:'/api/projects',project?'PATCH':'POST',project?{expected_version:project.version,changes:body}:body);
        modal.close();await context.refresh(true);context.toast('Проект сохранён');
      }catch(error){notice.textContent=error instanceof SyntaxError?'Команда проверки: укажите корректный JSON-массив аргументов.':error.message;notice.hidden=false;}
      finally{busy=false;button.disabled=false;}
    });
  }
  document.addEventListener('click',async event=>{
    if(!context||location.pathname!=='/projects')return;
    if(event.target.closest('[data-new-project]'))open();
    const edit=event.target.closest('[data-edit-project]');if(edit)open(edit.dataset.editProject);
    if(event.target.closest('[data-project-archives]')){showArchived=!showArchived;mount(context.root,context);}
    const goal=event.target.closest('[data-project-goal]');if(goal&&!goal.disabled)context.openGoal(null,goal.dataset.projectGoal);
    const diagnostic=event.target.closest('[data-check-readiness]');
    if(diagnostic&&!diagnostic.disabled){
      const project=context.projects.find(p=>p.id===diagnostic.dataset.checkReadiness);checking.add(project.id);mount(context.root,context);
      try{const report=await window.readinessUI.check({project_id:project.id,expected_project_version:project.version});reports.set(project.id,{version:project.version,report});}
      catch(error){reports.set(project.id,{version:project.version,error:error.message});}
      finally{checking.delete(project.id);if(location.pathname==='/projects')mount(context.root,context);}return;
    }
    const archive=event.target.closest('[data-archive-project]'),check=event.target.closest('[data-check-project]');
    const button=archive||check;if(!button||button.disabled)return;
    const project=context.projects.find(p=>p.id===(archive?.dataset.archiveProject||check.dataset.checkProject));button.disabled=true;
    try{
      if(archive){await context.api('/api/projects/'+project.id,'PATCH',{expected_version:project.version,changes:{archived:!project.archived}});await context.refresh(true);}
      else{
        const result=await context.api('/api/projects/'+project.id+'/resolve','POST',{expected_version:project.version});
        checks.set(project.id,{version:project.version,message:'Git доступен · коммит '+result.project.commit.slice(0,12),error:false});
        if(location.pathname==='/projects')mount(context.root,context);
      }
    }catch(error){
      if(check){checks.set(project.id,{version:project.version,message:error.message,error:true});if(location.pathname==='/projects')mount(context.root,context);}
      else context.toast(error.message,true);
    }finally{if(button.isConnected)button.disabled=false;}
  });
  document.querySelector('#modal').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  window.projectLibrary={mount};
})();
