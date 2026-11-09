import asyncio
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from homelab.terrain_gateway import TerrainCache, TerrainUnavailable, router, PREFIX


MESH = struct.pack('<3d2f7dI', *([1.0]*3), 0.0, 100.0, *([1.0]*7), 3) + bytes(18)
META = {'format':'quantized-mesh-1.0','projection':'EPSG:4326','scheme':'tms',
        'version':'5','maxzoom':14,'tiles':['https://untrusted.invalid/{z}'],
        'attribution':'ReEarth Mapterhorn NGA','parentUrl':'https://untrusted.invalid'}


class TerrainCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.offline = False
        async def handler(request):
            self.calls.append(str(request.url))
            await asyncio.sleep(.01)
            if self.offline:
                return httpx.Response(429, content=b'upstream unavailable')
            return httpx.Response(200, json=META) if request.url.path.endswith('layer.json') else httpx.Response(200, content=MESH)
        self.transport = httpx.MockTransport(handler)
        self.cache = TerrainCache(self.directory.name, transport=self.transport)

    async def asyncTearDown(self):
        await self.cache.close()

    async def test_cache_survives_restart_and_upstream_outage(self):
        data, status = await self.cache.tile('5',3,2,1,'')
        self.assertEqual((data,status), (MESH,'MISS'))
        self.offline = True
        restarted = TerrainCache(self.directory.name, transport=self.transport)
        self.assertEqual(await restarted.tile('5',3,2,1,''), (MESH,'HIT'))
        self.assertEqual(len(self.calls),2)
        with self.assertRaises(TerrainUnavailable):
            await restarted.tile('5',3,3,1,'')
        self.offline = False
        self.assertEqual((await restarted.tile('5',3,3,1,''))[0], MESH)

    async def test_deduplicates_identical_concurrent_requests(self):
        values = await asyncio.gather(*(self.cache.tile('5',3,2,1,'') for _ in range(20)))
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(v[0]==MESH for v in values))
        self.assertFalse(self.cache.pending)

    async def test_validates_path_revision_and_extensions(self):
        for args in [('../x',1,1,1,''),('5',21,1,1,''),('5',1,4,1,''),('5',1,1,2,''),('5',1,1,1,'../../private')]:
            with self.assertRaises(ValueError): await self.cache.tile(*args)
        self.assertFalse(self.calls)
        with self.assertRaises(ValueError): await self.cache.tile('6',1,1,1,'')
        self.assertEqual(len(self.calls),1)
        metadata = await self.cache.metadata()
        self.assertEqual(metadata['tiles'],['5/{z}/{x}/{y}.terrain'])
        self.assertNotIn('parentUrl',metadata)
        self.assertEqual(metadata['attribution'],META['attribution'])

    async def test_keeps_extension_variants_separate(self):
        for ext in ['', 'watermask', 'octvertexnormals']:
            await self.cache.tile('5',2,1,1,ext)
        self.assertEqual(len(self.calls),4)

    async def test_invalid_mesh_is_not_cached(self):
        self.cache.transport = httpx.MockTransport(lambda r: httpx.Response(200,json=META) if r.url.path.endswith('layer.json') else httpx.Response(200,content=b'<html>'+b'x'*200))
        with self.assertRaises(TerrainUnavailable): await self.cache.tile('5',2,1,1,'')
        self.assertIsNone(await self.cache.read('5/2/1/1?extensions='))

    async def test_cache_limit_enforced_on_every_write(self):
        self.cache.max_bytes = 30
        self.cache.max_files = 2
        for n in range(8): await self.cache.write(str(n),b'0123456789')
        files=list(Path(self.directory.name).glob('*.cache'))
        self.assertLessEqual(len(files),2)
        self.assertLessEqual(sum(p.stat().st_size for p in files),30)

    async def test_old_valid_tile_remains_available_when_revalidation_fails(self):
        await self.cache.tile('5',3,2,1,'')
        os.utime(self.cache.path('5/3/2/1?extensions='),(0,0))
        self.offline=True
        self.assertEqual(await self.cache.tile('5',3,2,1,''),(MESH,'STALE'))

    async def test_stale_metadata_and_circuit_breaker(self):
        await self.cache.metadata()
        os.utime(self.cache.path('metadata'),(0, __import__('time').time()-7200))
        self.offline=True
        self.assertEqual((await self.cache.metadata())['version'],'5')
        for n in range(6):
            with self.assertRaises(TerrainUnavailable): await self.cache.tile('5',3,n,1,'')
        self.assertLessEqual(len(self.calls),4)


class TerrainRouteTests(unittest.TestCase):
    def test_public_contract_and_not_an_open_proxy(self):
        app=FastAPI();app.include_router(router)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'MOSAICEARTH_TERRAIN_GATEWAY':'1','MOSAICEARTH_DATA_DIR':tmp}):
            from homelab.terrain_gateway import cache
            cache().transport=httpx.MockTransport(lambda r: httpx.Response(200,json=META) if r.url.path.endswith('layer.json') else httpx.Response(200,content=MESH))
            with TestClient(app) as c:
                r=c.get(PREFIX+'/layer.json');self.assertEqual(r.status_code,200)
                self.assertEqual(r.headers['access-control-allow-origin'],'*')
                url=PREFIX+'/5/1/1/1.terrain'
                r=c.get(url);self.assertEqual(r.content,MESH)
                self.assertEqual(c.get(url,headers={'If-None-Match':r.headers['etag']}).status_code,304)
                self.assertEqual(c.get(url+'?url=http://169.254.169.254').status_code,400)
                self.assertEqual(c.get(PREFIX+'/5/1/9/9.terrain').status_code,404)
        with patch.dict(os.environ,{'MOSAICEARTH_TERRAIN_GATEWAY':'0'}):
            self.assertEqual(TestClient(app).get(PREFIX+'/layer.json').status_code,404)


if __name__=='__main__': unittest.main()
