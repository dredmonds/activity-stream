import asyncio
import datetime
import json
import os
from subprocess import Popen
import sys
import unittest
from unittest.mock import Mock, patch

import aiohttp
from aiohttp import web
from freezegun import freeze_time

from core.app import run_application
from core.app_utils import flatten
from core.tests_utils import (
    append_until,
    delete_all_es_data,
    fetch_all_es_data_until,
    get,
    get_until,
    has_at_least,
    has_at_least_ordered_items,
    hawk_auth_header,
    is_http_accepted_eventually,
    mock_env,
    post,
    post_with_headers,
    read_file,
    respond_http,
    run_app_until_accepts_http,
    run_es_application,
    run_feed_application,
)


class TestBase(unittest.TestCase):

    def setup_manual(self, env, mock_feed):
        ''' Test setUp function that can be customised on a per-test basis '''

        self.addCleanup(self.teardown_manual)

        self.os_environ_patcher = patch.dict(os.environ, env)
        self.os_environ_patcher.start()
        self.loop = asyncio.get_event_loop()

        self.feed_requested = [asyncio.Future(), asyncio.Future()]

        def feed_requested_callback(request):
            try:
                first_not_done = next(
                    future for future in self.feed_requested if not future.done())
            except StopIteration:
                pass
            else:
                first_not_done.set_result(request)

        self.feed_runner_1 = \
            self.loop.run_until_complete(
                run_feed_application(mock_feed, feed_requested_callback, 8081),
            )

        original_app_runner = aiohttp.web.AppRunner

        def wrapped_app_runner(*args, **kwargs):
            self.app_runner = original_app_runner(*args, **kwargs)
            return self.app_runner

        self.app_runner_patcher = patch('aiohttp.web.AppRunner', wraps=wrapped_app_runner)
        self.app_runner_patcher.start()
        self.loop.run_until_complete(delete_all_es_data())

    def teardown_manual(self):
        for task in asyncio.Task.all_tasks():
            task.cancel()
        self.loop = asyncio.get_event_loop()
        self.loop.run_until_complete(asyncio.gather(*flatten([
            ([self.app_runner.cleanup()] if hasattr(self, 'app_runner') else []) +
            ([self.feed_runner_1.cleanup()] if hasattr(self, 'feed_runner_1') else [])
        ])))
        if hasattr(self, 'app_runner_patcher'):
            self.app_runner_patcher.stop()
        if hasattr(self, 'os_environ_patcher'):
            self.os_environ_patcher.stop()


class TestAuthentication(TestBase):

    def test_no_auth_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        text, status = self.loop.run_until_complete(post_with_headers(url, {
            'Content-Type': '',
            'X-Forwarded-For': '1.2.3.4',
            'X-Forwarded-Proto': 'http',
        }))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Authentication credentials were not provided."}')

    def test_bad_id_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-incorrect', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_bad_secret_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-2', url, 'POST', '', '',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_bad_method_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'GET', '', 'application/json',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_bad_content_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', 'content', '',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_bad_content_type_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', 'some-type',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_no_content_type_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', 'some-type',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post_with_headers(url, {
            'Authorization': auth,
            'X-Forwarded-For': x_forwarded_for,
            'X-Forwarded-Proto': 'http',
        }))
        self.assertEqual(status, 401)
        self.assertIn('Content-Type header was not set.', text)

    def test_no_proto_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        text, status = self.loop.run_until_complete(post_with_headers(url, {
            'Authorization': auth,
            'Content-Type': '',
            'X-Forwarded-For': '1.2.3.4',
        }))
        self.assertEqual(status, 401)
        self.assertIn('The X-Forwarded-Proto header was not set.', text)

    def test_time_skew_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        past = datetime.datetime.now() + datetime.timedelta(seconds=-61)
        with freeze_time(past):
            auth = hawk_auth_header(
                'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
            )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_repeat_auth_then_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        x_forwarded_for = '1.2.3.4'
        _, status_1 = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status_1, 200)

        text_2, status_2 = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status_2, 401)
        self.assertEqual(text_2, '{"details": "Incorrect authentication credentials."}')

    def test_nonces_cleared(self):
        ''' Makes duplicate requests, but with the code patched so the nonce expiry time
            is shorter then the allowed Hawk skew. The second request succeeding gives
            evidence that the cache of nonces was cleared.
        '''
        self.setup_manual(env=mock_env(), mock_feed=read_file)

        now = datetime.datetime.now()
        past = now + datetime.timedelta(seconds=-45)

        with patch('core.app.NONCE_EXPIRE', 30):
            run_app_until_accepts_http()

            url = 'http://127.0.0.1:8080/v1/'
            x_forwarded_for = '1.2.3.4'

            with freeze_time(past):
                auth = hawk_auth_header(
                    'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
                )
                _, status_1 = self.loop.run_until_complete(
                    post(url, auth, x_forwarded_for))
            self.assertEqual(status_1, 200)

            with freeze_time(now):
                _, status_2 = self.loop.run_until_complete(
                    post(url, auth, x_forwarded_for))
            self.assertEqual(status_2, 200)

    def test_no_x_forwarded_for_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        text, status = self.loop.run_until_complete(post_with_headers(url, {
            'Authorization': auth,
            'Content-Type': '',
        }))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_bad_x_forwarded_for_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        x_forwarded_for = '3.4.5.6'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_at_end_x_forwarded_for_401(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        x_forwarded_for = '3.4.5.6,1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 401)
        self.assertEqual(text, '{"details": "Incorrect authentication credentials."}')

    def test_second_id_returns_object(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-2', 'incoming-some-secret-2', url, 'POST', '', '',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 200)
        self.assertEqual(text, '{"secret": "to-be-hidden"}')

    def test_post_returns_object(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'POST', '', '',
        )
        x_forwarded_for = '1.2.3.4'
        text, status = self.loop.run_until_complete(post(url, auth, x_forwarded_for))
        self.assertEqual(status, 200)
        self.assertEqual(text, '{"secret": "to-be-hidden"}')

    def test_post_creds_get_403(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-1', 'incoming-some-secret-1', url, 'GET', '', 'application/json',
        )
        x_forwarded_for = '1.2.3.4'
        text, status, _ = self.loop.run_until_complete(get(url, auth, x_forwarded_for, b''))
        self.assertEqual(status, 403)
        self.assertEqual(text, '{"details": "You are not authorized to perform this action."}')


class TestApplication(TestBase):

    def test_get_returns_feed_data(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        x_forwarded_for = '1.2.3.4'

        result, status, headers = self.loop.run_until_complete(
            get_until(url, x_forwarded_for, has_at_least_ordered_items(2), asyncio.sleep))
        self.assertEqual(status, 200)
        self.assertEqual(result['orderedItems'][0]['id'],
                         'dit:exportOpportunities:Enquiry:49863:Create')
        self.assertEqual(result['orderedItems'][1]['id'],
                         'dit:exportOpportunities:Enquiry:49862:Create')
        self.assertEqual(headers['Server'], 'activity-stream')

    def test_pagination(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url_1 = 'http://127.0.0.1:8080/v1/'
        x_forwarded_for = '1.2.3.4'
        self.loop.run_until_complete(
            get_until(url_1, x_forwarded_for, has_at_least_ordered_items(2), asyncio.sleep))

        query = json.dumps({
            'size': '1',
        }).encode('utf-8')
        auth = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url_1,
            'GET', query, 'application/json',
        )
        result_1, status_1, _ = self.loop.run_until_complete(
            get(url_1, auth, x_forwarded_for, query))
        result_1_json = json.loads(result_1)
        self.assertEqual(status_1, 200)
        self.assertEqual(len(result_1_json['orderedItems']), 1)
        self.assertEqual(result_1_json['orderedItems'][0]['id'],
                         'dit:exportOpportunities:Enquiry:49863:Create')
        self.assertIn('next', result_1_json)

        url_2 = result_1_json['next']
        auth_2 = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url_2, 'GET', b'', 'application/json',
        )
        result_2, status_2, _ = self.loop.run_until_complete(
            get(url_2, auth_2, x_forwarded_for, b''))
        result_2_json = json.loads(result_2)
        self.assertEqual(status_2, 200)
        self.assertEqual(len(result_2_json['orderedItems']), 1)
        self.assertEqual(result_2_json['orderedItems'][0]['id'],
                         'dit:exportOpportunities:Enquiry:49862:Create')
        self.assertIn('next', result_2_json)

    def test_pagination_expiry(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)
        run_app_until_accepts_http()

        url_1 = 'http://127.0.0.1:8080/v1/'
        x_forwarded_for = '1.2.3.4'
        self.loop.run_until_complete(
            get_until(url_1, x_forwarded_for, has_at_least_ordered_items(2), asyncio.sleep))

        now = datetime.datetime.now()
        past = now + datetime.timedelta(seconds=-60)

        query = json.dumps({
            'size': '1',
        }).encode('utf-8')

        with freeze_time(past):
            auth = hawk_auth_header(
                'incoming-some-id-3', 'incoming-some-secret-3', url_1,
                'GET', query, 'application/json',
            )
            result_1, _, _ = self.loop.run_until_complete(
                get(url_1, auth, x_forwarded_for, query))
            result_1_json = json.loads(result_1)
            url_2 = result_1_json['next']

        with freeze_time(now):
            auth_2 = hawk_auth_header(
                'incoming-some-id-3', 'incoming-some-secret-3', url_2,
                'GET', b'', 'application/json',
            )
            result_2, status_2, _ = self.loop.run_until_complete(
                get(url_2, auth_2, x_forwarded_for, b''))
            self.assertEqual(json.loads(result_2)['details'], 'Scroll ID not found.')
            self.assertEqual(status_2, 404)

    def test_bad_mapping_then_exception(self):
        self.setup_manual(env=mock_env(), mock_feed=read_file)

        async def put_incompatible_mapping():
            headers = {
                'Content-Type': 'application/json',
            }

            async with aiohttp.ClientSession() as session:
                await session.put('http://127.0.0.1:9200/activities', headers=headers)
                await session.put('http://127.0.0.1:9200/activities/_doc/1',
                                  headers=headers, json={
                                      'published_date': 'Something that is not a date'
                                  })

        self.loop.run_until_complete(put_incompatible_mapping())

        app = asyncio.ensure_future(run_application())
        done, pending = self.loop.run_until_complete(asyncio.wait([app], timeout=5))

        self.assertFalse(pending)
        self.assertIn('mapper [published_date] of different type',
                      str(next(iter(done)).exception()))

    def test_bad_ensure_index_exception(self):
        self.setup_manual(env={**mock_env(), 'ELASTICSEARCH__PORT': '9201'},
                          mock_feed=read_file)

        async def respond_401_with_message(_):
            message = '{"error": "some message"}'
            return web.Response(text=message, status=401, content_type='application/json')

        routes = [
            web.put('/activities', respond_401_with_message),
        ]
        es_runner = self.loop.run_until_complete(
            run_es_application(port=9201, override_routes=routes))

        app = asyncio.ensure_future(run_application())
        done, pending = self.loop.run_until_complete(asyncio.wait([app], timeout=5))

        self.loop.run_until_complete(es_runner.cleanup())
        self.assertFalse(pending)
        self.assertIn('some message"',
                      str(next(iter(done)).exception()))

    def test_get_can_filter(self):
        env = {
            **mock_env(),
            'FEEDS__1__SEED': (
                'http://localhost:8081/'
                'tests_fixture_activity_stream_multipage_1.json'
            ),
            'FEEDS__2__SEED': 'http://localhost:8081/tests_fixture_zendesk_1.json',
            'FEEDS__2__API_EMAIL': 'test@test.com',
            'FEEDS__2__API_KEY': 'some-key',
            'FEEDS__2__TYPE': 'zendesk',
        }
        self.setup_manual(env=env, mock_feed=read_file)

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0)

        async def _test():
            with patch('asyncio.sleep', wraps=fast_sleep):
                asyncio.ensure_future(run_application())
                return await fetch_all_es_data_until(has_at_least(4), original_sleep)

        self.loop.run_until_complete(_test())

        url = 'http://127.0.0.1:8080/v1/'
        x_forwarded_for = '1.2.3.4'

        query = json.dumps({
            'query': {
                'bool': {
                    'filter': [{
                        'range': {
                            'published': {
                                'gte': '2011-04-12',
                                'lte': '2011-04-12',
                            },
                        },
                    }],
                },
            },
        }).encode('utf-8')
        auth = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url, 'GET', query, 'application/json',
        )
        result, status, _ = self.loop.run_until_complete(
            get(url, auth, x_forwarded_for, query))
        self.assertEqual(status, 200)
        data = json.loads(result)
        self.assertEqual(len(data['orderedItems']), 2)
        self.assertIn('2011-04-12', data['orderedItems'][0]['published'])
        self.assertIn('2011-04-12', data['orderedItems'][1]['published'])

        query = json.dumps({
            'query': {
                'bool': {
                    'filter': [{
                        'range': {
                            'published': {
                                'gte': '2011-04-12',
                                'lte': '2011-04-12',
                            },
                        },
                    }, {
                        'term': {
                            'type': 'Create',
                        },
                    }, {
                        'term': {
                            'object.type': 'dit:exportOpportunities:Enquiry',
                        },
                    }],
                },
            },
        }).encode('utf-8')
        auth = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url, 'GET', query, 'application/json',
        )
        result, status, _ = self.loop.run_until_complete(
            get(url, auth, x_forwarded_for, query))
        self.assertEqual(status, 200)
        data = json.loads(result)
        self.assertEqual(len(data['orderedItems']), 1)
        self.assertIn('2011-04-12', data['orderedItems'][0]['published'])
        self.assertEqual('Create', data['orderedItems'][0]['type'])
        self.assertIn('dit:exportOpportunities:Enquiry', data['orderedItems'][0]['object']['type'])

    @freeze_time('2012-01-14 12:00:01')
    @patch('os.urandom', return_value=b'something-random')
    def test_single_page(self, _):
        posted_to_es_once, append_es = append_until(lambda results: len(results) == 1)

        self.setup_manual(env={**mock_env(), 'ELASTICSEARCH__PORT': '9201'},
                          mock_feed=read_file)

        async def return_200_and_callback(request):
            content, headers = (await request.content.read(), request.headers)
            asyncio.get_event_loop().call_soon(append_es, (content, headers))
            return await respond_http('{}', 200)(request)

        routes = [
            web.post('/_bulk', return_200_and_callback),
        ]
        es_runner = self.loop.run_until_complete(
            run_es_application(port=9201, override_routes=routes))
        asyncio.ensure_future(run_application())

        async def _test():
            return await posted_to_es_once

        [[es_bulk_content, es_bulk_headers]] = self.loop.run_until_complete(_test())
        es_bulk_request_dicts = [
            json.loads(line)
            for line in es_bulk_content.split(b'\n')[0:-1]
        ]

        self.loop.run_until_complete(es_runner.cleanup())

        self.assertEqual(self.feed_requested[0].result(
        ).headers['Authorization'], (
            'Hawk '
            'mac="keUgjONtI1hLtS4DzGl+0G63o1nPFmvtIsTsZsB/NPM=", '
            'hash="B0weSUXsMcb5UhL41FZbrUJCAotzSI3HawE1NPLRUz8=", '
            'id="feed-some-id", '
            'ts="1326542401", '
            'nonce="c29tZX"'
        ))

        self.assertEqual(
            es_bulk_headers['Authorization'],
            'AWS4-HMAC-SHA256 '
            'Credential=some-id/20120114/us-east-2/es/aws4_request, '
            'SignedHeaders=content-type;host;x-amz-date, '
            'Signature=a28466109ed35c5e8b48203115f6c862217886c119fda19dd5bbe6043f0df1fd')
        self.assertEqual(es_bulk_content.decode('utf-8')[-1], '\n')
        self.assertEqual(es_bulk_headers['Content-Type'], 'application/x-ndjson')

        self.assertEqual(es_bulk_request_dicts[0]['index']['_index'], 'activities')
        self.assertEqual(es_bulk_request_dicts[0]['index']['_type'], '_doc')
        self.assertEqual(es_bulk_request_dicts[0]['index']
                         ['_id'], 'dit:exportOpportunities:Enquiry:49863:Create')
        self.assertEqual(es_bulk_request_dicts[1]['published'], '2018-04-12T12:48:13+00:00')
        self.assertEqual(es_bulk_request_dicts[1]['type'], 'Create')
        self.assertEqual(es_bulk_request_dicts[1]['object']
                         ['type'][1], 'dit:exportOpportunities:Enquiry')
        self.assertEqual(es_bulk_request_dicts[1]['actor']['dit:companiesHouseNumber'], '123432')

        self.assertEqual(es_bulk_request_dicts[2]['index']['_index'], 'activities')
        self.assertEqual(es_bulk_request_dicts[2]['index']['_type'], '_doc')
        self.assertEqual(es_bulk_request_dicts[2]['index']
                         ['_id'], 'dit:exportOpportunities:Enquiry:49862:Create')
        self.assertEqual(es_bulk_request_dicts[3]['published'], '2018-03-23T17:06:53+00:00')
        self.assertEqual(es_bulk_request_dicts[3]['type'], 'Create')
        self.assertEqual(es_bulk_request_dicts[3]['object']
                         ['type'][1], 'dit:exportOpportunities:Enquiry')
        self.assertEqual(es_bulk_request_dicts[3]['actor']['dit:companiesHouseNumber'], '82312')

    def test_es_auth(self):
        get_es_once, append_es = append_until(lambda results: len(results) == 1)

        self.setup_manual(env={**mock_env(), 'ELASTICSEARCH__PORT': '9201'},
                          mock_feed=read_file)

        async def return_200_and_callback(request):
            print('HELLo')
            content, headers = (await request.content.read(), request.headers)
            asyncio.get_event_loop().call_soon(append_es, (content, headers))
            return await respond_http('{}', 200)(request)

        routes = [
            web.get('/activities/_search', return_200_and_callback),
        ]
        es_runner = self.loop.run_until_complete(
            run_es_application(port=9201, override_routes=routes))
        run_app_until_accepts_http()

        with \
                freeze_time('2012-01-15 12:00:01'), \
                patch('os.urandom', return_value=b'something-random'):
            url = 'http://127.0.0.1:8080/v1/'
            auth = hawk_auth_header(
                'incoming-some-id-3', 'incoming-some-secret-3', url, 'GET', '', 'application/json',
            )
            x_forwarded_for = '1.2.3.4'
            self.loop.run_until_complete(get(url, auth, x_forwarded_for, b''))
            self.loop.run_until_complete(es_runner.cleanup())

            async def _test():
                return await get_es_once

            [[_, es_headers]] = self.loop.run_until_complete(_test())

        self.assertEqual(es_headers['Authorization'],
                         'AWS4-HMAC-SHA256 '
                         'Credential=some-id/20120115/us-east-2/es/aws4_request, '
                         'SignedHeaders=content-type;host;x-amz-date, '
                         'Signature=5b6d0a3400a19730c1fcd359c77f605ae5a6bbb287dfa5'
                         '04dfe05f37767c28d4')

    def test_es_401_is_proxied(self):
        self.setup_manual(env={**mock_env(), 'ELASTICSEARCH__PORT': '9201'},
                          mock_feed=read_file)
        routes = [
            web.get('/activities/_search', respond_http('{"elasticsearch": "error"}', 401)),
        ]
        es_runner = self.loop.run_until_complete(
            run_es_application(port=9201, override_routes=routes))
        run_app_until_accepts_http()

        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url, 'GET', '', 'application/json',
        )
        x_forwarded_for = '1.2.3.4'
        text, status, _ = self.loop.run_until_complete(get(url, auth, x_forwarded_for, b''))
        self.loop.run_until_complete(es_runner.cleanup())

        self.assertEqual(status, 401)
        self.assertEqual(text, '{"elasticsearch": "error"}')

    def test_es_no_connect_on_get_500(self):
        self.setup_manual({
            **mock_env(),
            'ELASTICSEARCH__PORT': '9201'
        }, mock_feed=read_file)
        es_runner = self.loop.run_until_complete(
            run_es_application(port=9201, override_routes=[]))
        run_app_until_accepts_http()

        self.loop.run_until_complete(es_runner.cleanup())
        url = 'http://127.0.0.1:8080/v1/'
        auth = hawk_auth_header(
            'incoming-some-id-3', 'incoming-some-secret-3', url, 'GET', '', 'application/json',
        )
        x_forwarded_for = '1.2.3.4'
        text, status, _ = self.loop.run_until_complete(get(url, auth, x_forwarded_for, b''))

        self.assertEqual(status, 500)
        self.assertEqual(text, '{"details": "An unknown error occurred."}')

    def test_multipage(self):
        self.setup_manual(
            {**mock_env(), 'FEEDS__1__SEED': (
                'http://localhost:8081/'
                'tests_fixture_activity_stream_multipage_1.json'
            )
            },
            mock_feed=read_file,
        )

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0)

        async def _test():
            with patch('asyncio.sleep', wraps=fast_sleep) as mock_sleep:
                asyncio.ensure_future(run_application())
                mock_sleep.assert_not_called()
                result = await fetch_all_es_data_until(has_at_least(2), original_sleep)
                mock_sleep.assert_any_call(0)
                return result

        results = self.loop.run_until_complete(_test())
        self.assertIn('dit:exportOpportunities:Enquiry:4986999:Create',
                      str(results))

    def test_two_feeds(self):
        env = {
            **mock_env(),
            'FEEDS__2__SEED': 'http://localhost:8081/tests_fixture_activity_stream_2.json',
            'FEEDS__2__ACCESS_KEY_ID': 'feed-some-id',
            'FEEDS__2__SECRET_ACCESS_KEY': '?[!@$%^%',
            'FEEDS__2__TYPE': 'activity_stream',
        }
        self.setup_manual(env=env, mock_feed=read_file)

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0)

        async def _test():
            with patch('asyncio.sleep', wraps=fast_sleep):
                asyncio.ensure_future(run_application())
                return await fetch_all_es_data_until(has_at_least(4), original_sleep)

        results = self.loop.run_until_complete(_test())
        self.assertIn('dit:exportOpportunities:Enquiry:49863:Create', str(results))
        self.assertIn('dit:exportOpportunities:Enquiry:42863:Create', str(results))

    def test_zendesk(self):
        def has_two_zendesk_tickets(results):
            if 'hits' not in results or 'hits' not in results['hits']:
                return False

            is_zendesk_ticket = [
                item
                for item in results['hits']['hits']
                for source in [item['_source']]
                if 'dit:application' in source and source['dit:application'] == 'zendesk'
            ]
            return len(is_zendesk_ticket) == 2

        env = {
            **mock_env(),
            'FEEDS__2__SEED': 'http://localhost:8081/tests_fixture_zendesk_1.json',
            'FEEDS__2__API_EMAIL': 'test@test.com',
            'FEEDS__2__API_KEY': 'some-key',
            'FEEDS__2__TYPE': 'zendesk',
        }
        self.setup_manual(env=env, mock_feed=read_file)

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0)

        async def _test():
            with patch('asyncio.sleep', wraps=fast_sleep):
                asyncio.ensure_future(run_application())
                return await fetch_all_es_data_until(has_two_zendesk_tickets, original_sleep)

        results = json.dumps(self.loop.run_until_complete(_test()))
        self.assertIn('"dit:zendesk:Ticket:1"', results)
        self.assertIn('"dit:zendesk:Ticket:1:Create"', results)
        self.assertIn('"2011-04-12T12:48:13+00:00"', results)
        self.assertIn('"dit:zendesk:Ticket:3"', results)
        self.assertIn('"dit:zendesk:Ticket:3:Create"', results)
        self.assertIn('"2011-04-12T12:48:13+00:00"', results)

    def test_on_bad_json_retries(self):
        sent_broken = False

        def read_file_broken_then_fixed(path):
            nonlocal sent_broken

            feed_contents_maybe_broken = (
                read_file(path) +
                ('something-invalid' if not sent_broken else '')
            )
            sent_broken = True
            return feed_contents_maybe_broken

        self.setup_manual(env=mock_env(), mock_feed=read_file_broken_then_fixed)

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0)

        async def _test():
            with patch('asyncio.sleep', wraps=fast_sleep) as mock_sleep:
                asyncio.ensure_future(run_application())
                mock_sleep.assert_not_called()
                results = await fetch_all_es_data_until(has_at_least(1), original_sleep)
                mock_sleep.assert_any_call(60)
                return results

        es_results = self.loop.run_until_complete(_test())

        self.assertIn(
            'dit:exportOpportunities:Enquiry:49863:Create',
            str(es_results),
        )


class TestProcess(unittest.TestCase):

    def setUp(self):
        loop = asyncio.get_event_loop()

        loop.run_until_complete(delete_all_es_data())
        self.feed_runner_1 = loop.run_until_complete(run_feed_application(read_file, Mock(), 8081))
        self.server = Popen([sys.executable, '-m', 'core.app'], env={
            **mock_env(),
            'COVERAGE_PROCESS_START': os.environ['COVERAGE_PROCESS_START'],
        })

    def tearDown(self):
        for task in asyncio.Task.all_tasks():
            task.cancel()
        self.server.terminate()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.feed_runner_1.cleanup())

    def test_server_accepts_http(self):
        self.assertTrue(is_http_accepted_eventually())
