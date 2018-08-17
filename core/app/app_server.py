import hmac
import time

from aiohttp import web
import mohawk
from mohawk.exc import HawkFail

from shared.logger import (
    logged,
    get_child_logger,
)
from shared.utils import (
    random_url_safe,
)

from .app_elasticsearch import (
    es_search,
    es_search_existing_scroll,
    es_search_new_scroll,
    es_min_verification_age,
)
from .app_redis import (
    set_private_scroll_id,
    redis_get_metrics,
    set_nonce_nx,
    get_feeds_status,
)


NOT_PROVIDED = 'Authentication credentials were not provided.'
INCORRECT = 'Incorrect authentication credentials.'
MISSING_CONTENT_TYPE = 'Content-Type header was not set. ' + \
                       'It must be set for authentication, even if as the empty string.'
MISSING_X_FORWARDED_PROTO = 'The X-Forwarded-Proto header was not set.'
NOT_AUTHORIZED = 'You are not authorized to perform this action.'
UNKNOWN_ERROR = 'An unknown error occurred.'


def authenticator(incoming_key_pairs, redis_client, nonce_expire):
    @web.middleware
    async def authenticate(request, handler):
        if 'X-Forwarded-Proto' not in request.headers:
            request['logger'].warning(
                'Failed authentication: no X-Forwarded-Proto header passed'
            )
            raise web.HTTPUnauthorized(text=MISSING_X_FORWARDED_PROTO)

        if 'Authorization' not in request.headers:
            raise web.HTTPUnauthorized(text=NOT_PROVIDED)

        if 'Content-Type' not in request.headers:
            raise web.HTTPUnauthorized(text=MISSING_CONTENT_TYPE)

        try:
            receiver = await _authenticate_or_raise(incoming_key_pairs, redis_client,
                                                    nonce_expire, request)
        except HawkFail as exception:
            request['logger'].warning('Failed authentication %s', exception)
            raise web.HTTPUnauthorized(text=INCORRECT)

        request['logger'] = get_child_logger(
            request['logger'],
            receiver.resource.credentials['id'],
        )
        request['permissions'] = receiver.resource.credentials['permissions']
        return await handler(request)

    return authenticate


async def _authenticate_or_raise(incoming_key_pairs, redis_client, nonce_expire, request):
    def lookup_credentials(passed_access_key_id):
        matching_key_pairs = [
            key_pair
            for key_pair in incoming_key_pairs
            if hmac.compare_digest(key_pair['key_id'], passed_access_key_id)
        ]

        if not matching_key_pairs:
            raise HawkFail(f'No Hawk ID of {passed_access_key_id}')

        return {
            'id': matching_key_pairs[0]['key_id'],
            'key': matching_key_pairs[0]['secret_key'],
            'permissions': matching_key_pairs[0]['permissions'],
            'algorithm': 'sha256',
        }

    receiver = mohawk.Receiver(
        lookup_credentials,
        request.headers['Authorization'],
        str(request.url.with_scheme(request.headers['X-Forwarded-Proto'])),
        request.method,
        content=await request.read(),
        content_type=request.headers['Content-Type'],
        # Mohawk doesn't provide an async way of checking nonce
        seen_nonce=lambda _, __, ___: False,
    )

    nonce = receiver.resource.nonce
    access_key_id = receiver.resource.credentials['id']
    nonce_key = f'nonce-{access_key_id}-{nonce}'
    redis_response = await set_nonce_nx(redis_client, nonce_key, nonce_expire)
    seen_nonce = not redis_response == b'OK'
    if seen_nonce:
        raise web.HTTPUnauthorized(text=INCORRECT)

    return receiver


def authorizer():
    @web.middleware
    async def authorize(request, handler):
        if request.method not in request['permissions']:
            raise web.HTTPForbidden(text=NOT_AUTHORIZED)

        return await handler(request)

    return authorize


def raven_reporter(raven_client):
    @web.middleware
    async def _raven_reporter(request, handler):
        try:
            return await handler(request)
        except (web.HTTPSuccessful, web.HTTPRedirection, web.HTTPClientError):
            raise
        except BaseException:
            raven_client.captureException(data={
                'request': {
                    'url': str(request.url.with_scheme(request.headers['X-Forwarded-Proto'])),
                    'query_string': request.query_string,
                    'method': request.method,
                    'data': await request.read(),
                    'headers':  dict(request.headers),
                }
            })
            raise

    return _raven_reporter


def convert_errors_to_json():
    @web.middleware
    async def _convert_errors_to_json(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exception:
            response = json_response({'details': exception.text}, status=exception.status_code)
        except BaseException as exception:
            request['logger'].exception('About to return 500')
            response = json_response({'details': UNKNOWN_ERROR}, status=500)
        return response

    return _convert_errors_to_json


async def handle_post(_):
    return json_response({'secret': 'to-be-hidden'}, status=200)


def handle_get_new(logger, session, redis_client, pagination_expire, es_endpoint):
    return _handle_get(logger, session, redis_client, pagination_expire, es_endpoint,
                       es_search_new_scroll)


def handle_get_existing(logger, session, redis_client, pagination_expire, es_endpoint):
    return _handle_get(logger, session, redis_client, pagination_expire, es_endpoint,
                       es_search_existing_scroll)


def _handle_get(logger, session, redis_client, pagination_expire, es_endpoint, get_path_query):
    async def handle(request):
        incoming_body = await request.read()
        path, query, body = await get_path_query(redis_client, request.match_info,
                                                 incoming_body)

        async def to_public_scroll_url(private_scroll_id):
            public_scroll_id = random_url_safe(8)
            await set_private_scroll_id(redis_client, public_scroll_id,
                                        private_scroll_id, pagination_expire)
            return str(request.url.join(
                request.app.router['scroll'].url_for(public_scroll_id=public_scroll_id)))

        results, status = await es_search(logger, session, es_endpoint, path, query, body,
                                          {'Content-Type': request.headers['Content-Type']},
                                          to_public_scroll_url)

        return json_response(results, status=status)

    return handle


def handle_get_check(parent_logger, session, redis_client, es_endpoint, feed_endpoints):
    start_counter = time.perf_counter()

    # Grace period after uptime to allow new feeds to start reporting
    # without making the service appear down
    startup_feed_grace_seconds = 30

    async def handle(_):
        logger = get_child_logger(parent_logger, 'check')

        with logged(logger, 'Checking', []):
            await redis_client.execute('SET', 'redis-check', b'GREEN', 'EX', 1)
            redis_result = await redis_client.execute('GET', 'redis-check')
            is_redis_green = redis_result == b'GREEN'

            min_age = await es_min_verification_age(logger, session, es_endpoint)
            is_elasticsearch_green = min_age < 60

            uptime = time.perf_counter() - start_counter
            in_grace_period = uptime <= startup_feed_grace_seconds

            # The status of the feeds are via Redis...
            # - To actually reflect if each was recently sucessful, since it is done by the
            #   outgoing application, not this one
            # - To keep the guarantee that we only make a single request to each feed at any one
            #   time (locking between the outoing application and this one would be tricky)
            feeds_statuses = await get_feeds_status(redis_client, [
                feed.unique_id for feed in feed_endpoints
            ])
            feeds_statuses_with_red = [status if status ==
                                       b'GREEN' else b'RED' for status in feeds_statuses]
            are_all_feeds_green = all([status == b'GREEN' for status in feeds_statuses])

            all_green = is_redis_green and is_elasticsearch_green and \
                (are_all_feeds_green or in_grace_period)

            status = \
                (b'__UP__' if all_green else b'__DOWN__') + \
                (b' (IN_STARTUP_GRACE_PERIOD)' if in_grace_period else b'') + b'\n' + \
                (b'redis:' + (b'GREEN' if is_redis_green else b'RED')) + b'\n' + \
                (b'elasticsearch:' + (b'GREEN' if is_elasticsearch_green else b'RED')) + b'\n' + \
                b''.join([
                    feed.unique_id.encode('utf-8') + b':' + feeds_statuses_with_red[i] + b'\n'
                    for (i, feed) in enumerate(feed_endpoints)
                ])

        return web.Response(body=status, status=200, headers={
            'Content-Type': 'text/plain; charset=utf-8',
        })

    return handle


def handle_get_metrics(redis_client):
    async def handle(_):
        return web.Response(body=await redis_get_metrics(redis_client), status=200, headers={
            'Content-Type': 'text/plain; charset=utf-8',
        })

    return handle


def json_response(data, status):
    return web.json_response(data, status=status, headers={
        'Server': 'activity-stream'
    })
