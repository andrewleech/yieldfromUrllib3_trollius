# TODO: Break this module up into pieces. Maybe group by functionality tested
# rather than the socket level-ness of it.

import trollius as asyncio
from trollius import From, Return
import functools

import sys


sys.path.extend(['..', '../..', '../../..'])

from yieldfrom_t.urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from yieldfrom_t.urllib3.poolmanager import proxy_from_url
from yieldfrom_t.urllib3.exceptions import (
        MaxRetryError,
        ProxyError,
        ReadTimeoutError,
        SSLError,
        ProtocolError,
)
from yieldfrom_t.urllib3.util.ssl_ import HAS_SNI
from yieldfrom_t.urllib3.util.timeout import Timeout
from yieldfrom_t.urllib3.util.retry import Retry

from hide_from_setup.dummyserver.testcase import SocketDummyServerTestCase
from hide_from_setup.dummyserver.server import (
    DEFAULT_CERTS, DEFAULT_CA, get_unreachable_address)

from nose.plugins.skip import SkipTest
from threading import Event
import socket
import ssl

def async_test(f):

    testLoop = asyncio.get_event_loop()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test


class TestCookies(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def test_multi_setcookie(self):
        def multicookie_response_handler(listener):
            sock = listener.accept()[0]

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            sock.send(b'HTTP/1.1 200 OK\r\n'
                      b'Set-Cookie: foo=1\r\n'
                      b'Set-Cookie: bar=1\r\n'
                      b'\r\n')
            sock.close()

        self._start_server(multicookie_response_handler)
        pool = HTTPConnectionPool(self.host, self.port)
        r = yield From(pool.request('GET', '/', retries=0))
        self.assertEqual(r.headers, {'set-cookie': 'foo=1, bar=1'})


class TestSNI(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def tst_hostname_in_first_request_packet(self):
        if not HAS_SNI:
            raise SkipTest('SNI-support not available')

        done_receiving = Event()
        self.buf = b''

        def socket_handler(listener):
            sock = listener.accept()[0]

            self.buf = sock.recv(65536) # We only accept one packet
            done_receiving.set()  # let the test know it can proceed
            sock.close()

        self._start_server(socket_handler)
        pool = HTTPSConnectionPool(self.host, self.port)
        try:
            yield From(pool.request('GET', '/', retries=0))
        except SSLError: # We are violating the protocol
            pass
        done_receiving.wait()
        self.assertTrue(self.host.encode() in self.buf,
                        "missing hostname in SSL handshake")


class TestSocketClosing(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def tst_recovery_when_server_closes_connection(self):
        # Does the pool work seamlessly if an open connection in the
        # connection pool gets hung up on by the server, then reaches
        # the front of the queue again?

        done_closing = Event()

        def socket_handler(listener):
            for i in 0, 1:
                sock = listener.accept()[0]

                buf = b''
                while not buf.endswith(b'\r\n\r\n'):
                    buf = sock.recv(65536)

                body = 'Response %d' % i
                sock.send(('HTTP/1.1 200 OK\r\n'
                          'Content-Type: text/plain\r\n'
                          'Content-Length: %d\r\n'
                          '\r\n'
                          '%s' % (len(body), body)).encode('utf-8'))

                sock.close()  # simulate a server timing out, closing socket
                done_closing.set()  # let the test know it can proceed

        self._start_server(socket_handler)
        pool = HTTPConnectionPool(self.host, self.port)

        response = yield From(pool.request('GET', '/', retries=0))
        self.assertEqual(response.status, 200)
        self.assertEqual((yield From(response.data), b'Response 0'))

        done_closing.wait()  # wait until the socket in our pool gets closed

        response = yield From(pool.request('GET', '/', retries=0))
        self.assertEqual(response.status, 200)
        self.assertEqual((yield From(response.data), b'Response 1'))

    @async_test
    def test_connection_refused(self):
        # Does the pool retry if there is no listener on the port?
        host, port = get_unreachable_address()
        pool = HTTPConnectionPool(host, port)
        self.aioAssertRaises(MaxRetryError, pool.request, 'GET', '/', retries=0)

    @async_test
    def test_connection_read_timeout(self):
        timed_out = Event()
        def socket_handler(listener):
            sock = listener.accept()[0]
            while not sock.recv(65536).endswith(b'\r\n\r\n'):
                pass

            timed_out.wait()
            sock.close()

        self._start_server(socket_handler)
        pool = HTTPConnectionPool(self.host, self.port, timeout=0.001, retries=False)

        try:
            self.aioAssertRaises(ReadTimeoutError, pool.request, 'GET', '/')
        finally:
            timed_out.set()

    @async_test
    def tst_timeout_errors_cause_retries(self):
        def socket_handler(listener):
            sock_timeout = listener.accept()[0]

            # Wait for a second request before closing the first socket.
            sock = listener.accept()[0]
            sock_timeout.close()

            # Second request.
            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            # Now respond immediately.
            body = 'Response 2'
            sock.send(('HTTP/1.1 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      '%s' % (len(body), body)).encode('utf-8'))

            sock.close()

        # In situations where the main thread throws an exception, the server
        # thread can hang on an accept() call. This ensures everything times
        # out within 1 second. This should be long enough for any socket
        # operations in the test suite to complete
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1)

        try:
            self._start_server(socket_handler)
            t = Timeout(connect=0.1, read=0.1)
            pool = HTTPConnectionPool(self.host, self.port, timeout=t)

            response = yield From(pool.request('GET', '/', retries=1))
            self.assertEqual(response.status, 200)
            self.assertEqual((yield From(response.data), b'Response 2'))
        finally:
            socket.setdefaulttimeout(default_timeout)

    @async_test
    def test_delayed_body_read_timeout(self):
        timed_out = Event()

        def socket_handler(listener):
            sock = listener.accept()[0]
            buf = b''
            body = 'Hi'
            while not buf.endswith(b'\r\n\r\n'):
                buf = sock.recv(65536)
            sock.send(('HTTP/1.1 200 OK\r\n'
                       'Content-Type: text/plain\r\n'
                       'Content-Length: %d\r\n'
                       '\r\n' % len(body)).encode('utf-8'))

            timed_out.wait()
            sock.send(body.encode('utf-8'))
            sock.close()

        self._start_server(socket_handler)
        pool = HTTPConnectionPool(self.host, self.port)

        response = yield From(pool.urlopen('GET', '/', retries=0, preload_content=False,
                                timeout=Timeout(connect=1, read=0.1)))
        try:
            self.aioAssertRaises(ReadTimeoutError, response.read)
        finally:
            timed_out.set()

    @async_test
    def test_incomplete_response(self):
        body = 'Response'
        partial_body = body[:2]

        def socket_handler(listener):
            sock = listener.accept()[0]

            # Consume request
            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf = sock.recv(65536)

            # Send partial response and close socket.
            sock.send((
                'HTTP/1.1 200 OK\r\n'
                'Content-Type: text/plain\r\n'
                'Content-Length: %d\r\n'
                '\r\n'
                '%s' % (len(body), partial_body)).encode('utf-8')
            )
            sock.close()

        self._start_server(socket_handler)
        pool = HTTPConnectionPool(self.host, self.port)

        response = yield From(pool.request('GET', '/', retries=0, preload_content=False))
        self.aioAssertRaises(ProtocolError, response.read)

    def test_retry_weird_http_version(self):
        """ Retry class should handle httplib.BadStatusLine errors properly """

        def socket_handler(listener):
            sock = listener.accept()[0]
            # First request.
            # Pause before responding so the first request times out.
            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            # send unknown http protocol
            body = "bad http 0.5 response"
            sock.send(('HTTP/0.5 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      '%s' % (len(body), body)).encode('utf-8'))
            sock.close()

            # Second request.
            sock = listener.accept()[0]
            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            # Now respond immediately.
            sock.send(('HTTP/1.1 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      'foo' % (len('foo'))).encode('utf-8'))

            sock.close()  # Close the socket.

        self._start_server(socket_handler)
        pool = HTTPConnectionPool(self.host, self.port)
        retry = Retry(read=1)
        response = yield From(pool.request('GET', '/', retries=retry))
        self.assertEqual(response.status, 200)
        self.assertEqual((yield From(response.data), b'foo'))



class TestProxyManager(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def test_simple(self):
        def echo_socket_handler(listener):
            sock = listener.accept()[0]

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            sock.send(('HTTP/1.1 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      '%s' % (len(buf), buf.decode('utf-8'))).encode('utf-8'))
            sock.close()

        self._start_server(echo_socket_handler)
        base_url = 'http://%s:%d' % (self.host, self.port)
        proxy = proxy_from_url(base_url)

        r = yield From(proxy.request('GET', 'http://google.com/'))

        self.assertEqual(r.status, 200)
        # FIXME: The order of the headers is not predictable right now. We
        # should fix that someday (maybe when we migrate to
        # OrderedDict/MultiDict).
        self.assertEqual(sorted((yield From(r.data)).split(b'\r\n')),
                         sorted([
                             b'GET http://google.com/ HTTP/1.1',
                             b'Host: google.com',
                             b'Accept-Encoding: identity',
                             b'Accept: */*',
                             b'',
                             b'',
                         ]))

    @async_test
    def test_headers(self):
        def echo_socket_handler(listener):
            sock = listener.accept()[0]

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            sock.send(('HTTP/1.1 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      '%s' % (len(buf), buf.decode('utf-8'))).encode('utf-8'))
            sock.close()

        self._start_server(echo_socket_handler)
        base_url = 'http://%s:%d' % (self.host, self.port)

        # Define some proxy headers.
        proxy_headers = {'For The Proxy': 'YEAH!'}
        proxy = proxy_from_url(base_url, proxy_headers=proxy_headers)

        conn = proxy.connection_from_url('http://www.google.com/')

        r = yield From(conn.urlopen('GET', 'http://www.google.com/', assert_same_host=False))

        self.assertEqual(r.status, 200)
        # FIXME: The order of the headers is not predictable right now. We
        # should fix that someday (maybe when we migrate to
        # OrderedDict/MultiDict).
        self.assertTrue(b'For The Proxy: YEAH!\r\n' in (yield From(r.data)))

    @async_test
    def test_retries(self):
        def echo_socket_handler(listener):
            sock = listener.accept()[0]
            # First request, which should fail
            sock.close()

            # Second request
            sock = listener.accept()[0]

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)

            sock.send(('HTTP/1.1 200 OK\r\n'
                      'Content-Type: text/plain\r\n'
                      'Content-Length: %d\r\n'
                      '\r\n'
                      '%s' % (len(buf), buf.decode('utf-8'))).encode('utf-8'))
            sock.close()

        self._start_server(echo_socket_handler)
        base_url = 'http://%s:%d' % (self.host, self.port)

        proxy = proxy_from_url(base_url)
        conn = proxy.connection_from_url('http://www.google.com')

        r = yield From(conn.urlopen('GET', 'http://www.google.com',
                         assert_same_host=False, retries=1))
        self.assertEqual(r.status, 200)

        self.aioAssertRaises(ProxyError, conn.urlopen, 'GET',
                'http://www.google.com',
                assert_same_host=False, retries=False)

    @async_test
    def tst_connect_reconn(self):
        def proxy_ssl_one(listener):
            sock = listener.accept()[0]

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += sock.recv(65536)
            s = buf.decode('utf-8')
            if not s.startswith('CONNECT '):
                sock.send(('HTTP/1.1 405 Method not allowed\r\n'
                           'Allow: CONNECT\r\n\r\n').encode('utf-8'))
                sock.close()
                return

            if not s.startswith('CONNECT %s:443' % (self.host,)):
                sock.send(('HTTP/1.1 403 Forbidden\r\n\r\n').encode('utf-8'))
                sock.close()
                return

            sock.send(('HTTP/1.1 200 Connection Established\r\n\r\n').encode('utf-8'))
            ssl_sock = ssl.wrap_socket(sock,
                                       server_side=True,
                                       keyfile=DEFAULT_CERTS['keyfile'],
                                       certfile=DEFAULT_CERTS['certfile'],
                                       ca_certs=DEFAULT_CA)

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += ssl_sock.recv(65536)

            ssl_sock.send(('HTTP/1.1 200 OK\r\n'
                           'Content-Type: text/plain\r\n'
                           'Content-Length: 2\r\n'
                           'Connection: close\r\n'
                           '\r\n'
                           'Hi').encode('utf-8'))
            ssl_sock.close()

        def echo_socket_handler(listener):
            proxy_ssl_one(listener)
            proxy_ssl_one(listener)

        self._start_server(echo_socket_handler)
        base_url = 'http://%s:%d' % (self.host, self.port)

        proxy = proxy_from_url(base_url)

        url = 'https://{0}'.format(self.host)
        conn = proxy.connection_from_url(url)
        r = yield From(conn.urlopen('GET', url, retries=0))
        self.assertEqual(r.status, 200)
        r = yield From(conn.urlopen('GET', url, retries=0))
        self.assertEqual(r.status, 200)


class TestSSL(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def test_ssl_failure_midway_through_conn(self):
        def socket_handler(listener):
            sock = listener.accept()[0]
            sock2 = sock.dup()
            ssl_sock = ssl.wrap_socket(sock,
                                       server_side=True,
                                       keyfile=DEFAULT_CERTS['keyfile'],
                                       certfile=DEFAULT_CERTS['certfile'],
                                       ca_certs=DEFAULT_CA)

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += ssl_sock.recv(65536)

            # Deliberately send from the non-SSL socket.
            sock2.send((
                'HTTP/1.1 200 OK\r\n'
                'Content-Type: text/plain\r\n'
                'Content-Length: 2\r\n'
                '\r\n'
                'Hi').encode('utf-8'))
            sock2.close()
            ssl_sock.close()

        self._start_server(socket_handler)
        pool = HTTPSConnectionPool(self.host, self.port)

        self.aioAssertRaises(SSLError, pool.request, 'GET', '/', retries=0)

    @async_test
    def test_ssl_read_timeout(self):
        timed_out = Event()

        def socket_handler(listener):
            sock = listener.accept()[0]
            ssl_sock = ssl.wrap_socket(sock,
                                       server_side=True,
                                       keyfile=DEFAULT_CERTS['keyfile'],
                                       certfile=DEFAULT_CERTS['certfile'],
                                       ca_certs=DEFAULT_CA)

            buf = b''
            while not buf.endswith(b'\r\n\r\n'):
                buf += ssl_sock.recv(65536)

            # Send incomplete message (note Content-Length)
            ssl_sock.send((
                'HTTP/1.1 200 OK\r\n'
                'Content-Type: text/plain\r\n'
                'Content-Length: 10\r\n'
                '\r\n'
                'Hi-').encode('utf-8'))
            timed_out.wait()

            sock.close()
            ssl_sock.close()

        self._start_server(socket_handler)
        pool = HTTPSConnectionPool(self.host, self.port)

        response = yield From(pool.urlopen('GET', '/', retries=0, preload_content=False,
                                timeout=Timeout(connect=1, read=0.5)))
        try:
            self.aioAssertRaises(ReadTimeoutError, response.read)
        finally:
            timed_out.set()


def consume_socket(sock, chunks=65536):
    while not sock.recv(chunks).endswith(b'\r\n\r\n'):
        pass


def create_response_handler(response, num=1):
    def socket_handler(listener):
        for _ in range(num):
            sock = listener.accept()[0]
            consume_socket(sock)

            sock.send(response)
            sock.close()

    return socket_handler


class TestErrorWrapping(SocketDummyServerTestCase):

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        except Exception as e:
            self.fail('expected %s exception, got %s instead' % (exc.__name__, e.__name__))
        else:
            self.fail('expected %s not raised' % exc.__name__)

    @async_test
    def test_bad_statusline(self):
        handler = create_response_handler(
           b'HTTP/1.1 Omg What Is This?\r\n'
           b'Content-Length: 0\r\n'
           b'\r\n'
        )
        self._start_server(handler)
        pool = HTTPConnectionPool(self.host, self.port, retries=False)
        self.aioAssertRaises(ProtocolError, pool.request, 'GET', '/')

    @async_test
    def test_unknown_protocol(self):
        handler = create_response_handler(
           b'HTTP/1000 200 OK\r\n'
           b'Content-Length: 0\r\n'
           b'\r\n'
        )
        self._start_server(handler)
        pool = HTTPConnectionPool(self.host, self.port, retries=False)
        self.aioAssertRaises(ProtocolError, pool.request, 'GET', '/')
