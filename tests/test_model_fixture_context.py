"""Fault injection fixtures keep their trigger when action history is archived."""
import json
import unittest
from model_fixture_context import has_file_write


class FixtureContextTests(unittest.TestCase):
    def test_visible_action_and_archived_edits_both_trigger_after_write(self):
        for action in ('write_file','replace_text','replace_text_batch'):
            with self.subTest(action=action):
                self.assertTrue(has_file_write([{'role':'assistant','content':json.dumps({'action':action,'path':'app.py'})}]))
                self.assertTrue(has_file_write([{'role':'assistant','content':'{"archived":true}'},
                    {'role':'user','content':json.dumps({'working_context':{'edits':[{'step':3,'action':action,'path':'app.py'}]}})}]))

    def test_code_mentions_plain_notes_and_reads_do_not_fabricate_writes(self):
        messages=[{'role':'user','content':'Previous decision note: write_file next'},
            {'role':'user','content':json.dumps({'tool_result':{'content':'write_file replace_text_batch'}})},
            {'role':'assistant','content':'{"action":"read_file","path":"app.py"}'},
            {'role':'user','content':json.dumps({'working_context':{'edits':[],'recent_action_metadata':[{'action':'read_file'}]}})}]
        self.assertFalse(has_file_write(messages))

    def test_missing_non_json_and_non_object_messages_are_ignored(self):
        self.assertFalse(has_file_write([{}, {'role':'assistant','content':'note'},
            {'role':'user','content':'[]'},{'role':'user','content':'null'}, {'role':'system','content':'{"action":"write_file"}'}]))


if __name__=='__main__':unittest.main()
