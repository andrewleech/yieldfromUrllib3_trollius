
import os
import unittest
import functools
import trollius as asyncio
from trollius import From, Return
import sys
sys.path.append('../../')

from urllib.parse import urljoin
from yieldfrom_t.urllib3.connectionpool import HTTPConnectionPool
from yieldfrom_t.urllib3.poolmanager import PoolManager
from yieldfrom_t.urllib3 import connection_from_url
from yieldfrom_t.urllib3.exceptions import (
    ClosedPoolError,
    LocationValueError,
    EmptyPoolError,
)

# Requests to this URL should always fail with a connection timeout (nothing
# listening on that port)
HTTPBIN = os.environ.get('HTTPBIN_URL', 'http://httpbin.org/')
# Issue #1483: Make sure the URL always has a trailing slash
HTTPBIN = HTTPBIN.rstrip('/') + '/'


def httpbin(*suffix):
    """Returns url for HTTPBIN resource."""
    return urljoin(HTTPBIN, '/'.join(suffix))


class RequestsTestCase(unittest.TestCase):

    def setUp(self):
        """Create simple data set with headers."""
        pass

    def tearDown(self):
        """Teardown."""
        pass

    def test_queue_overload(self):

        http = HTTPConnectionPool('httpbin.org', maxsize=3, block=True, timeout=3)

        testLoop = asyncio.get_event_loop()
        testLoop.set_debug(True)
        global test_queue_overload_count
        test_queue_overload_count = 0

        @asyncio.coroutine
        def get_page():
            global test_queue_overload_count
            try:
                resp = yield From(http.request('GET', '/delay/1', pool_timeout=3))
                pg = yield From(resp.data)
                self.assertTrue(b'Connection' in pg, pg)
            except EmptyPoolError:
                pass
            except Exception as e:
                raise
            else:
                test_queue_overload_count += 1

        pageGetters = [get_page(), get_page(), get_page(), get_page(), get_page()]
        testLoop.run_until_complete(asyncio.wait(pageGetters, return_when=asyncio.ALL_COMPLETED))
        self.assertGreater(test_queue_overload_count, 4, 'not all page_getters ran')



if __name__ == '__main__':
    unittest.main()