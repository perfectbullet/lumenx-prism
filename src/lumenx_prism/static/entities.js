'use strict';
const $ = id => document.getElementById(id);
const endpoint = '/v1/scripts/extract-entities';
let watchSequence = 0;
const store = {get(key){try{return sessionStorage.getItem(key);}catch{return null;}},set(key,value){try{sessionStorage.setItem(key,value);}catch{}},remove(key){try{sessionStorage.removeItem(key);}catch{}}};
function historyPath(){return $('historySource').value === 'legacy' ? `${endpoint}/history` : '/v1/jobs';}
const errors = {execution_outcome_unknown:'worker 中断或租约过期，上游是否完成无法确认。系统不会自动重复调用。',model_config_changed:'排队期间模型配置已修改，本任务未调用模型。请检查配置后重新提交。',job_store_unavailable:'任务库暂时不可用；请重试查询或使用相同输入重试提交。',idempotency_conflict:'提交标识对应的输入不一致，请刷新页面后重试。',worker_execution_error:'任务执行异常，请检查 worker。',unauthorized:'服务令牌无效，请填写 AI_SERVICE_API_KEY。',not_configured:'模型未配置，请前往模型配置页。',model_not_configured:'尚未绑定实体提取模型，请前往模型配置页。',model_database_not_configured:'尚未配置模型数据库连接。',model_config_unavailable:'模型配置库不可用，请检查 MongoDB。',model_config_disabled:'当前绑定的模型已停用，请选择其他模型。',model_config_incompatible:'当前绑定不适用于实体提取，请选择文本 Chat Completions 模型。',model_config_not_found:'当前绑定的模型不存在，请重新配置。',upstream_error:'模型调用失败，请检查服务端模型配置。',invalid_model_output:'模型输出未通过 JSON / 实体校验。',history_unavailable:'MongoDB 读写失败，请检查连接、账号权限和网络。未收到结果时请先查看历史，避免重复提交。',history_configuration_error:'数据库配置无效；数据库应为 lumenx_prism。',history_not_configured:'尚未配置 MongoDB，当前不会保存历史。',history_not_found:'历史记录不存在。'};
let busy = false, currentResult = null, selectedHistory = null, offset = 0, more = false, historySequence = 0;
const pageSize = 8;
function textNode(tag, text, className){const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;}
function notice(message=''){$('notice').textContent=message;$('notice').hidden=!message;}
function status(message, kind=''){$('runStatus').textContent=message;$('runStatus').className=kind;}
function headers(){const token=$('token').value.trim();return token?{'Authorization':`Bearer ${token}`}:{ };}
async function api(path, options={}){
  let response;
  try{response=await fetch(path,{cache:'no-store',credentials:'omit',...options,headers:{...headers(),...options.headers}});}
  catch{throw Error('无法连接服务。若提交已发出，请先刷新历史确认状态，不要立即重复提交。');}
  const data=await response.json();
  if(!response.ok){const code=data.detail?.code;const error=Error(errors[code] || (response.status===422?'输入格式无效，请检查长度与字段。':`请求失败（HTTP ${response.status}）`));error.response=response;error.code=code;throw error;}
  return {data,response};
}
function setBusy(value){busy=value;for(const id of ['extract','sample','restore','connect','token','script','prompt'])$(id).disabled=value;$('restore').disabled=value||!selectedHistory;$('extract').textContent=value?'正在提取…':'提取实体';document.querySelectorAll('.history-row').forEach(node=>node.disabled=value);}
function renderResult(result){
  currentResult=result;$('result').replaceChildren();$('raw').textContent=result?JSON.stringify(result,null,2):'本次没有返回实体结果';$('copy').disabled=!result;
  if(!result){$('result').append(textNode('div','本次没有可展示的实体结果。','empty'));return;}
  const labels={characters:'角色',scenes:'场景',props:'道具'};
  const fields={age:'年龄',gender:'性别',clothing:'服装',visual_weight:'重要度',time_of_day:'时段',lighting_mood:'光线'};
  for(const [key,label] of Object.entries(labels)){
    const items=result[key]||[];
    $('result').append(textNode('div',`${label} · ${items.length}`,'group-title'));
    const grid=textNode('div','','entity-grid');
    if(!items.length)grid.append(textNode('p','未提取到该类实体。','hint'));
    for(const item of items){const card=textNode('article','','entity');card.append(textNode('h3',item.name));if(item.id)card.append(textNode('div',item.id,'ref'));card.append(textNode('p',item.description||'未描述外观'));for(const [field,name] of Object.entries(fields))if(item[field]!=null&&item[field]!=='')card.append(textNode('p',`${name}：${item[field]}`));grid.append(card);}
    $('result').append(grid);
  }
}
function date(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';}
const stateNames={queued:'排队中',running:'处理中 / 未完成',succeeded:'成功',failed:'失败'};
function meta(row){return `${date(row.created_at)} · ${row.requested_model||'—'} · ${row.duration_ms??'—'} ms`;}
async function loadHistory(){
  const sequence=++historySequence;
  $('historyStatus').textContent='正在读取历史…';$('prev').disabled=true;$('next').disabled=true;
  try{
    const query=new URLSearchParams({limit:String(pageSize),offset:String(offset)});if($('historySource').value==='legacy' && $('filter').value==='queued'){$('historyList').replaceChildren();$('historyStatus').textContent='旧同步历史没有排队状态。';return;}if($('filter').value)query.set('status',$('filter').value);if($('historySource').value!=='legacy')query.set('type','entity_extraction');
    const {data}=await api(`${historyPath()}?${query}`);if(sequence!==historySequence)return;
    $('historyList').replaceChildren();more=data.has_more;
    for(const row of data.items){const button=textNode('button','','history-row');button.disabled=busy;button.append(textNode('span',stateNames[row.status]||row.status,`tag ${row.status==='succeeded'?'ok':row.status==='failed'?'error':'busy'}`),textNode('span',row.text_preview,'title'),textNode('span',meta(row),'meta'));button.onclick=()=>openHistory(row.id);$('historyList').append(button);}
    $('historyStatus').textContent=data.items.length?`本页 ${data.items.length} 条记录。任务由独立 worker 执行；点击记录查看进度。`:'暂无记录。提交剧本后可在这里回看。';
  }catch(error){if(sequence!==historySequence)return;more=false;$('historyList').replaceChildren();$('historyStatus').textContent=error.message;}
  if(sequence!==historySequence)return;
  $('pageLabel').textContent=`第 ${Math.floor(offset/pageSize)+1} 页`;$('prev').disabled=offset===0;$('next').disabled=!more;
}
async function openHistory(id){
  if(busy)return;
  setBusy(true);notice('');
  try{
    const {data}=await api(`${historyPath()}/${encodeURIComponent(id)}`);if(($('historySource').value!=='legacy'&&data.type!=='entity_extraction')||typeof data.request?.text!=='string')throw Error('该记录不是实体提取任务，无法载入剧本。');selectedHistory=data;
    $('resultTitle').textContent='历史结果';renderResult(data.result);$('runMeta').textContent=`${stateNames[data.status]} · ${meta(data)} · ${data.id}`;
    if(data.error_code)notice(errors[data.error_code]||data.error_code);
    if($('historySource').value==='legacy' && data.status==='running')notice('旧同步记录没有后台恢复能力。');
    $('historyDetail').hidden=false;$('historyDetail').open=true;
    $('historyMeta').textContent=JSON.stringify({id:data.id,provider:data.provider,requested_model:data.requested_model,status:data.status,created_at:data.created_at,model_config:data.model_config,actual_model:data.actual_model,usage:data.usage,prompt_sha256:data.prompt_sha256},null,2);
    if($('historySource').value!=='legacy' && ['queued','running'].includes(data.status)){store.set('prism.activeJob',data.id);setTimeout(()=>watchJob(data.id),0);}
    $('historyText').textContent=data.request.text;$('historyPrompt').textContent=data.request.custom_extraction_prompt||'使用默认提示词';
  }catch(error){notice(error.message);}finally{setBusy(false);}
}
async function connect(){
  $('connect').disabled=true;
  try{const {data}=await api('/v1/test-console/config');$('config').textContent=`${data.provider} / ${data.model} · ${data.model_configured?'凭据已配置':'缺少模型凭据'} · 配置 v${data.model_revision} · ${data.history_configured?'历史已配置':'未启用历史'}`;status('服务已连接','ok');await loadHistory();const active=store.get('prism.activeJob');if(active)watchJob(active);}
  catch(error){status('连接失败','error');notice(error.message);}finally{$('connect').disabled=false;}
}
async function watchJob(id){
  const sequence=++watchSequence;
  setBusy(true);notice('');
  try{
    while(sequence===watchSequence){
      const {data}=await api(`/v1/jobs/${encodeURIComponent(id)}`);
      if(sequence!==watchSequence)return;
      $('runMeta').textContent=`任务：${id} · ${data.phase}`;
      $('elapsed').textContent=`${Math.max(0,(Date.now()-new Date(data.created_at))/1000).toFixed(1)} s`;
      status(stateNames[data.status],data.status==='failed'?'error':data.status==='succeeded'?'ok':'busy');
      if(['succeeded','failed'].includes(data.status)){
        renderResult(data.result);if(data.error_code)notice(errors[data.error_code]||data.error_code);
        store.remove('prism.activeJob');break;
      }
      if(data.status==='queued')$('runMeta').textContent+= ' · 等待 worker，请确认独立 worker 已启动';
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }catch(error){status('查询暂停','error');notice(`${error.message} 任务不会因页面断开而取消；重新连接服务可继续查询。`);}
  finally{if(sequence===watchSequence){setBusy(false);await loadHistory();}}
}
async function extract(){
  if(busy)return;
  const text=$('script').value.trim();if(!text){status('请先输入剧本','error');$('script').focus();return;}
  const custom=$('prompt').value;
  if([...text].length>80000||[...custom].length>30000){status('输入超过长度限制','error');return;}
  setBusy(true);notice('');status('正在提交任务…','busy');$('resultTitle').textContent='本次结果';renderResult(null);
  try{
    const body=JSON.stringify({text,custom_extraction_prompt:custom});
    const digest=await PrismRequests.fingerprint(body);
    let pending;try{pending=JSON.parse(store.get('prism.pendingSubmission'));}catch{}
    if(!pending || pending.digest!==digest){pending={digest,key:PrismRequests.createRequestId()};store.set('prism.pendingSubmission',JSON.stringify(pending));}
    const {data}=await api(`${endpoint}/jobs`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':pending.key},body});
    store.set('prism.activeJob',data.id);store.remove('prism.pendingSubmission');
    $('historySource').value='jobs';offset=0;await loadHistory();await watchJob(data.id);
  }catch(error){status('提交未确认','error');notice(`${error.message} 相同输入再次提交会复用提交标识，避免重复创建任务。`);setBusy(false);}
}
$('connect').onclick=connect;$('extract').onclick=extract;$('refresh').onclick=loadHistory;
$('script').oninput=()=>{$('count').textContent=`${[...$('script').value].length} / 80000`;$('sampleNote').hidden=true;};
$('script').onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();extract();}};
$('sample').onclick=async()=>{try{const response=await fetch('/static/sample.json');if(!response.ok)throw Error();const sample=await response.json();$('script').value=sample.text;$('prompt').value='';$('script').oninput();$('sampleNote').hidden=false;$('script').focus();}catch{notice('样本加载失败，请直接粘贴剧本。');}};
$('copy').onclick=async()=>{if(!currentResult)return;try{await navigator.clipboard.writeText(JSON.stringify(currentResult,null,2));$('copy').textContent='已复制';setTimeout(()=>$('copy').textContent='复制 JSON',1600);}catch{notice('剪贴板不可用，请展开原始 JSON 手动复制。');}};
$('restore').onclick=()=>{if(!selectedHistory||busy||typeof selectedHistory.request?.text!=='string')return;$('script').value=selectedHistory.request.text;$('prompt').value=selectedHistory.request.custom_extraction_prompt||'';$('script').oninput();$('script').focus();$('script').setSelectionRange(0,0);$('script').scrollTop=0;document.querySelector('.editor-fields')?.scrollTo({top:0});status('已恢复输入，尚未提交');window.scrollTo({top:0,behavior:'smooth'});};
$('historySource').onchange=()=>{offset=0;$('filter').value='';loadHistory();};
$('filter').onchange=()=>{offset=0;loadHistory();};$('prev').onclick=()=>{offset=Math.max(0,offset-pageSize);loadHistory();};$('next').onclick=()=>{offset+=pageSize;loadHistory();};
fetch('/health',{cache:'no-store'}).then(response=>{if(!response.ok)throw Error();$('health').textContent='服务在线';$('health').className='tag ok';}).catch(()=>{$('health').textContent='服务离线';$('health').className='tag error';});
