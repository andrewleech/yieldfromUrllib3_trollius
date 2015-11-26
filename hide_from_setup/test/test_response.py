import unittest
import trollius as asyncio
from trollius import From, Return
import functools
from io import BytesIO, BufferedReader

import sys
sys.path.append('../../')

from yieldfrom_t.urllib3.response import HTTPResponse
from yieldfrom_t.urllib3.exceptions import DecodeError


from base64 import b64decode

# A known random (i.e, not-too-compressible) payload generated with:
#    "".join(random.choice(string.printable) for i in xrange(512))
#    .encode("zlib").encode("base64")
# Randomness in tests == bad, and fixing a seed may not be sufficient.
ZLIB_PAYLOAD = b64decode(b"""\
eJwFweuaoQAAANDfineQhiKLUiaiCzvuTEmNNlJGiL5QhnGpZ99z8luQfe1AHoMioB+QSWHQu/L+
lzd7W5CipqYmeVTBjdgSATdg4l4Z2zhikbuF+EKn69Q0DTpdmNJz8S33odfJoVEexw/l2SS9nFdi
pis7KOwXzfSqarSo9uJYgbDGrs1VNnQpT9f8zAorhYCEZronZQF9DuDFfNK3Hecc+WHLnZLQptwk
nufw8S9I43sEwxsT71BiqedHo0QeIrFE01F/4atVFXuJs2yxIOak3bvtXjUKAA6OKnQJ/nNvDGKZ
Khe5TF36JbnKVjdcL1EUNpwrWVfQpFYJ/WWm2b74qNeSZeQv5/xBhRdOmKTJFYgO96PwrHBlsnLn
a3l0LwJsloWpMbzByU5WLbRE6X5INFqjQOtIwYz5BAlhkn+kVqJvWM5vBlfrwP42ifonM5yF4ciJ
auHVks62997mNGOsM7WXNG3P98dBHPo2NhbTvHleL0BI5dus2JY81MUOnK3SGWLH8HeWPa1t5KcW
S5moAj5HexY/g/F8TctpxwsvyZp38dXeLDjSQvEQIkF7XR3YXbeZgKk3V34KGCPOAeeuQDIgyVhV
nP4HF2uWHA==""")


def async_test(f):

    testLoop = asyncio.get_event_loop()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test



class TestLegacyResponse(unittest.TestCase):
    def test_getheaders(self):
        headers = {'host': 'example.com'}
        r = HTTPResponse(headers=headers)
        self.assertEqual(r.getheaders(), headers)

    def test_getheader(self):
        headers = {'host': 'example.com'}
        r = HTTPResponse(headers=headers)
        self.assertEqual(r.getheader('host'), 'example.com')


class TestResponse(unittest.TestCase):

    @asyncio.coroutine
    def aioAssertRaises(self, exc, f, *args, **kwargs):
        """tests a coroutine for whether it raises given error."""
        try:
            yield From(f(*args, **kwargs))
        except exc as e:
            pass
        else:
            raise Exception('expected %s not raised' % exc.__name__)

    def _fake_fp(self, data):
        fp = asyncio.StreamReader()
        fp.feed_data(data)
        fp.feed_eof()
        return fp

    @async_test
    def test_cache_content(self):
        r = HTTPResponse('foo')
        _d = yield From(r.data)
        self.assertEqual(_d, 'foo')
        self.assertEqual(r._body, 'foo')

    @async_test
    def test_default(self):
        r = HTTPResponse()
        self.assertEqual((yield From(r.data)), None)

    @async_test
    def test_none(self):
        r = HTTPResponse(None)
        _d = yield From(r.data)
        self.assertEqual(_d, None)

    @async_test
    def test_preload(self):
        fp = self._fake_fp(b'foo')

        r = HTTPResponse(fp, preload_content=True)

        #self.assertEqual(fp.tell(), len(b'foo'))
        self.assertEqual((yield From(r.data)), b'foo')

    @async_test
    def test_no_preload(self):
        fp = self._fake_fp(b'foo')

        r = HTTPResponse(fp, preload_content=False)

        #self.assertEqual(fp.tell(), 0)
        _d = yield From(r.data)
        self.assertEqual(_d, b'foo')
        #self.assertEqual(fp.tell(), len(b'foo'))

    @async_test
    def test_decode_bad_data(self):
        fp = asyncio.StreamReader()
        fp.feed_data(b'\x00' * 10)
        fp.feed_eof()
        t = HTTPResponse(fp, headers={'content-encoding': 'deflate'})
        yield From(self.aioAssertRaises(DecodeError, t.init))

    @async_test
    def test_decode_deflate(self):
        import zlib
        data = zlib.compress(b'foo')

        fp = self._fake_fp(data)
        r = HTTPResponse(fp, headers={'content-encoding': 'deflate'})

        self.assertEqual((yield From(r.data)), b'foo')

    @async_test
    def test_decode_deflate_case_insensitve(self):
        import zlib
        data = zlib.compress(b'foo')

        fp = self._fake_fp(data)
        r = HTTPResponse(fp, headers={'content-encoding': 'DeFlAtE'})

        self.assertEqual((yield From(r.data)), b'foo')

    @async_test
    def test_chunked_decoding_deflate(self):
        import zlib
        data = zlib.compress(b'foo')

        fp = asyncio.StreamReader()
        fp.feed_data(data)
        r = HTTPResponse(fp, headers={'content-encoding': 'deflate'},
                         preload_content=False)
        #yield From(r.init())
        _d1 = yield From(r.read(3))
        _d2 = yield From(r.read(1))
        _d3 = yield From(r.read(2))
        self.assertEqual(_d1, b'')
        self.assertEqual(_d2, b'f')
        self.assertEqual(_d3, b'oo')

    @async_test
    def tst_chunked_decoding_deflate2(self):
        import zlib
        compress = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
        data = compress.compress(b'foo')
        data += compress.flush()

        fp = asyncio.StreamReader()
        fp.feed_data(data)
        r = HTTPResponse(fp, headers={'content-encoding': 'deflate'},
                         preload_content=False)

        yield From(r.init())
        _d1 = yield From(r.read(3))
        self.assertEqual(_d1, b'')
        _d2 = yield From(r.read(1))
        self.assertEqual(_d2, b'f')
        _d3 = yield From(r.read(2))
        self.assertEqual(_d3, b'oo')

    @async_test
    def test_chunked_decoding_gzip(self):
        import zlib
        compress = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        data = compress.compress(b'foo')
        data += compress.flush()

        fp = asyncio.StreamReader()
        fp.feed_data(data)
        r = HTTPResponse(fp, headers={'content-encoding': 'gzip'},
                         preload_content=False)

        yield From(r.init())
        _d1 = yield From(r.read(10))
        self.assertEqual(_d1, b'')
        _d2 = yield From(r.read(5))
        self.assertEqual(_d2, b'foo')
        _d3 = yield From(r.read(2))
        self.assertEqual(_d3, b'')

    @async_test
    def test_body_blob(self):
        resp = HTTPResponse(b'foo')
        _d = yield From(resp.data)
        self.assertEqual(_d, b'foo')
        self.assertTrue(resp.closed)

    @async_test
    def tst_io(self):
        import socket
        from yieldfrom_t.http.client import HTTPResponse as OldHTTPResponse

        fp = self._fake_fp(b'foo')
        #fp = BytesIO(b'foo')
        resp = HTTPResponse(fp, preload_content=False)

        self.assertEqual(resp.closed, False)
        self.assertEqual(resp.readable(), True)
        self.assertEqual(resp.writable(), False)
        self.assertRaises(IOError, resp.fileno)

        resp.close()
        self.assertEqual(resp.closed, True)

        # Try closing with an `httplib.HTTPResponse`, because it has an
        # `isclosed` method.
        hlr = OldHTTPResponse(socket.socket())
        resp2 = HTTPResponse(hlr, preload_content=False)
        self.assertEqual(resp2.closed, False)
        resp2.close()
        self.assertEqual(resp2.closed, True)

        #also try when only data is present.
        resp3 = HTTPResponse('foodata')
        self.assertRaises(IOError, resp3.fileno)

        resp3._fp = 2
        # A corner case where _fp is present but doesn't have `closed`,
        # `isclosed`, or `fileno`.  Unlikely, but possible.
        self.assertEqual(resp3.closed, True)
        self.assertRaises(IOError, resp3.fileno)

    def tst_io_bufferedreader(self):

        fp = self._fake_fp(b'foo')
        #fp = BytesIO(b'foo')
        resp = HTTPResponse(fp, preload_content=False)
        br = BufferedReader(resp)

        self.assertEqual(br.read(), b'foo')

        br.close()
        self.assertEqual(resp.closed, True)

        b = b'fooandahalf'
        fp = self._fake_fp(b)
        #fp = BytesIO(b)
        resp = HTTPResponse(fp, preload_content=False)
        br = BufferedReader(resp, 5)

        br.read(1)  # sets up the buffer, reading 5
        self.assertEqual(len(fp.read()), len(b) - 5)

        # This is necessary to make sure the "no bytes left" part of `readinto`
        # gets tested.
        while not br.closed:
            br.read(5)

    @async_test
    def test_io_readinto(self):
        # This test is necessary because in py2.6, `readinto` doesn't get called
        # in `test_io_bufferedreader` like it does for all the other python
        # versions.  Probably this is because the `io` module in py2.6 is an
        # old version that has a different underlying implementation.

        fp = self._fake_fp(b'foo')
        #fp = BytesIO(b'foo')
        resp = HTTPResponse(fp, preload_content=False)

        barr = bytearray(3)
        amtRead = yield From(resp.readinto(barr))
        assert amtRead == 3
        assert b'foo' == barr

        # The reader should already be empty, so this should read nothing.
        amtRead = yield From(resp.readinto(barr))
        assert amtRead == 0
        assert b'foo' == barr

    def test_streaming(self):
        fp = BytesIO(b'foo')
        resp = HTTPResponse(fp, preload_content=False)
        stream = yield From(resp.stream(2, decode_content=False))

        self.assertEqual(next(stream), b'fo')
        self.assertEqual(next(stream), b'o')
        self.assertRaises(StopIteration, next, stream)

    def test_streaming_tell(self):

        fp = self._fake_fp(b'foo')
        #fp = BytesIO(b'foo')
        resp = HTTPResponse(fp, preload_content=False)
        stream = yield From(resp.stream(2, decode_content=False))

        position = 0

        position += len(next(stream))
        self.assertEqual(2, position)
        self.assertEqual(position, resp.tell())

        position += len(next(stream))
        self.assertEqual(3, position)
        self.assertEqual(position, resp.tell())

        self.assertRaises(StopIteration, next, stream)

    @async_test
    def test_gzipped_streaming(self):
        import zlib
        compress = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        data = compress.compress(b'foo')
        data += compress.flush()

        #fp = BytesIO(data)
        fp = self._fake_fp(data)
        resp = HTTPResponse(fp, headers={'content-encoding': 'gzip'},
                         preload_content=False)
        stream = yield From(resp.stream(2))

        self.assertEqual(next(stream), b'fo')
        self.assertEqual(next(stream), b'o')
        self.assertRaises(StopIteration, next, stream)

    @async_test
    def test_gzipped_streaming_tell(self):
        import zlib
        compress = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        uncompressed_data = b'foo'
        data = compress.compress(uncompressed_data)
        data += compress.flush()

        #fp = BytesIO(data)
        fp = self._fake_fp(data)
        resp = HTTPResponse(fp, headers={'content-encoding': 'gzip'},
                         preload_content=False)
        stream = yield From(resp.stream())

        # Read everything
        payload = next(stream)
        self.assertEqual(payload, uncompressed_data)

        self.assertEqual(len(data), resp.tell())

        self.assertRaises(StopIteration, next, stream)

    def tst_deflate_streaming_tell_intermediate_point(self):

        # test not relevant any longer, now that 'stream' is just a cached
        #  set of blocks

        # Ensure that ``tell()`` returns the correct number of bytes when
        # part-way through streaming compressed content.
        import zlib

        NUMBER_OF_READS = 10

        class MockCompressedDataReading(BytesIO):
            """
            A ByteIO-like reader returning ``payload`` in ``NUMBER_OF_READS``
            calls to ``read``.
            """

            def __init__(self, payload, payload_part_size):
                self.payloads = [
                    payload[i*payload_part_size:(i+1)*payload_part_size]
                             for i in range(NUMBER_OF_READS+1)]

                assert b"".join(self.payloads) == payload

            def read(self, _=None):
                # Amount is unused.
                yield None

                raise Return (b''.join(self.payloads))

                #if len(self.payloads) > 0:
                #    return self.payloads.pop(0)
                #return b""


        uncompressed_data = zlib.decompress(ZLIB_PAYLOAD)

        payload_part_size = len(ZLIB_PAYLOAD) // NUMBER_OF_READS
        fp = MockCompressedDataReading(ZLIB_PAYLOAD, payload_part_size)
        resp = HTTPResponse(fp, headers={'content-encoding': 'deflate'},
                            preload_content=False)
        stream = yield From(resp.stream(payload_part_size))

        parts_positions = []
        for part in stream:
            _t = resp.tell()
            parts_positions.append((part, _t))
        end_of_stream = resp.tell()

        self.assertRaises(StopIteration, next, stream)

        parts, positions = zip(*parts_positions)

        # Check that the payload is equal to the uncompressed data
        payload = b"".join(parts)
        self.assertEqual(uncompressed_data, payload)

        # Check that the positions in the stream are correct
        expected = [(i+1)*payload_part_size for i in range(NUMBER_OF_READS)]
        self.assertEqual(expected, list(positions))

        # Check that the end of the stream is in the correct place
        self.assertEqual(len(ZLIB_PAYLOAD), end_of_stream)

    @async_test
    def test_deflate_streaming(self):
        import zlib
        data = zlib.compress(b'foo')

        fp = self._fake_fp(data)
        resp = HTTPResponse(fp, headers={'content-encoding': 'deflate'},
                         preload_content=False)
        stream = yield From(resp.stream(2))

        self.assertEqual(next(stream), b'fo')
        self.assertEqual(next(stream), b'o')
        self.assertRaises(StopIteration, next, stream)

    @async_test
    def test_deflate2_streaming(self):
        import zlib
        compress = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
        data = compress.compress(b'foo')
        data += compress.flush()

        fp = self._fake_fp(data)
        resp = HTTPResponse(fp, headers={'content-encoding': 'deflate'},
                         preload_content=False)
        stream = yield From(resp.stream(2))

        self.assertEqual(next(stream), b'fo')
        self.assertEqual(next(stream), b'o')
        self.assertRaises(StopIteration, next, stream)

    @async_test
    def test_empty_stream(self):

        fp = self._fake_fp(b'')
        #fp = BytesIO(b'')
        resp = HTTPResponse(fp, preload_content=False)
        stream = yield From(resp.stream(2, decode_content=False))

        self.assertRaises(StopIteration, next, stream)

    @async_test
    def test_mock_httpresponse_stream(self):
        # Mock out a HTTP Request that does enough to make it through urllib3's
        # read() and close() calls, and also exhausts and underlying file
        # object.
        class MockHTTPRequest(object):
            self.fp = None

            @asyncio.coroutine
            def read(self, amt=None):
                data = yield From(self.fp.read(amt))
                if not data:
                    self.fp = None

                raise Return (data)

            def close(self):
                self.fp = None

        #bio = BytesIO(b'foo')
        bio = self._fake_fp(b'foo')
        fp = MockHTTPRequest()
        fp.fp = bio
        resp = HTTPResponse(fp, preload_content=False)
        stream = yield From(resp.stream(2))

        self.assertEqual(next(stream), b'fo')
        self.assertEqual(next(stream), b'o')
        self.assertRaises(StopIteration, next, stream)

    def test_get_case_insensitive_headers(self):
        headers = {'host': 'example.com'}
        r = HTTPResponse(headers=headers)
        self.assertEqual(r.headers.get('host'), 'example.com')
        self.assertEqual(r.headers.get('Host'), 'example.com')

if __name__ == '__main__':
    unittest.main()
