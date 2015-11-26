import unittest
import trollius as asyncio
from trollius import From, Return
import json
import functools

import sys
sys.path.extend(['..', '../..', '../../../'])

from hide_from_setup.dummyserver.testcase import (HTTPDummyServerTestCase,
                                  IPv6HTTPDummyServerTestCase)
from yieldfrom_t.urllib3.poolmanager import PoolManager
from yieldfrom_t.urllib3.connectionpool import port_by_scheme
from yieldfrom_t.urllib3.exceptions import MaxRetryError


def async_test(f):

    testLoop = asyncio.get_event_loop()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test



class TestPoolManager(HTTPDummyServerTestCase):

    def setUp(self):
        self.base_url = 'http://%s:%d' % (self.host, self.port)
        self.base_url_alt = 'http://%s:%d' % (self.host_alt, self.port)

    @async_test
    def test_redirect(self):
        http = PoolManager()

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                     fields={'target': '%s/' % self.base_url},
                                     redirect=False))

        self.assertEqual(r.status, 303)

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                    fields={'target': '%s/' % self.base_url}))

        self.assertEqual(r.status, 200)
        self.assertEqual((yield From(r.data)), b'Dummy server!')

    @async_test
    def test_redirect_twice(self):
        http = PoolManager()

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                     fields={'target': '%s/redirect' % self.base_url},
                                     redirect=False))

        self.assertEqual(r.status, 303)

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                         fields={'target': '%s/redirect?target=%s/' % (self.base_url, self.base_url)}))

        self.assertEqual(r.status, 200)
        self.assertEqual((yield From(r.data)), b'Dummy server!')

    @async_test
    def test_redirect_to_relative_url(self):
        http = PoolManager()

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                     fields = {'target': '/redirect'},
                                     redirect = False))

        self.assertEqual(r.status, 303)

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                     fields = {'target': '/redirect'}))

        self.assertEqual(r.status, 200)
        self.assertEqual((yield From(r.data)), b'Dummy server!')

    @async_test
    def test_cross_host_redirect(self):
        http = PoolManager()

        cross_host_location = '%s/echo?a=b' % self.base_url_alt
        try:
            yield From(http.request('GET', '%s/redirect' % self.base_url,
                                     fields={'target': cross_host_location},
                                     timeout=0.1, retries=0))
            self.fail("Request succeeded instead of raising an exception like it should.")

        except MaxRetryError:
            pass

        r = yield From(http.request('GET', '%s/redirect' % self.base_url,
                                    fields={'target': '%s/echo?a=b' % self.base_url_alt},
                                    timeout=0.1, retries=1))

        self.assertEqual(r._pool.host, self.host_alt)

    @async_test
    def test_missing_port(self):
        # Can a URL that lacks an explicit port like ':80' succeed, or
        # will all such URLs fail with an error?

        http = PoolManager()

        # By globally adjusting `port_by_scheme` we pretend for a moment
        # that HTTP's default port is not 80, but is the port at which
        # our test server happens to be listening.
        port_by_scheme['http'] = self.port
        try:
            r = yield From(http.request('GET', 'http://%s/' % self.host, retries=0))
        finally:
            port_by_scheme['http'] = 80

        self.assertEqual(r.status, 200)
        self.assertEqual((yield From(r.data)), b'Dummy server!')

    @async_test
    def test_headers(self):
        http = PoolManager(headers={'Foo': 'bar'})

        r = yield From(http.request_encode_url('GET', '%s/headers' % self.base_url))
        returned_headers = json.loads((yield From(r.data).decode()))
        self.assertEqual(returned_headers.get('Foo'), 'bar')

        r = yield From(http.request_encode_body('POST', '%s/headers' % self.base_url))
        returned_headers = json.loads((yield From(r.data).decode()))
        self.assertEqual(returned_headers.get('Foo'), 'bar')

        r = yield From(http.request_encode_url('GET', '%s/headers' % self.base_url, headers={'Baz': 'quux'}))
        returned_headers = json.loads((yield From(r.data).decode()))
        self.assertEqual(returned_headers.get('Foo'), None)
        self.assertEqual(returned_headers.get('Baz'), 'quux')

        r = yield From(http.request_encode_body('GET', '%s/headers' % self.base_url, headers={'Baz': 'quux'}))
        returned_headers = json.loads((yield From(r.data).decode()))
        self.assertEqual(returned_headers.get('Foo'), None)
        self.assertEqual(returned_headers.get('Baz'), 'quux')

    @async_test
    def test_http_with_ssl_keywords(self):
        http = PoolManager(ca_certs='REQUIRED')

        r = yield From(http.request('GET', 'http://%s:%s/' % (self.host, self.port)))
        self.assertEqual(r.status, 200)


class TestIPv6PoolManager(IPv6HTTPDummyServerTestCase):
    def setUp(self):
        self.base_url = 'http://[%s]:%d' % (self.host, self.port)

    @async_test
    def test_ipv6(self):
        http = PoolManager()
        yield From(http.request('GET', self.base_url))

if __name__ == '__main__':
    unittest.main()
