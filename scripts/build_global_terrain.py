"""Build the CC0 NOAA ETOPO coarse offline display fallback.

Requires GDAL, NumPy and requests. Downloads ~0.9 GB of build inputs once into
NOAA_SOURCE_CACHE; only the small generated grid and manifest are distributed.
No Mapterhorn planet archive is downloaded. Not survey or navigation data.
"""
import gzip,hashlib,json,requests,os
from pathlib import Path
import numpy as np
from osgeo import gdal
p=Path(os.environ.get('TERRAIN_OUTPUT',Path(__file__).resolve().parents[1]/'platform/homelab/terrain-data'));p.mkdir(parents=True,exist_ok=True)
cache=Path(os.environ.get('NOAA_SOURCE_CACHE',Path.home()/'.cache/mosaicearth/noaa'));cache.mkdir(parents=True,exist_ok=True)
gdal.UseExceptions();gdal.SetConfigOption('GDAL_HTTP_TIMEOUT','60');gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN','EMPTY_DIR')
base='https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/'
values={}
for kind,folder in [('surface','60s_surface_elev_gtif'),('geoid','60s_geoid_gtif')]:
 print('Reading coarse global',kind,flush=True)
 url=base+folder+'/ETOPO_2022_v1_60s_N90W180_'+kind+'.tif'
 local=cache/(kind+'-etopo60.tif')
 if not local.exists():
  with requests.get(url,stream=True,timeout=(15,60)) as r:
   r.raise_for_status();total=0
   with local.with_suffix('.partial').open('wb') as f:
    for chunk in r.iter_content(4*1024*1024):
     f.write(chunk);total+=len(chunk)
     if total%(64*1024*1024)==0:print(kind,total,flush=True)
   assert total==int(r.headers['Content-Length'])
  local.with_suffix('.partial').replace(local)
 d=gdal.Open(str(local))
 a=d.ReadAsArray(buf_xsize=1440,buf_ysize=720,resample_alg=gdal.GRIORA_Average)
 assert np.isfinite(a).all() and a.min()>-12000
 values[kind]=a
v=np.maximum(values['surface'],0)+values['geoid']
raw=np.rint(v/.5).astype('<i2').tobytes();encoded=gzip.compress(raw,compresslevel=9,mtime=0)
(p/'global-etopo2022-v1.bin.gz').write_bytes(encoded)
manifest={'version':1,'source':'NOAA NCEI ETOPO 2022 v1 60 arc-second surface and EGM2008 geoid','doi':'https://doi.org/10.25921/fd45-gt74','license':'CC0-1.0','datum':'WGS84 ellipsoidal','width':1440,'height':720,'step':.25,'pixel_centres':True,'scale':.5,'format':'int16 little-endian','file':'global-etopo2022-v1.bin.gz','sha256':hashlib.sha256(raw).hexdigest(),'gzipSha256':hashlib.sha256(encoded).hexdigest(),'bytes':len(encoded),'note':'Coarse display fallback (~28 km at equator); negative mean elevations clamped to sea surface before adding geoid. Use bundled polar grids beyond 84 degrees. Not for surveying or navigation.'}
(p/'global-etopo2022-v1.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest),flush=True)
