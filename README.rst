===================
yieldfrom . urllib3
===================


What is this about?
===================

Yieldfrom is a project to port various useful Python 3 libraries, both standard library and otherwise,
to work under Asyncio.  The intention is to have the port be as alike as possible to the original, so that
the learning curve is minimal, and to make porting dependent modules as easy as possible.

This package is a port of the *Urllib3* package.

Some functions, methods, and properties have become coroutines.  This document itemizes those, with a few
notes on how usage needs to be different.  Other than what is mentioned here, the classes, methods and functions
are all named the same and used the same.

Since the 'yield from coroutine' statements block the current method until the statement completes, this variant
can be a statement-for-statement replacement for the original, and the architecture of the app is unchanged.  No
callbacks anywhere.

yieldfrom_t is a direct port of yieldfrom to trollius syntax for use with python 2.7 etc.

imports
=======

Instead of importing like:

	from urllib.connections import HTTPConnection
	from urllib import connections

use:
	from yieldfrom_t.urllib.connections import HTTPConnection
	from yieldfrom_t.urllib import connections


Classes HTTPConnection and HTTPSConnection
==========================================

The *connect* method is now a coroutine.  Call it with yield from, like 'c = yield From(conn.connect(...))', and
otherwise the argument list is the same.


Classes HTTPConnectionPool, PoolManager, and ProxyManager
=========================================================

These classes all feature methods *urlopen*, *request*, *request_encode_url*, and *request_encode_body* , which
have become coroutines.  The argument list is unchanged, and the functionality is unchanged.  Just call it with
'yield From()' as a coroutine.


Class HTTPResponse
==================

There is one new method, *init()* which is a coroutine.  Its function was performed by the constructor in
*Urllib3*, but needs to be async here.  A coroutine constructor would be difficult, so the async portions are
moved to the *init()* method.  Run it as a coroutine after constructing an HTTPResponse.  Generally, users of
the module won't be creating HTTPResponses directly, so this should not be much of an issue.

The *read*, *readinto*, *stream* methods are all coroutines.  The *data* attribute is actually a property, now
a coroutine, and should be referenced with the *yield* *From* syntax, like 'd = yield From(resp.data)'.


The *from_httplib* classmethod is a coroutine also, though you probably won't be using it directly.

The *stream* method does not actually stream, but preloads the body and simulates a stream for compatibility
with modules and apps that use the method.


Otherwise
=========

Other than the changes above, the API is the same as the original, and excellent documentation can be found at:
`URLLIB3 <http://urllib3.readthedocs.org>`.