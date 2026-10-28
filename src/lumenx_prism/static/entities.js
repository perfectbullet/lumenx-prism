'use strict';
const $ = id => document.getElementById(id);
const endpoint = '/v1/scripts/extract-entities';
const errors = {unauthorized:'服务令牌无效，请填写 AI_SERVICE_API_KEY。',not_configured:'模型未配置，请前往模型配置页。',model_not_configured:'尚未绑定实体提取模型，请前往模型配置页。',model_database_not_configured:'尚未配置模型数据库连接。',model_config_unavailable:'模型配置库不可用，请检查 MongoDB。',model_config_disabled:'当前绑定的模型已停用，请选择其他模型。',model_config_incompatible:'当前绑定不适用于实体提取，请选择文本 Chat Completions 模型。',model_config_not_found:'当前绑定的模型不存在，请重新配置。',upstream_error:'模型调用失败，请检查服务端模型配置。',invalid_model_output:'模型输出未通过 JSON / 实体校验。',history_unavailable:'MongoDB 读写失败，请检查连接、账号权限和网络。未收到结果时请先查看历史，避免重复提交。',history_configuration_error:'数据库配置无效；数据库应为 lumenx_prism。',history_not_configured:'尚未配置 MongoDB，当前不会保存历史。',history_not_found:'历史记录不存在。'};
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
function setBusy(value){busy=value;for(const id of ['extract','sample','restore','connect','token','script','prompt'])$(id).disabled=value;$('extract').textContent=value?'正在提取…':'提取实体';document.querySelectorAll('.history-row').forEach(node=>node.disabled=value);}
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
const stateNames={running:'处理中 / 未完成',succeeded:'成功',failed:'失败'};
function meta(row){return `${date(row.created_at)} · ${row.requested_model||'—'} · ${row.duration_ms??'—'} ms`;}
async function loadHistory(){
  const sequence=++historySequence;
  $('historyStatus').textContent='正在读取历史…';$('prev').disabled=true;$('next').disabled=true;
  try{
    const query=new URLSearchParams({limit:String(pageSize),offset:String(offset)});if($('filter').value)query.set('status',$('filter').value);
    const {data}=await api(`${endpoint}/history?${query}`);if(sequence!==historySequence)return;
    $('historyList').replaceChildren();more=data.has_more;
    for(const row of data.items){const button=textNode('button','','history-row');button.disabled=busy;button.append(textNode('span',stateNames[row.status]||row.status,`tag ${row.status==='succeeded'?'ok':row.status==='failed'?'error':'busy'}`),textNode('span',row.text_preview,'title'),textNode('span',meta(row),'meta'));button.onclick=()=>openHistory(row.id);$('historyList').append(button);}
    $('historyStatus').textContent=data.items.length?`本页 ${data.items.length} 条记录。运行中长时间未更新的记录可能因服务中断而未完成。`:'暂无记录。提交剧本后可在这里回看。';
  }catch(error){if(sequence!==historySequence)return;more=false;$('historyList').replaceChildren();$('historyStatus').textContent=error.message;}
  if(sequence!==historySequence)return;
  $('pageLabel').textContent=`第 ${Math.floor(offset/pageSize)+1} 页`;$('prev').disabled=offset===0;$('next').disabled=!more;
}
async function openHistory(id){
  if(busy)return;
  setBusy(true);notice('');
  try{
    const {data}=await api(`${endpoint}/history/${encodeURIComponent(id)}`);selectedHistory=data;
    $('resultTitle').textContent='历史结果';renderResult(data.result);$('runMeta').textContent=`${stateNames[data.status]} · ${meta(data)} · ${data.id}`;
    if(data.error_code)notice(errors[data.error_code]||data.error_code);
    if(data.status==='running')notice('该记录尚未完成，或服务中断导致结果未回写。查看历史不会恢复模型任务。');
    $('historyDetail').hidden=false;$('historyDetail').open=true;
    $('historyMeta').textContent=JSON.stringify({id:data.id,provider:data.provider,requested_model:data.requested_model,status:data.status,created_at:data.created_at,model_config:data.model_config,actual_model:data.actual_model,usage:data.usage,prompt_sha256:data.prompt_sha256},null,2);
    $('historyText').textContent=data.request.text;$('historyPrompt').textContent=data.request.custom_extraction_prompt||'使用默认提示词';
  }catch(error){notice(error.message);}finally{setBusy(false);}
}
async function connect(){
  $('connect').disabled=true;
  try{const {data}=await api('/v1/test-console/config');$('config').textContent=`${data.provider} / ${data.model} · ${data.model_configured?'凭据已配置':'缺少模型凭据'} · 配置 v${data.model_revision} · ${data.history_configured?'历史已配置':'未启用历史'}`;status('服务已连接','ok');await loadHistory();}
  catch(error){status('连接失败','error');notice(error.message);}finally{$('connect').disabled=false;}
}
async function extract(){
  if(busy)return;
  const text=$('script').value.trim();if(!text){status('请先输入剧本','error');$('script').focus();return;}
  const custom=$('prompt').value;
  if([...text].length>80000||[...custom].length>30000){status('输入超过长度限制','error');return;}
  setBusy(true);notice('');status('正在调用模型，请等待…','busy');$('resultTitle').textContent='本次结果';$('runMeta').textContent='';renderResult(null);
  const started=performance.now();const timer=setInterval(()=>$('elapsed').textContent=`${((performance.now()-started)/1000).toFixed(1)} s`,100);
  try{
    const {data,response}=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,custom_extraction_prompt:custom})});
    renderResult(data);const id=response.headers.get('X-Prism-History-Id');$('runMeta').textContent=id?`历史编号：${id}`:'本次未启用历史记录';status('提取完成','ok');
    if(response.headers.get('X-Prism-History-Warning'))notice('输入已保存，但结果回写失败。请复制当前 JSON 留存；不要为补记录而重复调用模型。');
    if(!id)notice('本次未启用 MongoDB 历史，请检查服务端配置。');
  }catch(error){status('提取失败','error');notice(error.message);const id=error.response?.headers.get('X-Prism-History-Id');if(id)$('runMeta').textContent=`历史编号：${id}`;if(error.response?.headers.get('X-Prism-History-Warning'))notice(`${error.message} 输入已保存，但失败状态未回写；历史可能仍显示未完成。`);}
  finally{clearInterval(timer);$('elapsed').textContent=`${((performance.now()-started)/1000).toFixed(1)} s`;setBusy(false);offset=0;await loadHistory();}
}
$('connect').onclick=connect;$('extract').onclick=extract;$('refresh').onclick=loadHistory;
$('script').oninput=()=>{$('count').textContent=`${[...$('script').value].length} / 80000`;$('sampleNote').hidden=true;};
$('script').onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();extract();}};
$('sample').onclick=async()=>{try{const response=await fetch('/static/sample.json');if(!response.ok)throw Error();const sample=await response.json();$('script').value=sample.text;$('prompt').value='';$('script').oninput();$('sampleNote').hidden=false;$('script').focus();}catch{notice('样本加载失败，请直接粘贴剧本。');}};
$('copy').onclick=async()=>{if(!currentResult)return;try{await navigator.clipboard.writeText(JSON.stringify(currentResult,null,2));$('copy').textContent='已复制';setTimeout(()=>$('copy').textContent='复制 JSON',1600);}catch{notice('剪贴板不可用，请展开原始 JSON 手动复制。');}};
$('restore').onclick=()=>{if(!selectedHistory||busy)return;$('script').value=selectedHistory.request.text;$('prompt').value=selectedHistory.request.custom_extraction_prompt||'';$('script').oninput();$('script').focus();status('已恢复输入，尚未提交');window.scrollTo({top:0,behavior:'smooth'});};
$('filter').onchange=()=>{offset=0;loadHistory();};$('prev').onclick=()=>{offset=Math.max(0,offset-pageSize);loadHistory();};$('next').onclick=()=>{offset+=pageSize;loadHistory();};
fetch('/health',{cache:'no-store'}).then(response=>{if(!response.ok)throw Error();$('health').textContent='服务在线';$('health').className='tag ok';}).catch(()=>{$('health').textContent='服务离线';$('health').className='tag error';});
