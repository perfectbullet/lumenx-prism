"""Browser utility regression in insecure-context and native WebCrypto modes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required for console utility regression')
def test_http_request_ids_and_fingerprints_match_native_crypto():
    utility = Path(__file__).parents[1] / 'src/lumenx_prism/static/request-utils.js'
    values = ['', 'abc', '中文剧本与🙂', *['x' * n for n in (55, 56, 63, 64, 65)], '中文' * 80000]
    program = r'''
const {readFileSync}=require('node:fs');
const {webcrypto}=require('node:crypto');
const vm=require('node:vm');
const source=readFileSync(process.argv[1],'utf8');
const values=JSON.parse(readFileSync(0,'utf8'));
(async()=>{
 const results=[];
 for(const crypto of [{getRandomValues:webcrypto.getRandomValues.bind(webcrypto)},webcrypto]){
  const context=vm.createContext({crypto,TextEncoder});vm.runInContext(source,context);
  const api=context.PrismRequests;
  const ids=Array.from({length:500},()=>api.createRequestId());
  if(new Set(ids).size!==500||!ids.every(id=>/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)))throw Error('Invalid UUIDs');
  const seeds=Array.from({length:500},()=>api.randomSeed());
  if(!seeds.every(s=>Number.isSafeInteger(s)&&s>=0&&s<=Number.MAX_SAFE_INTEGER))throw Error('Seed out of range');
  results.push(await Promise.all(values.map(v=>api.fingerprint(v))));
 }
 for(const [word,expected] of [[0,0],[0xffffffff,Number.MAX_SAFE_INTEGER]]){
  const context=vm.createContext({crypto:{getRandomValues:a=>a.fill(word)},TextEncoder});vm.runInContext(source,context);
  if(context.PrismRequests.randomSeed()!==expected)throw Error('Seed boundary mismatch');
 }
 process.stdout.write(JSON.stringify(results));
})().catch(e=>{process.stderr.write(String(e));process.exit(1)});
'''
    result = subprocess.run(['node', '-e', program, str(utility)], input=json.dumps(values),
                            capture_output=True, text=True, check=True, timeout=30)
    expected = [hashlib.sha256(value.encode()).hexdigest() for value in values]
    assert json.loads(result.stdout) == [expected, expected]


@pytest.mark.parametrize('name', ['images', 'entities'])
def test_console_loads_http_utility_before_application_script(name):
    root = Path(__file__).parents[1] / 'src/lumenx_prism/static'
    page = (root / (name + '.html')).read_text()
    assert page.index('/static/request-utils.js') < page.index('/static/' + name + '.js')
    script = (root / (name + '.js')).read_text()
    assert 'crypto.randomUUID()' not in script
    assert 'crypto.subtle' not in script
    assert 'PrismRequests.createRequestId()' in script
