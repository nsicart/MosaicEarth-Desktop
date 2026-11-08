"""Optional GPU installation must preserve usable CPU engines and saved bytes."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import mosaicearth_desktop as desktop

class GPUInstallationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.resources=self.root/'resources';self.resources.mkdir()
        self.workspace=desktop.Workspace(self.root/'workspace')
        (self.workspace.root/'account-grant.json').write_text('{}')
        self.installed={};self.calls=[];self.loaded=[];self.paused=False;self.gpu_error=False;self.corrupt=False
        self.payloads={'cpu':b'cpu verified bytes','cuda':b'cuda verified bytes'}
        for kind,content in self.payloads.items():
            data={'version':'0.4.0','filename':kind+'.tgz','sha256':hashlib.sha256(content).hexdigest(),
                  'bytes':len(content),'unpacked_bytes':100,'image_ids':{kind:'sha256:'+kind}}
            (self.resources/('engine-payload'+('-cuda' if kind=='cuda' else '')+'.json')).write_text(json.dumps(data))
        def inspect(args,**kw):
            self.assertIn('inspect',args)
            name=args[args.index('inspect')+1];digest=self.installed.get(name)
            return subprocess.CompletedProcess(args,0 if digest else 1,(digest or '').encode())
        def download(path,start,end,total):
            kind='cuda' if 'variant=cuda' in path else 'cpu';self.calls.append((kind,start,end))
            if kind=='cuda' and self.gpu_error:raise ValueError('connection interrupted')
            block=self.payloads[kind][start:end+1]
            return bytes([0])*len(block) if self.corrupt else block
        def load(args,log,**kw):
            name=Path(args[-1]).stem;self.loaded.append(name);self.installed[name]='sha256:'+name
        client=SimpleNamespace(token='',request=lambda *a,**kw:{'token':'fixture'},download_range=download)
        patches=[patch.object(desktop,'RESOURCES',self.resources),
            patch.object(desktop,'Client',return_value=client),
            patch.object(desktop.subprocess,'run',side_effect=inspect),
            patch.object(desktop.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)),
            patch.object(self.workspace,'installation_status',return_value={'docker':True,'account':{'email':'fixture'}}),
            patch.object(self.workspace,'docker_run',side_effect=load),
            patch('mosaicearth_gpu.detect',return_value={'mode':'cuda'})]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.workspace.start_transfer=lambda key,operation:operation(lambda *a:None,lambda:self.paused)
    def test_resumes_gpu_after_cpu_and_verifies_before_load(self):
        self.installed['cpu']='sha256:cpu'
        (self.workspace.root/'cuda.tgz').write_bytes(self.payloads['cuda'][:5])
        result=self.workspace.provision_engines()
        self.assertTrue(result['engines_installed']);self.assertIsNone(result['gpu_warning'])
        self.assertEqual(self.calls,[('cuda',5,len(self.payloads['cuda'])-1)])
        self.assertEqual(self.loaded,['cuda']);self.assertFalse((self.workspace.root/'cuda.tgz').exists())
    def test_failed_optional_gpu_retains_cpu_and_resume_bytes(self):
        self.gpu_error=True
        (self.workspace.root/'cuda.tgz').write_bytes(self.payloads['cuda'][:5])
        result=self.workspace.provision_engines()
        self.assertEqual(self.installed,{'cpu':'sha256:cpu'})
        self.assertIn('CPU processing is ready',result['gpu_warning'])
        self.assertEqual((self.workspace.root/'cuda.tgz').read_bytes(),self.payloads['cuda'][:5])
    def test_corrupt_gpu_is_not_loaded_and_retries_cleanly(self):
        self.installed['cpu']='sha256:cpu';self.corrupt=True
        self.assertIsNotNone(self.workspace.provision_engines()['gpu_warning'])
        self.assertEqual(self.loaded,[]);self.assertFalse((self.workspace.root/'cuda.tgz').exists())
        self.corrupt=False
        self.assertIsNone(self.workspace.provision_engines()['gpu_warning'])
        self.assertEqual(self.loaded,['cuda'])
    def test_pause_is_not_reported_as_successful_gpu_installation(self):
        self.installed['cpu']='sha256:cpu';self.paused=True
        with self.assertRaisesRegex(ValueError,'paused'):self.workspace.provision_engines()
        self.assertEqual(self.loaded,[])
    def test_cpu_preference_skips_optional_download(self):
        with patch('mosaicearth_gpu.detect',return_value={'mode':'cpu'}):
            self.assertIsNone(self.workspace.provision_engines()['gpu_warning'])
        self.assertEqual(self.loaded,['cpu']);self.assertTrue(all(c[0]=='cpu' for c in self.calls))

if __name__=='__main__': unittest.main()
