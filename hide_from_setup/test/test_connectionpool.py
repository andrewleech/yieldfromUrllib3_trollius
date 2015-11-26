import unittest
import os
import functools
import trollius as asyncio
from trollius import From, Return

import sys
sys.path.append('../../')
#sys.path.append('..')

os.environ['PYTHONASYNCIODEBUG'] = '1'

from yieldfrom_t.urllib3.connectionpool import (
    connection_from_url,
    HTTPConnection,
    HTTPConnectionPool,
)
from yieldfrom_t.urllib3.util.timeout import Timeout
from yieldfrom_t.urllib3.packages.ssl_match_hostname import CertificateError
from yieldfrom_t.urllib3.exceptions import (
    ClosedPoolError,
    EmptyPoolError,
    HostChangedError,
    LocationValueError,
    MaxRetryError,
    ProtocolError,
    SSLError,
)

from socket import error as SocketError
from ssl import SSLError as BaseSSLError

from queue import Empty
from yieldfrom_t.http.client import HTTPException


def async_test(f):

    testLoop = asyncio.get_event_loop()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test


class TestConnectionPool(unittest.TestCase):
    """
    Tests in this suite should exercise the ConnectionPool functionality
    without actually making any network requests or connections.
    """

    @asyncio.coroutine
    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        else:
            raise Exception('expected %s not raised' % exc.__name__)


    def test_same_host(self):
        same_host = [
            ('http://google.com/', '/'),
            ('http://google.com/', 'http://google.com/'),
            ('http://google.com/', 'http://google.com'),
            ('http://google.com/', 'http://google.com/abra/cadabra'),
            ('http://google.com:42/', 'http://google.com:42/abracadabra'),
            # Test comparison using default ports
            ('http://google.com:80/', 'http://google.com/abracadabra'),
            ('http://google.com/', 'http://google.com:80/abracadabra'),
            ('https://google.com:443/', 'https://google.com/abracadabra'),
            ('https://google.com/', 'https://google.com:443/abracadabra'),
        ]

        for a, b in same_host:
            c = connection_from_url(a)
            self.assertTrue(c.is_same_host(b), "%s =? %s" % (a, b))

        not_same_host = [
            ('https://google.com/', 'http://google.com/'),
            ('http://google.com/', 'https://google.com/'),
            ('http://yahoo.com/', 'http://google.com/'),
            ('http://google.com:42', 'https://google.com/abracadabra'),
            ('http://google.com', 'https://google.net/'),
            # Test comparison with default ports
            ('http://google.com:42', 'http://google.com'),
            ('https://google.com:42', 'https://google.com'),
            ('http://google.com:443', 'http://google.com'),
            ('https://google.com:80', 'https://google.com'),
            ('http://google.com:443', 'https://google.com'),
            ('https://google.com:80', 'http://google.com'),
            ('https://google.com:443', 'http://google.com'),
            ('http://google.com:80', 'https://google.com'),
        ]

        for a, b in not_same_host:
            c = connection_from_url(a)
            self.assertFalse(c.is_same_host(b), "%s =? %s" % (a, b))
            c = connection_from_url(b)
            self.assertFalse(c.is_same_host(a), "%s =? %s" % (b, a))

    @async_test
    def test_max_connections(self):
        pool = HTTPConnectionPool(host='localhost', maxsize=1, block=True)

        try:
            yield From(pool._get_conn(timeout=0.1))

            try:
                yield From(pool._get_conn(timeout=0.1))
                self.fail("Managed to get a connection without EmptyPoolError")
            except EmptyPoolError:
                pass

            try:
                yield From(pool.request('GET', '/', pool_timeout=0.1))
                self.fail("Managed to get a connection without EmptyPoolError")
            except EmptyPoolError:
                pass

        except Exception as e:
            self.assertTrue(False)

        self.assertEqual(pool.num_connections, 1)

    @async_test
    def test_pool_edgecases(self):
        pool = HTTPConnectionPool(host='localhost', maxsize=1, block=False)

        conn1 = yield From(pool._get_conn(timeout=0.1))
        conn2 = yield From(pool._get_conn(timeout=0.1)) # New because block=False

        pool._put_conn(conn1)
        pool._put_conn(conn2) # Should be discarded

        self.assertEqual(conn1, (yield From(pool._get_conn())))
        self.assertNotEqual(conn2, (yield From(pool._get_conn(timeout=0.1))))

        self.assertEqual(pool.num_connections, 3)

    def test_exception_str(self):
        self.assertEqual(
            str(EmptyPoolError(HTTPConnectionPool(host='localhost'), "Test.")),
            "HTTPConnectionPool(host='localhost', port=None): Test.")

    def test_retry_exception_str(self):
        self.assertEqual(
            str(MaxRetryError(
                HTTPConnectionPool(host='localhost'), "Test.", None)),
            "HTTPConnectionPool(host='localhost', port=None): "
            "Max retries exceeded with url: Test. (Caused by redirect)")

        err = SocketError("Test")

        # using err.__class__ here, as socket.error is an alias for OSError
        # since Py3.3 and gets printed as this
        self.assertEqual(
            str(MaxRetryError(
                HTTPConnectionPool(host='localhost'), "Test.", err)),
            "HTTPConnectionPool(host='localhost', port=None): "
            "Max retries exceeded with url: Test. "
            "(Caused by %r)" % err)

    @asyncio.coroutine
    def test_pool_size(self):
        POOL_SIZE = 1
        pool = HTTPConnectionPool(host='localhost', maxsize=POOL_SIZE, block=True)

        def _raise(ex):
            raise ex()

        def _test(exception, expect):
            pool._make_request = lambda *args, **kwargs: _raise(exception)
            try:
                yield From(pool.request('GET', '/'))
            except expect:
                pass
            else:
                self.assertTrue(False, 'Expected exception %s not raised' % expect)

            self.assertEqual(pool.pool.qsize(), POOL_SIZE)

        # Make sure that all of the exceptions return the connection to the pool
        _test(Empty, EmptyPoolError)
        _test(BaseSSLError, SSLError)
        _test(CertificateError, SSLError)

        # The pool should never be empty, and with these two exceptions being raised,
        # a retry will be triggered, but that retry will fail, eventually raising
        # MaxRetryError, not EmptyPoolError
        # See: https://github.com/shazow/urllib3/issues/76
        pool._make_request = lambda *args, **kwargs: _raise(HTTPException)
        try:
            yield From(pool.request('GET', '/', retries=1, pool_timeout=0.01))
        except MaxRetryError:
            pass
        else:
            self.assertTrue(False, 'MaxRetryError not raised')

        self.assertEqual(pool.pool.qsize(), POOL_SIZE)

    @async_test
    def test_assert_same_host(self):
        c = connection_from_url('http://google.com:80')

        try:
            yield From(c.request('GET', 'http://yahoo.com:80', assert_same_host=True))
        except HostChangedError as e:
            pass
        else:
            self.assertTrue(False, 'HostChangedError not raised by request')

    @async_test
    def test_pool_close(self):
        pool = connection_from_url('http://google.com:80')

        # Populate with some connections
        conn1 = yield From(pool._get_conn(timeout=0.01))
        conn2 = yield From(pool._get_conn(timeout=0.01))
        conn3 = yield From(pool._get_conn(timeout=0.01))
        pool._put_conn(conn1)
        pool._put_conn(conn2)

        old_pool_queue = pool.pool

        pool.close()
        self.assertEqual(pool.pool, None)

        self.aioAssertRaises(ClosedPoolError, pool._get_conn)

        pool._put_conn(conn3)

        self.aioAssertRaises(ClosedPoolError, pool._get_conn)

        self.aioAssertRaises(Empty, old_pool_queue.get, block=False)

    def test_pool_timeouts(self):
        pool = HTTPConnectionPool(host='localhost')
        conn = pool._new_conn()
        self.assertEqual(conn.__class__, HTTPConnection)
        self.assertEqual(pool.timeout.__class__, Timeout)
        self.assertEqual(pool.timeout._read, Timeout.DEFAULT_TIMEOUT)
        self.assertEqual(pool.timeout._connect, Timeout.DEFAULT_TIMEOUT)
        self.assertEqual(pool.timeout.total, None)

        pool = HTTPConnectionPool(host='localhost', timeout=3)
        self.assertEqual(pool.timeout._read, 3)
        self.assertEqual(pool.timeout._connect, 3)
        self.assertEqual(pool.timeout.total, None)

    def test_no_host(self):
        self.assertRaises(LocationValueError, HTTPConnectionPool, None)



if __name__ == '__main__':
    unittest.main()
