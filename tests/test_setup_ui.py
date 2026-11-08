"""A completed download must not eject users from processing settings."""
import shutil
import subprocess
import unittest
from pathlib import Path


class SetupUiTest(unittest.TestCase):
    def test_settings_stay_open_and_only_new_installations_auto_open(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the setup controller regression')
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(process.argv[1], 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function fixture(initialStatus) {
  const elements = new Map(), calls = [], timers = [], redirects = [];
  let transferStatus = initialStatus;
  const element = () => ({style:{}, hidden:false, value:'auto', textContent:'',
    setAttribute(){}, append(){}, replaceChildren(){}, focus(){}});
  const document = {getElementById(id){if (!elements.has(id)) elements.set(id,element()); return elements.get(id);},createElement:element};
  const context = vm.createContext({document, window:{}, location:{pathname:'/setup', replace:url=>redirects.push(url)},
    setTimeout:fn=>timers.push(fn), fetch:async(path,options)=>{
      calls.push(path);
      let result = {};
      if (path === '/api/platform') result={status:'ready',message:'Ready',url:'http://127.0.0.1:19757',acceleration:{nodeodx:'cpu',opensplat:'cpu',reason:'CPU selected'}};
      if (path === '/api/installation') result={acceleration_preference:'cpu'};
      if (path === '/api/transfers') result={'engine-installation':{status:transferStatus,message:transferStatus,done:1,total:1}};
      return {ok:true,json:async()=>result};
    }});
  vm.runInContext(source,context);
  return {elements,calls,timers,redirects,setStatus:s=>{transferStatus=s;}};
}
async function flush(){await new Promise(setImmediate);await new Promise(setImmediate);}
(async()=>{
  const history=fixture('complete');await flush();
  for(let i=0;i<3;i++){history.timers.shift()();await flush();}
  assert.equal(history.calls.filter(p=>p==='/api/platform/start').length,0);
  assert.equal(history.redirects.length,0);
  assert.equal(history.elements.get('acceleration').value,'cpu');
  // An explicitly requested installation can complete immediately from cached images.
  await history.elements.get('install').onclick();history.timers.shift()();await flush();
  assert.equal(history.calls.filter(p=>p==='/api/platform/start').length,1);
  history.timers.shift()();await flush();assert.equal(history.redirects.length,1);
  // A page opened while a transfer is running follows that transfer to completion.
  const running=fixture('running');await flush();assert.equal(running.elements.get('install').disabled,true);
  running.setStatus('complete');running.timers.shift()();await flush();
  assert.equal(running.calls.filter(p=>p==='/api/platform/start').length,1);
  running.timers.shift()();await flush();assert.equal(running.redirects.length,1);
  console.log('Historical completion stays in settings; new and resumed installs open once.');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(Path(__file__).resolve().parents[1] / 'resources/setup.html')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
