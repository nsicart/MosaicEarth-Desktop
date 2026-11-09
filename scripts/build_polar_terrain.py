"""Build bounded CC0 ETOPO polar display grids; run with GDAL + NumPy.

NOAA ETOPO 2022 v1, 60 arc-second ice surface + EGM2008 geoid.
https://doi.org/10.25921/fd45-gt74 (accessed 2026-11-09).
These grids are contextual terrain, not navigation or survey measurements.
"""
import gzip, hashlib, json, os
from pathlib import Path
import numpy as np
from osgeo import gdal

gdal.UseExceptions()
gdal.SetConfigOption('GDAL_HTTP_TIMEOUT','60')
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN','EMPTY_DIR')
BASE='https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/'
SOURCES={
 'surface':BASE+'60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif',
 'geoid':BASE+'60s_geoid_gtif/ETOPO_2022_v1_60s_N90W180_geoid.tif',
}
DEST=Path(os.environ.get('POLAR_OUTPUT',Path(__file__).resolve().parents[1]/'platform/homelab/terrain-data'))
DEST.mkdir(parents=True,exist_ok=True)
width,height=1440,361
longitudes=np.arange(width)*.25-180
x=(longitudes+180)*60-.5
x0=np.floor(x).astype(int);fx=x-x0
manifest={'version':1,'datum':'WGS84 ellipsoidal (ice surface + EGM2008)',
 'source':'NOAA NCEI ETOPO 2022 v1 60 arc-second ice surface',
 'doi':'https://doi.org/10.25921/fd45-gt74','license':'CC0-1.0','accessed':'2026-11-09',
 'sources':SOURCES,'width':width,'height':height,'west':-180,'longitudeStep':.25,
 'latitudeStep':1/60,'scale':.25,'format':'int16 little-endian','grids':{}}
for hemisphere,north in [('north',90),('south',-84)]:
 lat=north-np.arange(height)/60
 yf=np.clip((90-lat)*60-.5,0,10799);y0=np.floor(yf).astype(int);fy=yf-y0
 top=int(y0.min());bottom=min(10799,int(y0.max())+1)
 values={}
 for kind,url in SOURCES.items():
  print('Reading',hemisphere,kind,flush=True)
  ds=gdal.Open('/vsicurl/'+url)
  assert (ds.RasterXSize,ds.RasterYSize)==(21600,10800)
  assert ds.GetGeoTransform()==(-180.,1/60,0.,90.,0.,-1/60)
  a=ds.GetRasterBand(1).ReadAsArray(0,top,21600,bottom-top+1)
  assert np.isfinite(a).all() and np.min(a)>-12000
  row0=y0-top;row1=np.minimum(y0+1,10799)-top
  c0=np.mod(x0,21600);c1=np.mod(x0+1,21600)
  b=(a[row0[:,None],c0]*(1-fx)+a[row0[:,None],c1]*fx)*(1-fy[:,None])+(a[row1[:,None],c0]*(1-fx)+a[row1[:,None],c1]*fx)*fy[:,None]
  values[kind]=b
 # Render water surface, not seafloor. The >84° Arctic region has no land.
 heights=np.maximum(values['surface'],0)+values['geoid']
 pole=0 if hemisphere=='north' else height-1
 heights[pole,:]=np.mean(heights[pole,:]) # one geographic pole, independent of longitude
 scaled=np.rint(heights/manifest['scale'])
 assert scaled.min()>-32768 and scaled.max()<32767
 raw=scaled.astype('<i2').tobytes();compressed=gzip.compress(raw,compresslevel=9,mtime=0)
 filename=f'polar-{hemisphere}-etopo2022-v1.bin.gz'
 (DEST/filename).write_bytes(compressed)
 manifest['grids'][hemisphere]={'north':north,'south':north-6,'file':filename,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'gzipSha256':hashlib.sha256(compressed).hexdigest(),'min':float(heights.min()),'max':float(heights.max()),'pole':float(heights[pole,0])}
 print(hemisphere,manifest['grids'][hemisphere],flush=True)
(DEST/'polar-etopo2022-v1.json').write_text(json.dumps(manifest,indent=2)+'\n')
