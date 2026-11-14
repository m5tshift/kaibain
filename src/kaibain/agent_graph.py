"""Read-only graph projection. Client reports do not grant execution authority."""
from datetime import datetime, timezone
import json


def seconds_between(start, end):
    try:return max(0, int((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()))
    except (ValueError,TypeError):return 0


def project_graph(db, agents):
    clock=datetime.now(timezone.utc).isoformat()
    tasks={row['id']:dict(row) for row in db.execute('SELECT id,parent_id,title,status FROM tasks')}
    runs={row['id']:dict(row) for row in db.execute('SELECT id,status,error,decision,updated_at,reviewed_at,continuation_of FROM agent_runs')}
    continued={run['continuation_of'] for run in runs.values() if run['continuation_of']}
    questions={row['task_id']:dict(row) for row in db.execute("SELECT task_id,run_id,question,created_at FROM goal_questions WHERE status='open'")}
    by_id={a['id']:a for a in agents}
    for agent in agents:
        task=tasks.get(agent['task_id']);root=task
        while root and root['parent_id']:root=tasks.get(root['parent_id'])
        agent['task_title']=task['title'] if task else None
        agent['goal_id']=root['id'] if root else None
        agent['goal_title']=root['title'] if root else None
    links=[]
    for row in db.execute("SELECT * FROM agent_links WHERE state!='obsolete' ORDER BY created_at,id"):
        link=dict(row);source=by_id.get(link['source_id']);target=by_id.get(link['target_id'])
        if (source and target and source['task_id']==link['source_task_id'] and target['task_id']==link['target_task_id'] and
                source['goal_id'] and source['goal_id']==target['goal_id']):links.append(link)
    for agent in agents:
        coordinator=by_id.get(agent['coordinator_id'])
        if agent['task_id'] and coordinator and coordinator['task_id'] and agent['goal_id']==coordinator['goal_id']:
            links.append({'id':'assignment:'+agent['id'],'source_id':coordinator['id'],'target_id':agent['id'],
                          'kind':'assignment','state':'active','message':'Назначение задачи'})
    for agent in agents:
        phase=agent['activity_phase'];reason=agent['action'];source='mcp'
        if phase=='idle' and agent['status']!='available':
            phase={'working':'working','waiting':'waiting','done':'done','error':'error'}[agent['status']]
        start=agent['phase_started_at'] or agent['registered_at'];end=clock
        run=runs.get(agent['run_id']);question=questions.get(agent['goal_id'])
        if run and run['status'] in ('failed','interrupted','cancelled','completed','cancelling','waiting_for_input'):
            source='run'
            if run['status'] in ('failed','interrupted'):
                phase='blocked';reason=run['error'] or 'Прогон прерван; требуется решение пользователя.'
            elif run['status']=='cancelled':phase='stopped';reason='Прогон остановлен пользователем.'
            elif run['status']=='cancelling':phase='stopping';reason='Ожидается завершение процессов и очистка microVM.'
            elif run['status']=='waiting_for_input':
                phase='waiting_for_input' if question else 'continued' if run['id'] in continued else 'waiting_for_resume'
                reason=question['question'] if question else 'Ответ передан новой попытке; этот контекст завершён.' if run['id'] in continued else 'Ответ сохранён; ожидает явного продолжения.'
            elif run['decision']=='accepted':phase='done';reason='Результат принят пользователем.'
            elif run['decision']=='changes_requested':phase='blocked';reason='Пользователь вернул результат на доработку.'
            else:phase='review';reason='Результат и проверки сохранены; ожидает решения пользователя.'
            start=run['reviewed_at'] or run['updated_at']
            if run['status'] not in ('cancelling','waiting_for_input') and phase not in ('review','blocked'):
                end=start
        if question and (not run or question['run_id']==run['id']):
            phase='waiting_for_input';reason=question['question'];start=question['created_at'];source='dialogue'
        elif not run and agent['task_id'] and phase=='waiting_for_input':
            phase='waiting';reason='Ответ получен; ожидает следующего действия координатора.';source='dialogue'
        if source=='mcp' and not agent['connected']:
            end=agent['last_seen']
            if phase not in ('done','review','error'):phase='offline';reason='Нет heartbeat более 30 секунд или клиент отключён.'
        waits=[link for link in links if link['source_id']==agent['id'] and link['kind']=='dependency' and link['state']=='active']
        if source=='mcp' and phase=='waiting' and waits:
            reason='Ожидает результат: '+', '.join(by_id[link['target_id']]['name'] for link in waits)
        activity={'phase':phase,'reason':reason,'source':source,'since':start,'elapsed_seconds':seconds_between(start,end)}
        budget=agent['budget']
        if budget:
            elapsed=budget['elapsed_seconds']
            if agent['connected'] and (not run or run['status']=='running'):
                elapsed+=seconds_between(budget.get('observed_at'),clock)
            activity['budget']={**budget,'seconds_remaining':max(0,round(budget['seconds_limit']-elapsed)),
                'tokens_remaining':max(0,budget['tokens_limit']-budget['tokens_used']),
                'steps_remaining':max(0,budget['steps_limit']-budget['steps_used'])}
        else:activity['budget']=None
        agent['activity']=activity
    return {'links':links,'observed_at':clock}
