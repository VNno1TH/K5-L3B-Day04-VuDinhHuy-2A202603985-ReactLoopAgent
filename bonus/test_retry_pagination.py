"""Test offline cho phần bonus: giới hạn retry, retry_after, pagination, rút gọn context, tương thích bài chính."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent
from agent import run_agent
from status_tools import SCHEMAS, StatusTools

URL = 'https://stspg.io/abc123'


def call(call_id, name, args):
    raw = json.dumps(args)
    msg = {'role': 'assistant', 'content': None,
           'tool_calls': [{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': raw}}]}
    return {'message': msg, 'calls': [{'id': call_id, 'name': name, 'arguments': raw}], 'finish': 'tool_calls', 'usage': {}}


def final(answer='Đã kiểm tra'):
    return {'message': {'role': 'assistant', 'content': answer}, 'calls': [], 'finish': 'stop', 'usage': {}}


class Scripted:
    def __init__(self, responses):
        self.responses, self.histories, self.schemas = iter(responses), [], []

    def complete(self, messages, schemas):
        self.histories.append(list(messages))
        self.schemas.append(schemas)
        return next(self.responses)


class FakeTools:
    """Tools giả: mỗi tên tool có một hàm (args, số lần gọi) -> result."""
    schemas = [{'type': 'function', 'function': {'name': 'x'}}]
    system_prompt = 'Bạn điều tra status của DigitalOcean.'

    def __init__(self, handlers):
        self.handlers, self.count = handlers, {}

    def execute(self, name, args):
        self.count[name] = self.count.get(name, 0) + 1
        return self.handlers[name](args, self.count[name])


def rate_limited(**extra):
    return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, **extra}


def tool_events(result, name):
    return [e for e in result['trace'] if e['event'] == 'tool' and e['name'] == name]


def page(offset, total=4, limit=2):
    nxt = offset + limit
    return {'ok': True, 'provider': 'npm', 'id': 'cc0t', 'url': URL, 'offset': offset, 'total_updates': total,
            'updates': [], 'next_offset': nxt if nxt < total else None}


class RetryTests(unittest.TestCase):
    def test_blocks_fourth_attempt_but_answers_the_call(self):
        tools = FakeTools({'get_vercel_status': lambda a, n: rate_limited()})
        calls = [call(str(i), 'get_vercel_status', {}) for i in range(4)]
        p = Scripted(calls + [final('Vercel: không kiểm tra được vì rate limit.')])
        result = run_agent('q', p, tools, max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.count['get_vercel_status'], 3)           # tool chỉ chạy 3 lần
        self.assertEqual(len(tool_events(result, 'get_vercel_status')), 3)
        self.assertEqual([e['event'] for e in result['trace']].count('retry_blocked'), 1)
        last_tool_msg = [m for m in p.histories[-1] if m['role'] == 'tool'][-1]
        self.assertEqual(last_tool_msg['tool_call_id'], '3')            # vẫn trả đúng tool_call_id
        self.assertEqual(json.loads(last_tool_msg['content'])['error'], 'RETRY_LIMIT_REACHED')

    def test_success_resets_counter(self):
        seq = [rate_limited(), rate_limited(), {'overall': {}}, rate_limited(), rate_limited(), {'overall': {}}]
        tools = FakeTools({'get_vercel_status': lambda a, n: seq[n - 1]})
        p = Scripted([call(str(i), 'get_vercel_status', {}) for i in range(6)] + [final()])
        result = run_agent('q', p, tools, max_steps=9)
        self.assertEqual(tools.count['get_vercel_status'], 6)
        self.assertNotIn('retry_blocked', [e['event'] for e in result['trace']])

    def test_long_retry_after_stops_retry(self):
        tools = FakeTools({'get_vercel_status': lambda a, n: rate_limited(retry_after='120')})
        p = Scripted([call('a', 'get_vercel_status', {}), call('b', 'get_vercel_status', {}), final('Vercel: chưa biết.')])
        with mock.patch.object(agent, '_sleep') as sleep:
            result = run_agent('q', p, tools, max_steps=6)
        self.assertEqual(tools.count['get_vercel_status'], 1)
        sleep.assert_not_called()
        self.assertEqual(result['status'], 'completed')

    def test_short_retry_after_waits_before_retry(self):
        seq = [rate_limited(retry_after_seconds=2), {'overall': {}}]
        tools = FakeTools({'get_vercel_status': lambda a, n: seq[n - 1]})
        p = Scripted([call('a', 'get_vercel_status', {}), call('b', 'get_vercel_status', {}), final()])
        with mock.patch.object(agent, '_sleep') as sleep:
            run_agent('q', p, tools, max_steps=6)
        sleep.assert_called_once()
        self.assertTrue(0 < sleep.call_args[0][0] <= 2)

    def test_invalid_arguments_is_limited_too(self):
        bad = {'ok': False, 'error': 'INVALID_ARGUMENTS', 'message': 'offset phải là integer'}
        tools = FakeTools({'get_incident_details': lambda a, n: bad})
        p = Scripted([call(str(i), 'get_incident_details', {'provider': 'npm', 'incident_id': 'x'}) for i in range(4)]
                     + [final('npm: không đọc được.')])
        run_agent('q', p, tools, max_steps=8)
        self.assertEqual(tools.count['get_incident_details'], 3)

    def test_unreported_failure_triggers_one_nudge(self):
        tools = FakeTools({'get_vercel_status': lambda a, n: rate_limited()})
        p = Scripted([call('a', 'get_vercel_status', {}), final('Mọi thứ ổn.'), final('Không kiểm tra được Vercel (429).')])
        result = run_agent('q', p, tools, max_steps=6)
        self.assertIn('validation_unreported_failure', [e['event'] for e in result['trace']])
        self.assertIn('Vercel', result['answer'])

    def test_reported_failure_needs_no_nudge(self):
        tools = FakeTools({'get_vercel_status': lambda a, n: rate_limited()})
        p = Scripted([call('a', 'get_vercel_status', {}), final('Không kiểm tra được Vercel (429).')])
        result = run_agent('q', p, tools, max_steps=6)
        self.assertNotIn('validation_unreported_failure', [e['event'] for e in result['trace']])
        self.assertEqual(len(p.histories), 2)


class PaginationTests(unittest.TestCase):
    def handler(self, args, n):
        return page(args.get('offset', 0))

    def test_unread_page_triggers_nudge_and_model_continues(self):
        tools = FakeTools({'get_incident_details': self.handler})
        p = Scripted([call('a', 'get_incident_details', {'provider': 'npm', 'incident_id': 'cc0t'}),
                      final('Xem ' + URL),
                      call('b', 'get_incident_details', {'provider': 'npm', 'incident_id': 'cc0t', 'offset': 2}),
                      final('Đã đọc hết. ' + URL)])
        result = run_agent('q', p, tools, max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('validation_unread_pages', [e['event'] for e in result['trace']])
        self.assertEqual(len(tool_events(result, 'get_incident_details')), 2)
        self.assertTrue(result['answer'].startswith('Đã đọc hết'))

    def test_no_nudge_when_timeline_fully_read(self):
        tools = FakeTools({'get_incident_details': lambda a, n: page(a.get('offset', 0), total=2)})
        p = Scripted([call('a', 'get_incident_details', {'provider': 'npm', 'incident_id': 'cc0t'}), final(URL)])
        result = run_agent('q', p, tools, max_steps=6)
        self.assertNotIn('validation_unread_pages', [e['event'] for e in result['trace']])
        self.assertEqual(len(p.histories), 2)

    def test_nudge_failure_falls_back_to_first_answer(self):
        tools = FakeTools({'get_incident_details': self.handler})
        p = Scripted([call('a', 'get_incident_details', {'provider': 'npm', 'incident_id': 'cc0t'}), final('Xem ' + URL)])
        result = run_agent('q', p, tools, max_steps=6)  # provider hết dữ liệu sau khi nhắc
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], 'Xem ' + URL)
        self.assertIn('fallback_to_draft', [e['event'] for e in result['trace']])


class ContextTests(unittest.TestCase):
    def test_long_list_is_compacted_for_model_but_kept_in_trace(self):
        big = {'provider': 'vercel', 'overall': {}, 'distractors': [{'id': i} for i in range(160)]}
        tools = FakeTools({'get_vercel_status': lambda a, n: big})
        p = Scripted([call('a', 'get_vercel_status', {}), final('Vercel ổn.')])
        result = run_agent('q', p, tools, max_steps=4)
        seen = json.loads([m for m in p.histories[1] if m['role'] == 'tool'][0]['content'])
        self.assertEqual(seen['distractors']['omitted_items'], 160)
        self.assertEqual(len(seen['distractors']['kept_sample']), 3)
        self.assertEqual(len(tool_events(result, 'get_vercel_status')[0]['result']['distractors']), 160)
        self.assertIn('context_compacted', [e['event'] for e in result['trace']])

    def test_short_lists_untouched(self):
        res = {'incidents': [{'id': i} for i in range(8)]}
        self.assertEqual(agent._compact(res), (res, {}))


class CompatibilityTests(unittest.TestCase):
    def test_main_tools_keep_main_schemas_and_prompt(self):
        p = Scripted([final()])
        run_agent('q', p, StatusTools(), max_steps=2)
        self.assertEqual(p.schemas[0], SCHEMAS)
        self.assertEqual(p.histories[0][0]['content'], agent.SYSTEM_PROMPT)

    def test_bonus_prompt_extends_tools_prompt(self):
        p = Scripted([final()])
        run_agent('q', p, FakeTools({}), max_steps=2)
        content = p.histories[0][0]['content']
        self.assertTrue(content.startswith(FakeTools.system_prompt))
        self.assertIn('RATE_LIMITED', content)
        self.assertEqual(p.schemas[0], FakeTools.schemas)


if __name__ == '__main__':
    unittest.main()