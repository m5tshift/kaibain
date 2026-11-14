"""Scripted HTTP fixtures read explicit progress, independent of context windows."""
import json


def model_response(body,decision):
    """Wrap scripted developer actions only when the wire schema requests it."""
    schema=body.get('format')
    properties=schema.get('properties') if isinstance(schema,dict) else None
    if isinstance(properties,dict) and 'decision_note' in properties and 'decision' in properties:
        return {'decision_note':'Scripted next action from fixture.','decision':decision}
    return decision


def completed_steps(messages):
    for message in messages:
        if message.get('role')!='user':continue
        try:value=json.loads(message['content'])
        except (ValueError,TypeError,KeyError):continue
        if isinstance(value,dict) and isinstance(value.get('working_context'),dict):
            steps=value['working_context'].get('completed_steps')
            if type(steps) is int and steps>=0:return steps
    return sum(message.get('role')=='assistant' for message in messages)


def has_file_write(messages):
    """Read explicit action history/archived edits, never infer work from text."""
    actions={'write_file','replace_text','replace_text_batch'}
    for message in messages:
        try:value=json.loads(message['content'])
        except (ValueError,TypeError,KeyError):continue
        if not isinstance(value,dict):continue
        if message.get('role')=='assistant' and value.get('action') in actions:return True
        view=value.get('working_context')
        if message.get('role')=='user' and isinstance(view,dict):
            if any(isinstance(edit,dict) and edit.get('action') in actions for edit in view.get('edits',[])):return True
    return False
