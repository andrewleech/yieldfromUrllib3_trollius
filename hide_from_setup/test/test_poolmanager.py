import unittest
import trollius as asyncio
from trollius import From, Return
import sys
sys.path.append('../../')

from yieldfrom_t.urllib3.poolmanager import PoolManager
from yieldfrom_t.urllib3 import connection_from_url
from yieldfrom_t.urllib3.exceptions import (
    ClosedPoolError,
    LocationValueError,
)


class TestPoolManager(unittest.TestCase):

    @asyncio.coroutine
    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        else:
            raise Exception('expected %s not raised' % exc.__name__)


    def test_same_url(self):
        # Convince ourselves that normally we don't get the same object
        conn1 = connection_from_url('http://localhost:8081/foo')
        conn2 = connection_from_url('http://localhost:8081/bar')

        self.assertNotEqual(conn1, conn2)

        # Now try again using the PoolManager
        p = PoolManager(1)

        conn1 = p.connection_from_url('http://localhost:8081/foo')
        conn2 = p.connection_from_url('http://localhost:8081/bar')

        self.assertEqual(conn1, conn2)

    def test_many_urls(self):
        urls = [
            "http://localhost:8081/foo",
            "http://www.google.com/mail",
            "http://localhost:8081/bar",
            "https://www.google.com/",
            "https://www.google.com/mail",
            "http://yahoo.com",
            "http://bing.com",
            "http://yahoo.com/",
        ]

        connections = set()

        p = PoolManager(10)

        for url in urls:
            conn = p.connection_from_url(url)
            connections.add(conn)

        self.assertEqual(len(connections), 5)

    def test_manager_clear(self):

        p = PoolManager(5)

        conn_pool = p.connection_from_url('http://google.com')
        self.assertEqual(len(p.pools), 1)

        conn = conn_pool._get_conn()

        p.clear()
        self.assertEqual(len(p.pools), 0)

        self.aioAssertRaises(ClosedPoolError, conn_pool._get_conn)

        conn_pool._put_conn(conn)

        self.aioAssertRaises(ClosedPoolError, conn_pool._get_conn)

        self.assertEqual(len(p.pools), 0)


    def test_nohost(self):
        p = PoolManager(5)
        self.assertRaises(LocationValueError, p.connection_from_url, 'http://@')
        self.assertRaises(LocationValueError, p.connection_from_url, None)


if __name__ == '__main__':
    unittest.main()
