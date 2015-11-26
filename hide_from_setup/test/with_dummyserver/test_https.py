import datetime
import logging
import ssl
import unittest
import warnings
import trollius as asyncio
from trollius import From, Return
import functools

import mock
from nose.plugins.skip import SkipTest

import sys
from hide_from_setup.dummyserver.testcase import HTTPSDummyServerTestCase
from hide_from_setup.dummyserver.server import DEFAULT_CA, DEFAULT_CA_BAD, DEFAULT_CERTS

sys.path.extend(['..', '../..', '../../../'])

from tst_stuff import (
    requires_network,
    TARPIT_HOST,
)

from yieldfrom_t.urllib3 import HTTPSConnectionPool
from yieldfrom_t.urllib3.connection import (
    VerifiedHTTPSConnection,
    UnverifiedHTTPSConnection,
    RECENT_DATE,
)
from yieldfrom_t.urllib3.exceptions import (
    SSLError,
    ReadTimeoutError,
    ConnectTimeoutError,
    InsecureRequestWarning,
    MaxRetryError,
    SystemTimeWarning,
)
from yieldfrom_t.urllib3.util.timeout import Timeout

log = logging.getLogger('urllib3.connectionpool')
log.setLevel(logging.NOTSET)
log.addHandler(logging.StreamHandler(sys.stdout))

def async_test(f):

    testLoop = asyncio.get_event_loop()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test


class TestHTTPS(HTTPSDummyServerTestCase):

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

    def setUp(self):
        self._pool = HTTPSConnectionPool(self.host, self.port)

    @async_test
    def test_simple(self):
        r = yield From(self._pool.request('GET', '/'))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @async_test
    def test_set_ssl_version_to_tlsv1(self):
        self._pool.ssl_version = ssl.PROTOCOL_TLSv1
        r = yield From(self._pool.request('GET', '/'))
        self.assertEqual(r.status, 200, (yield From(r.data)))

    @async_test
    def test_verified(self):
        https_pool = HTTPSConnectionPool(self.host, self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        conn = https_pool._new_conn()
        self.assertEqual(conn.__class__, VerifiedHTTPSConnection)

        with mock.patch('warnings.warn') as warn:
            r = yield From(https_pool.request('GET', '/'))
            self.assertEqual(r.status, 200)
            self.assertTrue(len(warn.call_args_list)==0, warn.call_args_list)
            #self.assertTrue(warn.call_args_list[0].startswith('call('), warn.call_args_list)

    @async_test
    def test_invalid_common_name(self):
        https_pool = HTTPSConnectionPool('127.0.0.1', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)
        try:
            yield From(https_pool.request('GET', '/'))
            self.fail("Didn't raise SSL invalid common name")
        except SSLError as e:
            self.assertTrue("doesn't match" in str(e))

    @async_test
    def test_verified_with_bad_ca_certs(self):
        https_pool = HTTPSConnectionPool(self.host, self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA_BAD)

        try:
            yield From(https_pool.request('GET', '/'))
            self.fail("Didn't raise SSL error with bad CA certs")
        except (SSLError, MaxRetryError, ConnectTimeoutError) as e:
            pass

    @async_test
    def test_verified_without_ca_certs(self):
        # default is cert_reqs=None which is ssl.CERT_NONE
        https_pool = HTTPSConnectionPool(self.host, self.port,
                                         cert_reqs='CERT_REQUIRED')

        try:
            yield From(https_pool.request('GET', '/'))
            self.fail("Didn't raise SSL error with no CA certs when"
                      "CERT_REQUIRED is set")
        except (SSLError, MaxRetryError, ConnectTimeoutError) as e:
            # there is a different error message depending on whether or
            # not pyopenssl is injected
            pass

    @async_test
    def test_no_ssl(self):
        pool = HTTPSConnectionPool(self.host, self.port)
        pool.ConnectionCls = None
        self.assertRaises(SSLError, pool._new_conn)
        yield From(self.aioAssertRaises(SSLError, pool.request, 'GET', '/'))

    @async_test
    def test_unverified_ssl(self):
        """ Test that bare HTTPSConnection can connect, make requests """
        pool = HTTPSConnectionPool(self.host, self.port)
        pool.ConnectionCls = UnverifiedHTTPSConnection

        with mock.patch('warnings.warn') as warn:
            r = yield From(pool.request('GET', '/'))
            self.assertEqual(r.status, 200)
            self.assertTrue(warn.called)

            call, = warn.call_args_list
            category = call[0][1]
            self.assertEqual(category, InsecureRequestWarning)

    @async_test
    def test_ssl_unverified_with_ca_certs(self):
        pool = HTTPSConnectionPool(self.host, self.port,
                                   cert_reqs='CERT_NONE',
                                   ca_certs=DEFAULT_CA_BAD)

        with mock.patch('warnings.warn') as warn:
            r = yield From(pool.request('GET', '/'))
            self.assertEqual(r.status, 200)
            self.assertTrue(warn.called)

            call, = warn.call_args_list
            category = call[0][1]
            self.assertEqual(category, InsecureRequestWarning)

    @requires_network
    @async_test
    def tst_ssl_verified_with_platform_ca_certs(self):
        """
        We should rely on the platform CA file to validate authenticity of SSL
        certificates. Since this file is used by many components of the OS,
        such as curl, apt-get, etc., we decided to not touch it, in order to
        not compromise the security of the OS running the test suite (typically
        urllib3 developer's OS).

        This test assumes that httpbin.org uses a certificate signed by a well
        known Certificate Authority.
        """
        try:
            import urllib3.contrib.pyopenssl
        except ImportError:
            raise SkipTest('Test requires PyOpenSSL')
        if (urllib3.connection.ssl_wrap_socket is
                urllib3.contrib.pyopenssl.orig_connection_ssl_wrap_socket):
            # Not patched
            raise SkipTest('Test should only be run after PyOpenSSL '
                           'monkey patching')

        https_pool = HTTPSConnectionPool('httpbin.org', 443,
                                         cert_reqs=ssl.CERT_REQUIRED)

        yield From(https_pool.request('HEAD', '/'))

    @async_test
    def test_assert_hostname_false(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        https_pool.assert_hostname = False
        yield From(https_pool.request('GET', '/'))

    @async_test
    def test_assert_specific_hostname(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        https_pool.assert_hostname = 'localhost'
        yield From(https_pool.request('GET', '/'))

    @async_test
    def tst_assert_fingerprint_md5(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        https_pool.assert_fingerprint = 'CA:84:E1:AD0E5a:ef:2f:C3:09' \
                                        ':E7:30:F8:CD:C8:5B'
        yield From(https_pool.request('GET', '/'))

    @async_test
    def test_assert_fingerprint_sha1(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        https_pool.assert_fingerprint = 'CC:45:6A:90:82:F7FF:C0:8218:8e:' \
                                        '7A:F2:8A:D7:1E:07:33:67:DE'
        yield From(https_pool.request('GET', '/'))

    @async_test
    def test_assert_invalid_fingerprint(self):

        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_REQUIRED',
                                         ca_certs=DEFAULT_CA)

        https_pool.assert_fingerprint = 'AA:AA:AA:AA:AA:AAAA:AA:AAAA:AA:' \
                                        'AA:AA:AA:AA:AA:AA:AA:AA:AA'

        yield From(self.aioAssertRaises(SSLError, https_pool.request, 'GET', '/'))
        https_pool._get_conn()

        # Uneven length
        https_pool.assert_fingerprint = 'AA:A'
        yield From(self.aioAssertRaises(SSLError, https_pool.request, 'GET', '/'))
        https_pool._get_conn()

        # Invalid length
        https_pool.assert_fingerprint = 'AA'
        yield From(self.aioAssertRaises(SSLError, https_pool.request, 'GET', '/'))

    @async_test
    def test_verify_none_and_bad_fingerprint(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_NONE',
                                         ca_certs=DEFAULT_CA_BAD)

        https_pool.assert_fingerprint = 'AA:AA:AA:AA:AA:AAAA:AA:AAAA:AA:' \
                                        'AA:AA:AA:AA:AA:AA:AA:AA:AA'
        yield From(self.aioAssertRaises(SSLError, https_pool.request, 'GET', '/'))

    @async_test
    def test_verify_none_and_good_fingerprint(self):
        https_pool = HTTPSConnectionPool('localhost', self.port,
                                         cert_reqs='CERT_NONE',
                                         ca_certs=DEFAULT_CA_BAD)

        https_pool.assert_fingerprint = 'CC:45:6A:90:82:F7FF:C0:8218:8e:' \
                                        '7A:F2:8A:D7:1E:07:33:67:DE'
        yield From(https_pool.request('GET', '/'))

    @requires_network
    @async_test
    def tst_https_timeout(self):
        timeout = Timeout(connect=0.001)
        https_pool = HTTPSConnectionPool(TARPIT_HOST, self.port,
                                          timeout=timeout, retries=False,
                                          cert_reqs='CERT_REQUIRED')

        timeout = Timeout(total=None, connect=0.001)
        https_pool = HTTPSConnectionPool(TARPIT_HOST, self.port,
                                         timeout=timeout, retries=False,
                                         cert_reqs='CERT_REQUIRED')
        yield From(self.aioAssertRaises(ConnectTimeoutError, https_pool.request, 'GET', '/'))

        timeout = Timeout(read=0.001)
        https_pool = HTTPSConnectionPool(self.host, self.port,
                                         timeout=timeout, retries=False,
                                         cert_reqs='CERT_REQUIRED')
        https_pool.ca_certs = DEFAULT_CA
        https_pool.assert_fingerprint = 'CC:45:6A:90:82:F7FF:C0:8218:8e:' \
                                        '7A:F2:8A:D7:1E:07:33:67:DE'
        url = '/sleep?seconds=0.005'
        try:
            yield From(https_pool.request('GET', url))
        except ReadTimeoutError as e:
            pass
        else:
            self.fail('ReadTimeoutError was not raised')

        timeout = Timeout(total=None)
        https_pool = HTTPSConnectionPool(self.host, self.port, timeout=timeout,
                                         cert_reqs='CERT_NONE')
        yield From(https_pool.request('GET', '/'))

    @async_test
    def test_tunnel(self):
        """ test the _tunnel behavior """
        timeout = Timeout(total=None)
        https_pool = HTTPSConnectionPool(self.host, self.port, timeout=timeout,
                                         cert_reqs='CERT_NONE')
        conn = https_pool._new_conn()
        try:
            conn.set_tunnel(self.host, self.port)
        except AttributeError: # python 2.6
            conn._set_tunnel(self.host, self.port)
        conn._tunnel = mock.Mock(return_value=iter([None, None, None]))
        yield From(https_pool._make_request(conn, 'GET', '/'))
        conn._tunnel.assert_called_once_with()

    @requires_network
    @async_test
    def tst_enhanced_timeout(self):
        def new_pool(timeout, cert_reqs='CERT_REQUIRED'):
            https_pool = HTTPSConnectionPool(TARPIT_HOST, self.port,
                                             timeout=timeout,
                                             retries=False,
                                             cert_reqs=cert_reqs)
            return https_pool

        https_pool = new_pool(Timeout(connect=0.001))
        conn = https_pool._new_conn()
        try:
            yield From(https_pool.request('GET', '/'))
        except ConnectTimeoutError as e:
            pass
        except Exception as e:
            pass
        except:
            pass
        else:
            self.fail('connect timeout error not raised')
        yield From(self.aioAssertRaises(ConnectTimeoutError, https_pool._make_request, conn, 'GET', '/'))

        https_pool = new_pool(Timeout(connect=5))
        yield From(self.aioAssertRaises(ConnectTimeoutError, https_pool.request, 'GET', '/',
                          timeout=Timeout(connect=0.001)))

        t = Timeout(total=None)
        https_pool = new_pool(t)
        conn = https_pool._new_conn()
        yield From(self.aioAssertRaises(ConnectTimeoutError, https_pool.request, 'GET', '/',
                          timeout=Timeout(total=None, connect=0.001)))

    @async_test
    def test_enhanced_ssl_connection(self):
        fingerprint = 'CC:45:6A:90:82:F7FF:C0:8218:8e:7A:F2:8A:D7:1E:07:33:67:DE'

        conn = VerifiedHTTPSConnection(self.host, self.port)
        https_pool = HTTPSConnectionPool(self.host, self.port,
                cert_reqs='CERT_REQUIRED', ca_certs=DEFAULT_CA,
                assert_fingerprint=fingerprint)

        https_pool._make_request(conn, 'GET', '/')

    @async_test
    def tst_ssl_correct_system_time(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            yield From(self._pool.request('GET', '/'))

        self.assertEqual([], w)

    @async_test
    def tst_ssl_wrong_system_time(self):
        with mock.patch('urllib3.connection.datetime') as mock_date:
            mock_date.date.today.return_value = datetime.date(1970, 1, 1)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter('always')
                yield From(self._pool.request('GET', '/'))

            self.assertEqual(len(w), 1)
            warning = w[0]

            self.assertEqual(SystemTimeWarning, warning.category)
            self.assertTrue(str(RECENT_DATE) in warning.message.args[0])


class TestHTTPS_TLSv1(HTTPSDummyServerTestCase):
    certs = DEFAULT_CERTS.copy()
    certs['ssl_version'] = ssl.PROTOCOL_TLSv1

    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        else:
            self.fail('expected %s not raised' % exc.__name__)

    def setUp(self):
        self._pool = HTTPSConnectionPool(self.host, self.port)

    @async_test
    def test_set_ssl_version_to_sslv3(self):
        self._pool.ssl_version = ssl.PROTOCOL_SSLv3
        yield From(self.aioAssertRaises((SSLError, ConnectTimeoutError, MaxRetryError), self._pool.request, 'GET', '/'))

    @async_test
    def test_ssl_version_as_string(self):
        self._pool.ssl_version = 'PROTOCOL_SSLv3'
        yield From(self.aioAssertRaises((SSLError, ConnectTimeoutError, MaxRetryError), self._pool.request, 'GET', '/'))

    @async_test
    def test_ssl_version_as_short_string(self):
        self._pool.ssl_version = 'SSLv3'
        yield From(self.aioAssertRaises((SSLError, ConnectTimeoutError, MaxRetryError), self._pool.request, 'GET', '/'))

    # def test_fail(self):
    #     self.assertTrue(False, 'duh')


if __name__ == '__main__':
    unittest.main()
