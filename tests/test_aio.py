import asyncio
import base64
import collections
import json
import struct
import sys

import aiohttp
import pytest
import six
from pytest_httpserver import RequestHandler

import consul
import consul.aio

Check = consul.Check


@pytest.fixture
def local_server(httpserver):
    from pytest_httpserver import RequestHandler

    handler = httpserver.expect_request('/v1/agent/services')
    assert isinstance(handler, RequestHandler)
    handler.respond_with_data(json.dumps({"foo": "bar"}), status=599)
    port = httpserver.port
    LocalServer = collections.namedtuple('LocalServer', ['port'])
    yield LocalServer(port)
    httpserver.stop()


@pytest.fixture
async def local_timeout_server(httpserver):
    async def func():
        return json.dumps({"foo": "bar"})

    handler = httpserver.expect_request('/v1/agent/services')
    assert isinstance(handler, RequestHandler)
    handler.respond_with_data(await func(), status=200)
    LocalServer = collections.namedtuple('LocalServer', ['port', 'server'])
    return LocalServer(httpserver.port, httpserver)


@pytest.fixture
def loop(request):
    asyncio.set_event_loop(None)
    loop = asyncio.new_event_loop()

    def fin():
        loop.close()

    request.addfinalizer(fin)
    return loop


class TestAsyncioConsul(object):

    def test_kv(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            index, data = await c.kv.get('foo')

            assert data is None
            response = await c.kv.put('foo', 'bar')
            assert response is True
            response = await c.kv.put('foo-2', 'bar')
            assert response is True
            index, data = await c.kv.get('foo')
            assert data['Value'] == six.b('bar')

        loop.run_until_complete(main())

    def test_consul_ctor(self, loop, consul_port):
        # same as previous but with global event loop
        async def main():
            c = consul.aio.Consul(port=consul_port)
            assert c._loop is loop
            await c.kv.put('foo', struct.pack('i', 1000))
            index, data = await c.kv.get('foo')
            assert struct.unpack('i', data['Value']) == (1000,)

        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())

    def test_kv_binary(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            await c.kv.put('foo', struct.pack('i', 1000))
            index, data = await c.kv.get('foo')
            assert struct.unpack('i', data['Value']) == (1000,)

        loop.run_until_complete(main())

    def test_kv_missing(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)

            fut = asyncio.ensure_future(put(), loop=loop)
            await c.kv.put('index', 'bump')
            index, data = await c.kv.get('foo')
            assert data is None
            index, data = await c.kv.get('foo', index=index)
            assert data['Value'] == six.b('bar')
            await fut

        async def put():
            c = consul.aio.Consul(port=consul_port, loop=loop)

            await asyncio.sleep(2.0 / 100, loop=loop)
            await c.kv.put('foo', 'bar')

        loop.run_until_complete(main())

    def test_kv_put_flags(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            await c.kv.put('foo', 'bar')
            index, data = await c.kv.get('foo')
            assert data['Flags'] == 0

            response = await c.kv.put('foo', 'bar', flags=50)
            assert response is True
            index, data = await c.kv.get('foo')
            assert data['Flags'] == 50

        loop.run_until_complete(main())

    def test_kv_delete(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            await c.kv.put('foo1', '1')
            await c.kv.put('foo2', '2')
            await c.kv.put('foo3', '3')
            index, data = await c.kv.get('foo', recurse=True)
            assert [x['Key'] for x in data] == ['foo1', 'foo2', 'foo3']

            response = await c.kv.delete('foo2')
            assert response is True
            index, data = await c.kv.get('foo', recurse=True)
            assert [x['Key'] for x in data] == ['foo1', 'foo3']
            response = await c.kv.delete('foo', recurse=True)
            assert response is True
            index, data = await c.kv.get('foo', recurse=True)
            assert data is None

        loop.run_until_complete(main())

    def test_kv_subscribe(self, loop, consul_port):
        async def get():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            fut = asyncio.ensure_future(put(), loop=loop)
            index, data = await c.kv.get('foo')
            assert data is None
            index, data = await c.kv.get('foo', index=index)
            assert data['Value'] == six.b('bar')
            await fut

        async def put():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            await asyncio.sleep(1.0 / 100, loop=loop)
            response = await c.kv.put('foo', 'bar')
            assert response is True

        loop.run_until_complete(get())

    def test_transaction(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            value = base64.b64encode(b"1").decode("utf8")
            d = {"KV": {"Verb": "set", "Key": "asdf", "Value": value}}
            r = await c.txn.put([d])
            assert r["Errors"] is None

            d = {"KV": {"Verb": "get", "Key": "asdf"}}
            r = await c.txn.put([d])
            assert r["Results"][0]["KV"]["Value"] == value

        loop.run_until_complete(main())

    def test_agent_services(self, loop, consul_port):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            services = await c.agent.services()
            assert services == {}
            response = await c.agent.service.register('foo')
            assert response is True
            services = await c.agent.services()
            assert services == {
                'foo': {'ID': 'foo',
                        'Service': 'foo',
                        'Tags': [],
                        'Meta': {},
                        'Port': 0,
                        'Address': '',
                        'Weights': {'Passing': 1, 'Warning': 1},
                        'EnableTagOverride': False}, }
            response = await c.agent.service.deregister('foo')
            assert response is True
            services = await c.agent.services()
            assert services == {}

        loop.run_until_complete(main())

    def test_catalog(self, loop, consul_port):
        async def nodes():
            c = consul.aio.Consul(port=consul_port, loop=loop)

            fut = asyncio.ensure_future(register(), loop=loop)
            index, nodes = await c.catalog.nodes()
            assert len(nodes) == 1
            current = nodes[0]

            index, nodes = await c.catalog.nodes(index=index)
            nodes.remove(current)
            assert [x['Node'] for x in nodes] == ['n1']

            index, nodes = await c.catalog.nodes(index=index)
            nodes.remove(current)
            assert [x['Node'] for x in nodes] == []
            await fut

        async def register():
            c = consul.aio.Consul(port=consul_port, loop=loop)

            await asyncio.sleep(1.0 / 100, loop=loop)
            response = await c.catalog.register('n1', '10.1.10.11')
            assert response is True
            await asyncio.sleep(50 / 1000.0, loop=loop)
            response = await c.catalog.deregister('n1')
            assert response is True

        loop.run_until_complete(nodes())

    def test_session(self, loop, consul_port):
        async def monitor():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            fut = asyncio.ensure_future(register(), loop=loop)
            index, services = await c.session.list()
            assert services == []
            await asyncio.sleep(20 / 1000.0, loop=loop)

            index, services = await c.session.list(index=index)
            assert len(services)

            index, services = await c.session.list(index=index)
            assert services == []
            await fut

        async def register():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            await asyncio.sleep(1.0 / 100, loop=loop)
            session_id = await c.session.create()
            await asyncio.sleep(50 / 1000.0, loop=loop)
            response = await c.session.destroy(session_id)
            assert response is True

        loop.run_until_complete(monitor())

    @pytest.mark.skipif(sys.version_info < (3, 4, 1),
                        reason="Python <3.4.1 doesnt support __del__ calls "
                               "from GC")
    def test_httpclient__del__method(self, loop, consul_port, recwarn):
        async def main():
            c = consul.aio.Consul(port=consul_port, loop=loop)
            _, _ = await c.kv.get('foo')
            del c
            import gc
            # run gc to ensure c is collected
            gc.collect()
            w = recwarn.pop(ResourceWarning)
            assert issubclass(w.category, ResourceWarning)

        loop.run_until_complete(main())

    def test_root(self, loop, local_server):
        async def test_timeout():
            time_out = False
            c = consul.aio.Consul(port=local_server.port, loop=loop)
            try:
                await c.agent.services()
            except consul.Timeout:
                time_out = True
            assert time_out

        loop.run_until_complete(test_timeout())

    def test_http_session(self, loop, local_timeout_server, consul_port):
        async def test_session_close():
            http_server = await local_timeout_server
            c = consul.aio.Consul(port=http_server.port, loop=loop)
            c.agent.services()

            c.http._session = aiohttp.ClientSession()

            assert not c.http._session.closed
            c.http.__del__()
            await c.http.close()
            assert c.http._session.closed
            http_server.server.stop()
            ...

        loop.run_until_complete(test_session_close())
