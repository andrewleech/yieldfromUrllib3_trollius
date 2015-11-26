import logging
import socket
import unittest
import trollius as asyncio
from trollius import From, Return

import mock

import errno
import sys
import time

try:
    from urllib.parse import urlencode
except:
    from urllib import urlencode

import sys
sys.path.extend(['..', '../..', '../../../'])

from tst_stuff import requires_network

from port_helpers import find_unused_port
from yieldfrom_t.urllib3 import (
    encode_multipart_formdata,
    HTTPConnectionPool,
)
from yieldfrom_t.urllib3.exceptions import (
    ConnectTimeoutError,
    EmptyPoolError,
    DecodeError,
    MaxRetryError,
    ReadTimeoutError,
    ProtocolError,
)
from yieldfrom_t.urllib3.packages.six import b, u
from yieldfrom_t.urllib3.util.retry import Retry
from yieldfrom_t.urllib3.util.timeout import Timeout

import tornado
from hide_from_setup.dummyserver.testcase import HTTPDummyServerTestCase

from nose.tools import timed

log = logging.getLogger('urllib3.connectionpool')
log.setLevel(logging.NOTSET)
log.addHandler(logging.StreamHandler(sys.stdout))


class TestConnectionPool(HTTPDummyServerTestCase):

    @asyncio.coroutine
    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        else:
            self.fail('expected %s not raised' % exc.__name__)

    def setUp(self):
        self.pool = HTTPConnectionPool(self.host, self.port)

    @asyncio.coroutine
    def get(self):
        r = yield From(self.pool.request('GET', '/specific_method',
                                         fields={'method': 'GET'}))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @asyncio.coroutine
    def post_url(self):
        r = yield From(self.pool.request('POST', '/specific_method',
                               fields={'method': 'POST'}))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @asyncio.coroutine
    def test_urlopen_put(self):
        r = yield From(self.pool.urlopen('PUT', '/specific_method?method=PUT'))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @asyncio.coroutine
    def test_wrong_specific_method(self):
        # To make sure the dummy server is actually returning failed responses
        r = yield From(self.pool.request('GET', '/specific_method',
                               fields={'method': 'POST'}))
        self.assertEqual(r.status, 400, (yield From(r.data)))

        r = yield From(self.pool.request('POST', '/specific_method',
                               fields={'method': 'GET'}))
        self.assertEqual(r.status, 400, (yield From(r.data)))

    @asyncio.coroutine
    def test_upload(self):
        data = "I'm in ur multipart form-data, hazing a cheezburgr"
        fields = {
            'upload_param': 'filefield',
            'upload_filename': 'lolcat.txt',
            'upload_size': len(data),
            'filefield': ('lolcat.txt', data),
        }

        r = yield From(self.pool.request('POST', '/upload', fields=fields))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @asyncio.coroutine
    def test_one_name_multiple_values(self):
        fields = [
            ('foo', 'a'),
            ('foo', 'b'),
        ]

        # urlencode
        r = yield From(self.pool.request('GET', '/echo', fields=fields))
        self.assertEqual((yield From(r.data)), b'foo=a&foo=b')

        # multipart
        r = yield From(self.pool.request('POST', '/echo', fields=fields))
        self.assertEqual((yield From(r.data).count(b'name="foo"'), 2))


    @asyncio.coroutine
    def test_unicode_upload(self):
        fieldname = u('myfile')
        filename = u('\xe2\x99\xa5.txt')
        data = u('\xe2\x99\xa5').encode('utf8')
        size = len(data)

        fields = {
            u('upload_param'): fieldname,
            u('upload_filename'): filename,
            u('upload_size'): size,
            fieldname: (filename, data),
        }

        r = yield From(self.pool.request('POST', '/upload', fields=fields))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @asyncio.coroutine
    def test_timeout_float(self):
        url = '/sleep?seconds=0.005'
        # Pool-global timeout
        pool = HTTPConnectionPool(self.host, self.port, timeout=0.001, retries=False)
        yield From(self.aioAssertRaises(ReadTimeoutError, pool.request, 'GET', url))

    @asyncio.coroutine
    def test_conn_closed(self):
        pool = HTTPConnectionPool(self.host, self.port, timeout=0.001, retries=False)
        conn = pool._get_conn()
        pool._put_conn(conn)
        try:
            url = '/sleep?seconds=0.005'
            yield From(pool.urlopen('GET', url))
            self.fail("The request should fail with a timeout error.")
        except ReadTimeoutError:
            if conn.sock:
                self.assertRaises(socket.error, conn.sock.recv, 1024)
        finally:
            pool._put_conn(conn)

    @asyncio.coroutine
    def test_nagle(self):
        """ Test that connections have TCP_NODELAY turned on """
        # This test needs to be here in order to be run. socket.create_connection actually tries to
        # connect to the host provided so we need a dummyserver to be running.
        pool = HTTPConnectionPool(self.host, self.port)
        conn = pool._get_conn()
        yield From(pool._make_request(conn, 'GET', '/'))
        tcp_nodelay_setting = conn.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        assert tcp_nodelay_setting > 0, ("Expected TCP_NODELAY to be set on the "
                                         "socket (with value greater than 0) "
                                         "but instead was %s" %
                                         tcp_nodelay_setting)

    def tst_socket_options(self):
        """Test that connections accept socket options."""
        # This test needs to be here in order to be run. socket.create_connection actually tries to
        # connect to the host provided so we need a dummyserver to be running.
        pool = HTTPConnectionPool(self.host, self.port, socket_options=[
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        ])
        s = pool._new_conn()._new_conn()  # Get the socket
        using_keepalive = s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) > 0
        self.assertTrue(using_keepalive)
        s.close()

    def tst_disable_default_socket_options(self):
        """Test that passing None disables all socket options."""
        # This test needs to be here in order to be run. socket.create_connection actually tries to
        # connect to the host provided so we need a dummyserver to be running.
        pool = HTTPConnectionPool(self.host, self.port, socket_options=None)
        s = pool._new_conn()._new_conn()
        using_nagle = s.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 0
        self.assertTrue(using_nagle)
        s.close()

    def tst_defaults_are_applied(self):
        """Test that modifying the default socket options works."""
        # This test needs to be here in order to be run. socket.create_connection actually tries to
        # connect to the host provided so we need a dummyserver to be running.
        pool = HTTPConnectionPool(self.host, self.port)
        # Get the HTTPConnection instance
        conn = pool._new_conn()
        # Update the default socket options
        conn.default_socket_options += [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
        s = conn._new_conn()
        nagle_disabled = s.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) > 0
        using_keepalive = s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) > 0
        self.assertTrue(nagle_disabled)
        self.assertTrue(using_keepalive)

    @timed(0.5)
    @asyncio.coroutine
    def test_timeout(self):
        """ Requests should time out when expected """
        url = '/sleep?seconds=0.002'
        timeout = Timeout(read=0.001)

        # Pool-global timeout
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout, retries=False)

        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ReadTimeoutError, pool._make_request,
                          conn, 'GET', url))
        pool._put_conn(conn)

        time.sleep(0.02) # Wait for server to start receiving again. :(

        yield From(self.aioAssertRaises(ReadTimeoutError, pool.request, 'GET', url))

        # Request-specific timeouts should raise errors
        pool = HTTPConnectionPool(self.host, self.port, timeout=0.1, retries=False)

        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ReadTimeoutError, pool._make_request,
                          conn, 'GET', url, timeout=timeout))
        pool._put_conn(conn)

        time.sleep(0.02) # Wait for server to start receiving again. :(

        yield From(self.aioAssertRaises(ReadTimeoutError, pool.request,
                          'GET', url, timeout=timeout))

        # Timeout int/float passed directly to request and _make_request should
        # raise a request timeout
        yield From(self.aioAssertRaises(ReadTimeoutError, pool.request,
                          'GET', url, timeout=0.001))
        conn = pool._new_conn()
        yield From(self.aioAssertRaises(ReadTimeoutError, pool._make_request, conn,
                          'GET', url, timeout=0.001))
        pool._put_conn(conn)

        # Timeout int/float passed directly to _make_request should not raise a
        # request timeout if it's a high value
        yield From(pool.request('GET', url, timeout=1))

    @requires_network
    @timed(0.5)
    @asyncio.coroutine
    def test_connect_timeout(self):
        url = '/sleep?seconds=0.005'
        timeout = Timeout(connect=0.001)

        # Pool-global timeout
        pool = HTTPConnectionPool(TARPIT_HOST, self.port, timeout=timeout)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ConnectTimeoutError, pool._make_request, conn, 'GET', url))

        # Retries
        retries = Retry(connect=0)
        yield From(self.aioAssertRaises(MaxRetryError, pool.request, 'GET', url,
                          retries=retries))

        # Request-specific connection timeouts
        big_timeout = Timeout(read=0.2, connect=0.2)
        pool = HTTPConnectionPool(TARPIT_HOST, self.port,
                                  timeout=big_timeout, retries=False)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ConnectTimeoutError, pool._make_request, conn, 'GET',
                          url, timeout=timeout))

        pool._put_conn(conn)
        yield From(self.aioAssertRaises(ConnectTimeoutError, pool.request, 'GET', url,
                          timeout=timeout))


    @asyncio.coroutine
    def test_connection_error_retries(self):
        """ ECONNREFUSED error should raise a connection error, with retries """
        port = find_unused_port()
        pool = HTTPConnectionPool(self.host, port)
        try:
            yield From(pool.request('GET', '/', retries=Retry(connect=3)))
            self.fail("Should have failed with a connection error.")
        except MaxRetryError as e:
            self.assertTrue(isinstance(e.reason, ProtocolError))
            self.assertEqual(e.reason.args[1].errno, errno.ECONNREFUSED)

    @asyncio.coroutine
    def test_timeout_reset(self):
        """ If the read timeout isn't set, socket timeout should reset """
        url = '/sleep?seconds=0.005'
        timeout = Timeout(connect=0.001)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        conn = pool._get_conn()
        try:
            yield From(pool._make_request(conn, 'GET', url))
        except ReadTimeoutError:
            self.fail("This request shouldn't trigger a read timeout.")

    @requires_network
    @timed(5.0)
    @asyncio.coroutine
    def test_total_timeout(self):
        url = '/sleep?seconds=0.005'

        timeout = Timeout(connect=3, read=5, total=0.001)
        pool = HTTPConnectionPool(TARPIT_HOST, self.port, timeout=timeout)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ConnectTimeoutError, pool._make_request, conn, 'GET', url))

        # This will get the socket to raise an EAGAIN on the read
        timeout = Timeout(connect=3, read=0)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ReadTimeoutError, pool._make_request, conn, 'GET', url))

        # The connect should succeed and this should hit the read timeout
        timeout = Timeout(connect=3, read=5, total=0.002)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ReadTimeoutError, pool._make_request, conn, 'GET', url))

    @requires_network
    @asyncio.coroutine
    def test_none_total_applies_connect(self):
        url = '/sleep?seconds=0.005'
        timeout = Timeout(total=None, connect=0.001)
        pool = HTTPConnectionPool(TARPIT_HOST, self.port, timeout=timeout)
        conn = pool._get_conn()
        yield From(self.aioAssertRaises(ConnectTimeoutError, pool._make_request, conn, 'GET', url))

    @asyncio.coroutine
    def test_timeout_success(self):
        timeout = Timeout(connect=3, read=5, total=None)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        yield From(pool.request('GET', '/'))
        # This should not raise a "Timeout already started" error
        yield From(pool.request('GET', '/'))

        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        # This should also not raise a "Timeout already started" error
        yield From(pool.request('GET', '/'))

        timeout = Timeout(total=None)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        yield From(pool.request('GET', '/'))

    @asyncio.coroutine
    def test_tunnel(self):
        # note the actual httplib.py has no tests for this functionality
        timeout = Timeout(total=None)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        conn = pool._get_conn()
        try:
            conn.set_tunnel(self.host, self.port)
        except AttributeError: # python 2.6
            conn._set_tunnel(self.host, self.port)

        conn._tunnel = mock.Mock(return_value=None)
        yield From(pool._make_request(conn, 'GET', '/'))
        conn._tunnel.assert_called_once_with()

        # test that it's not called when tunnel is not set
        timeout = Timeout(total=None)
        pool = HTTPConnectionPool(self.host, self.port, timeout=timeout)
        conn = pool._get_conn()

        conn._tunnel = mock.Mock(return_value=None)
        yield From(pool._make_request(conn, 'GET', '/'))
        self.assertEqual(conn._tunnel.called, False)

    @asyncio.coroutine
    def test_redirect(self):
        r = yield From(self.pool.request('GET', '/redirect', fields={'target': '/'}, redirect=False))
        self.assertEqual(r.status, 303)

        r = yield From(self.pool.request('GET', '/redirect', fields={'target': '/'}))
        self.assertEqual(r.status, 200)
        self.assertEqual((yield From(r.data)), b'Dummy server!')

    def tst_bad_connect(self):
        pool = HTTPConnectionPool('badhost.invalid', self.port)
        try:
            yield From(pool.request('GET', '/', retries=5))
            self.fail("should raise timeout exception here")
        except MaxRetryError as e:
            self.assertTrue(isinstance(e.reason, ProtocolError), e.reason)

    @asyncio.coroutine
    def test_keepalive(self):
        pool = HTTPConnectionPool(self.host, self.port, block=True, maxsize=1)

        r = yield From(pool.request('GET', '/keepalive?close=0'))
        r = yield From(pool.request('GET', '/keepalive?close=0'))

        self.assertEqual(r.status, 200)
        self.assertEqual(pool.num_connections, 1)
        self.assertEqual(pool.num_requests, 2)

    @asyncio.coroutine
    def test_keepalive_close(self):
        pool = HTTPConnectionPool(self.host, self.port,
                                  block=True, maxsize=1, timeout=2)

        r = yield From(pool.request('GET', '/keepalive?close=1', retries=0,
                                     headers={
                                         "Connection": "close",
                                     }))

        self.assertEqual(pool.num_connections, 1)

        # The dummyserver will have responded with Connection:close,
        # and httplib will properly cleanup the socket.

        # We grab the HTTPConnection object straight from the Queue,
        # because _get_conn() is where the check & reset occurs
        # pylint: disable-msg=W0212
        conn = pool.pool.get()
        self.assertEqual(conn.sock, None)
        pool._put_conn(conn)

        # Now with keep-alive
        r = yield From(pool.request('GET', '/keepalive?close=0', retries=0,
                                     headers={
                                         "Connection": "keep-alive",
                                     }))

        # The dummyserver responded with Connection:keep-alive, the connection
        # persists.
        conn = pool.pool.get()
        self.assertNotEqual(conn.sock, None)
        pool._put_conn(conn)

        # Another request asking the server to close the connection. This one
        # should get cleaned up for the next request.
        r = yield From(pool.request('GET', '/keepalive?close=1', retries=0,
                                     headers={
                                         "Connection": "close",
                                     }))

        self.assertEqual(r.status, 200)

        conn = pool.pool.get()
        self.assertEqual(conn.sock, None)
        pool._put_conn(conn)

        # Next request
        r = yield From(pool.request('GET', '/keepalive?close=0'))

    @asyncio.coroutine
    def test_post_with_urlencode(self):
        data = {'banana': 'hammock', 'lol': 'cat'}
        r = yield From(self.pool.request('POST', '/echo', fields=data, encode_multipart=False))
        self.assertEqual((yield From(r.data).decode('utf-8'), urlencode(data)))

    @asyncio.coroutine
    def test_post_with_multipart(self):
        data = {'banana': 'hammock', 'lol': 'cat'}
        r = yield From(self.pool.request('POST', '/echo',
                                    fields=data,
                                    encode_multipart=True))
        body = (yield From(r.data).split(b'\r\n'))

        encoded_data = encode_multipart_formdata(data)[0]
        expected_body = encoded_data.split(b'\r\n')

        # TODO: Get rid of extra parsing stuff when you can specify
        # a custom boundary to encode_multipart_formdata
        """
        We need to loop the return lines because a timestamp is attached
        from within encode_multipart_formdata. When the server echos back
        the data, it has the timestamp from when the data was encoded, which
        is not equivalent to when we run encode_multipart_formdata on
        the data again.
        """
        for i, line in enumerate(body):
            if line.startswith(b'--'):
                continue

            self.assertEqual(body[i], expected_body[i])

    @asyncio.coroutine
    def test_check_gzip(self):
        r = yield From(self.pool.request('GET', '/encodingrequest',
                                   headers={'accept-encoding': 'gzip'}))
        self.assertEqual(r.headers.get('content-encoding'), 'gzip')
        self.assertEqual((yield From(r.data)), b'hello, world!')

    @asyncio.coroutine
    def test_check_deflate(self):
        r = yield From(self.pool.request('GET', '/encodingrequest',
                                   headers={'accept-encoding': 'deflate'}))
        self.assertEqual(r.headers.get('content-encoding'), 'deflate')
        self.assertEqual((yield From(r.data)), b'hello, world!')

    @asyncio.coroutine
    def test_bad_decode(self):
        yield From(self.aioAssertRaises(DecodeError, self.pool.request,
                          'GET', '/encodingrequest',
                          headers={'accept-encoding': 'garbage-deflate'}))

        yield From(self.aioAssertRaises(DecodeError, self.pool.request,
                          'GET', '/encodingrequest',
                          headers={'accept-encoding': 'garbage-gzip'}))

    @asyncio.coroutine
    def test_connection_count(self):
        pool = HTTPConnectionPool(self.host, self.port, maxsize=1)

        yield From(pool.request('GET', '/'))
        yield From(pool.request('GET', '/'))
        yield From(pool.request('GET', '/'))

        self.assertEqual(pool.num_connections, 1)
        self.assertEqual(pool.num_requests, 3)

    @asyncio.coroutine
    def test_connection_count_bigpool(self):
        http_pool = HTTPConnectionPool(self.host, self.port, maxsize=16)

        yield From(http_pool.request('GET', '/'))
        yield From(http_pool.request('GET', '/'))
        yield From(http_pool.request('GET', '/'))

        self.assertEqual(http_pool.num_connections, 1)
        self.assertEqual(http_pool.num_requests, 3)

    @asyncio.coroutine
    def test_partial_response(self):
        pool = HTTPConnectionPool(self.host, self.port, maxsize=1)

        req_data = {'lol': 'cat'}
        resp_data = urlencode(req_data).encode('utf-8')

        r = yield From(pool.request('GET', '/echo', fields=req_data, preload_content=False))

        self.assertEqual(r.read(5), resp_data[:5])
        self.assertEqual(r.read(), resp_data[5:])

    @asyncio.coroutine
    def test_lazy_load_twice(self):
        # This test is sad and confusing. Need to figure out what's
        # going on with partial reads and socket reuse.

        pool = HTTPConnectionPool(self.host, self.port, block=True, maxsize=1, timeout=2)

        payload_size = 1024 * 2
        first_chunk = 512

        boundary = 'foo'

        req_data = {'count': 'a' * payload_size}
        resp_data = encode_multipart_formdata(req_data, boundary=boundary)[0]

        req2_data = {'count': 'b' * payload_size}
        resp2_data = encode_multipart_formdata(req2_data, boundary=boundary)[0]

        r1 = yield From(pool.request('POST', '/echo', fields=req_data, multipart_boundary=boundary, preload_content=False))

        self.assertEqual(r1.read(first_chunk), resp_data[:first_chunk])

        try:
            r2 = yield From(pool.request('POST', '/echo', fields=req2_data, multipart_boundary=boundary,
                                    preload_content=False, pool_timeout=0.001))

            # This branch should generally bail here, but maybe someday it will
            # work? Perhaps by some sort of magic. Consider it a TODO.

            self.assertEqual(r2.read(first_chunk), resp2_data[:first_chunk])

            self.assertEqual((yield From(r1.read()), resp_data[first_chunk:]))
            self.assertEqual((yield From(r2.read()), resp2_data[first_chunk:]))
            self.assertEqual(pool.num_requests, 2)

        except EmptyPoolError:
            self.assertEqual((yield From(r1.read()), resp_data[first_chunk:]))
            self.assertEqual(pool.num_requests, 1)

        self.assertEqual(pool.num_connections, 1)

    @asyncio.coroutine
    def test_for_double_release(self):
        MAXSIZE=5

        # Check default state
        pool = HTTPConnectionPool(self.host, self.port, maxsize=MAXSIZE)
        self.assertEqual(pool.num_connections, 0)
        self.assertEqual(pool.pool.qsize(), MAXSIZE)

        # Make an empty slot for testing
        pool.pool.get()
        self.assertEqual(pool.pool.qsize(), MAXSIZE-1)

        # Check state after simple request
        yield From(pool.urlopen('GET', '/'))
        self.assertEqual(pool.pool.qsize(), MAXSIZE-1)

        # Check state without release
        yield From(pool.urlopen('GET', '/', preload_content=False))
        self.assertEqual(pool.pool.qsize(), MAXSIZE-2)

        yield From(pool.urlopen('GET', '/'))
        self.assertEqual(pool.pool.qsize(), MAXSIZE-2)

        # Check state after read
        yield From(pool.urlopen('GET', '/').data)
        self.assertEqual(pool.pool.qsize(), MAXSIZE-2)

        yield From(pool.urlopen('GET', '/'))
        self.assertEqual(pool.pool.qsize(), MAXSIZE-2)

    @asyncio.coroutine
    def test_release_conn_parameter(self):
        MAXSIZE=5
        pool = HTTPConnectionPool(self.host, self.port, maxsize=MAXSIZE)
        self.assertEqual(pool.pool.qsize(), MAXSIZE)

        # Make request without releasing connection
        yield From(pool.request('GET', '/', release_conn=False, preload_content=False))
        self.assertEqual(pool.pool.qsize(), MAXSIZE-1)

    @asyncio.coroutine
    def test_dns_error(self):
        pool = HTTPConnectionPool('thishostdoesnotexist.invalid', self.port, timeout=0.001)
        yield From(self.aioAssertRaises(MaxRetryError, pool.request, 'GET', '/test', retries=2))

    @asyncio.coroutine
    def test_source_address(self):
        for addr in VALID_SOURCE_ADDRESSES:
            pool = HTTPConnectionPool(self.host, self.port, source_address=addr, retries=False)
            r = yield From(pool.request('GET', '/source_address'))
            assert (yield From(r.data) == b(addr[0]), (
                "expected the response to contain the source address {addr}, "
                "but was {data}".format(data=(yield From(r.data)), addr=b(addr[0]))))

    @asyncio.coroutine
    def test_source_address_error(self):
        for addr in INVALID_SOURCE_ADDRESSES:
            pool = HTTPConnectionPool(self.host, self.port,
                    source_address=addr, retries=False)
            yield From(self.aioAssertRaises(ProtocolError,
                    pool.request, 'GET', '/source_address'))

    @asyncio.coroutine
    def test_httplib_headers_case_insensitive(self):
        HEADERS = {'Content-Length': '0', 'Content-type': 'text/plain',
                    'Server': 'TornadoServer/%s' % tornado.version}
        r = yield From(self.pool.request('GET', '/specific_method',
                               fields={'method': 'GET'}))
        self.assertEqual(HEADERS, dict(r.headers.items())) # to preserve case sensitivity


class TestRetry(HTTPDummyServerTestCase):
    def setUp(self):
        self.pool = HTTPConnectionPool(self.host, self.port)

    @asyncio.coroutine
    def test_max_retry(self):
        try:
            r = yield From(self.pool.request('GET', '/redirect',
                                              fields={'target': '/'},
                                              retries=0))
            self.fail("Failed to raise MaxRetryError exception, returned %r" % r.status)
        except MaxRetryError:
            pass

    @asyncio.coroutine
    def test_disabled_retry(self):
        """ Disabled retries should disable redirect handling. """
        r = yield From(self.pool.request('GET', '/redirect',
                                          fields={'target': '/'},
                                          retries=False))
        self.assertEqual(r.status, 303)

        r = yield From(self.pool.request('GET', '/redirect',
                                          fields={'target': '/'},
                                          retries=Retry(redirect=False)))
        self.assertEqual(r.status, 303)

        # don't pass for orig
        #pool = HTTPConnectionPool('thishostdoesnotexist.invalid', self.port, timeout=0.001)
        #self.assertRaises(ProtocolError, pool.request, 'GET', '/test', retries=False)

    @asyncio.coroutine
    def test_read_retries(self):
        """ Should retry for status codes in the whitelist """
        retry = Retry(read=1, status_forcelist=[418])
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                             headers={'test-name': 'test_read_retries'},
                                             retries=retry))
        self.assertEqual(resp.status, 200)

    @asyncio.coroutine
    def test_read_total_retries(self):
        """ HTTP response w/ status code in the whitelist should be retried """
        headers = {'test-name': 'test_read_total_retries'}
        retry = Retry(total=1, status_forcelist=[418])
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                            headers=headers, retries=retry))
        self.assertEqual(resp.status, 200)

    @asyncio.coroutine
    def test_retries_wrong_whitelist(self):
        """HTTP response w/ status code not in whitelist shouldn't be retried"""
        retry = Retry(total=1, status_forcelist=[202])
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                             headers={'test-name': 'test_wrong_whitelist'},
                                             retries=retry))
        self.assertEqual(resp.status, 418)

    @asyncio.coroutine
    def test_default_method_whitelist_retried(self):
        """ urllib3 should retry methods in the default method whitelist """
        retry = Retry(total=1, status_forcelist=[418])
        resp = yield From(self.pool.request('OPTIONS', '/successful_retry',
                                             headers={'test-name': 'test_default_whitelist'},
                                             retries=retry))
        self.assertEqual(resp.status, 200)

    @asyncio.coroutine
    def test_retries_wrong_method_list(self):
        """Method not in our whitelist should not be retried, even if code matches"""
        headers = {'test-name': 'test_wrong_method_whitelist'}
        retry = Retry(total=1, status_forcelist=[418],
                      method_whitelist=['POST'])
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                 headers=headers, retries=retry))
        self.assertEqual(resp.status, 418)

    @asyncio.coroutine
    def test_read_retries_unsuccessful(self):
        headers = {'test-name': 'test_read_retries_unsuccessful'}
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                 headers=headers, retries=1))
        self.assertEqual(resp.status, 418)

    @asyncio.coroutine
    def test_retry_reuse_safe(self):
        """ It should be possible to reuse a Retry object across requests """
        headers = {'test-name': 'test_retry_safe'}
        retry = Retry(total=1, status_forcelist=[418])
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                 headers=headers, retries=retry))
        self.assertEqual(resp.status, 200)
        resp = yield From(self.pool.request('GET', '/successful_retry',
                                 headers=headers, retries=retry))
        self.assertEqual(resp.status, 200)


if __name__ == '__main__':
    unittest.main()
