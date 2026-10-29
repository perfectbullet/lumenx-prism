'use strict';
const $=id=>document.getElementById(id);
const endpoint='/v1/model-configs';
const labels={text:'文本',speech:'语音',image:'图像',video:'视频'};
const errors={unauthorized:'服务 token 无效，请重新填写。',model_revision_conflict:'配置已被其他操作修改，请重新打开该模型后编辑。',model_revision_required:'缺少配置版本，请重新打开该模型。',model_api_key_required:'新增模型需要 API key。',model_config_incompatible:'实体提取只能使用文本 + Chat Completions 模型。',image_model_incompatible:'图像生成需使用 comfyui + custom_http 配置。',model_config_disabled:'请先启用该模型。',model_config_unavailable:'模型配置库读写失败，请检查 MongoDB 连接。',model_database_not_configured:'尚未配置 MongoDB 连接。',history_configuration_error:'MongoDB 配置无效。',model_config_not_found:'该模型不存在。'};
let editing=null, busy=false, offset=0, sequence=0;
const pageSize=12;
function el(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
function message(text=''){$('formMessage').hidden=!text;$('formMessage').textContent=text;}
function lock(value){busy=value;document.querySelectorAll('button,form input,form textarea,form select,#token').forEach(n=>{if(value){n.dataset.previousDisabled=String(n.disabled);n.disabled=true;}else{n.disabled=n.dataset.previousDisabled==='true';delete n.dataset.previousDisabled;}});}

async function api(path,options={}){
 const token=$('token').value.trim();
 const response=await fetch(path,{cache:'no-store',credentials:'omit',...options,headers:{...(token?{Authorization:`Bearer ${token}`}:{ }),'Content-Type':'application/json',...options.headers}});
 const data=await response.json();
 if(!response.ok){const code=data.detail?.code;throw Error(errors[code]||(response.status===422?'配置格式无效，请检查字段、参数范围及 JSON。':`请求失败（HTTP ${response.status}）`));}
 return data;
}
function reset(){editing=null;$('modelForm').reset();$('parameters').value='{}';$('formTitle').textContent='新增模型';$('revision').textContent='新配置';$('keyState').textContent='API key 保存在 MongoDB，列表和详情不回显原文。';message();}
async function load(){
 const current=++sequence;$('listStatus').textContent='正在读取…';
 try{
  const data=await api(`${endpoint}?limit=${pageSize}&offset=${offset}`);if(sequence!==current)return;
  $('connectionState').textContent='已连接';$('connectionState').className='tag ok';$('models').replaceChildren();
  const bound=data.items.find(row=>row.id===data.entity_extraction_id);
  $('binding').textContent=data.entity_extraction_id?`实体提取当前绑定：${bound?.name||data.entity_extraction_id}`:'尚未绑定实体提取模型，请选择一个已启用的文本模型。';
  for(const row of data.items){
   const selected=row.id===data.entity_extraction_id;
   const card=el('article','',`model-card${selected?' current':''}`);
   const badges=el('div','','badges');badges.append(el('span',labels[row.kind]||row.kind,'tag'),el('span',row.enabled?'启用':'停用',`tag ${row.enabled?'ok':'error'}`));if(selected)badges.append(el('span','实体提取当前模型','tag ok'));
   card.append(badges,el('h3',row.name),el('code',`${row.provider} / ${row.model}`),el('p',`${row.protocol} · v${row.revision} · ${row.api_key_set?'API key 已配置':'缺少 API key'}`),el('p',row.base_url));if(row.notes)card.append(el('p',row.notes));
   const actions=el('div','','actions');const edit=el('button','编辑','ghost');edit.onclick=()=>open(row.id);actions.append(edit);
   if(row.kind==='text'&&row.protocol==='openai_chat'&&row.enabled){const bind=el('button',selected?'已用于实体提取':'用于实体提取',selected?'ghost':'primary');bind.disabled=selected;bind.onclick=()=>bindModel(row.id);actions.append(bind);}
   if(row.kind==='image'&&row.provider==='comfyui'&&row.enabled){const b=el('button',row.id===data.image_generation_id?'当前图像模型':'用于图像生成','primary');b.disabled=row.id===data.image_generation_id;b.onclick=async()=>{try{await api(`${endpoint}/${row.id}/image-generation`,{method:'PUT'});await load();}catch(e){message(e.message);}};actions.append(b);}
   card.append(actions);$('models').append(card);
  }
  $('listStatus').textContent=data.items.length?`本页 ${data.items.length} 个模型`:'暂无配置，可以从左侧新增。';$('prev').disabled=offset===0;$('next').disabled=!data.has_more;$('pageLabel').textContent=`第 ${Math.floor(offset/pageSize)+1} 页`;
 }catch(error){if(current!==sequence)return;$('listStatus').textContent=error.message;$('connectionState').textContent='读取失败';$('connectionState').className='tag error';$('models').replaceChildren();$('prev').disabled=offset===0;$('next').disabled=true;}
}
async function open(id){
 if(busy)return;lock(true);message();
 try{const row=await api(`${endpoint}/${id}`);editing=row;for(const field of ['name','kind','provider','protocol','model','notes'])$(field).value=row[field]||'';$('baseUrl').value=row.base_url;$('apiKey').value='';$('enabled').checked=row.enabled;$('capabilities').value=(row.capabilities||[]).join(', ');$('parameters').value=JSON.stringify(row.parameters||{},null,2);$('formTitle').textContent=`编辑 · ${row.name}`;$('revision').textContent=`版本 ${row.revision}`;$('keyState').textContent=row.api_key_set?'已保存 API key；留空保留，填写新值则替换。':'尚未配置 API key。';window.scrollTo({top:0,behavior:'smooth'});}
 catch(error){message(error.message);}finally{lock(false);}
}
async function bindModel(id){if(busy)return;lock(true);message();try{await api(`${endpoint}/${id}/entity-extraction`,{method:'PUT'});message('实体提取绑定已更新，下一次请求即生效。');}catch(error){message(error.message);}finally{lock(false);await load();}}
$('modelForm').onsubmit=async event=>{
 event.preventDefault();if(busy)return;
 let parameters;try{parameters=JSON.parse($('parameters').value||'{}');if(!parameters||Array.isArray(parameters)||typeof parameters!=='object')throw Error();}catch{message('调用参数必须是合法 JSON 对象。');return;}
 if(!editing&&!$('apiKey').value.trim()&&!($('provider').value.trim()==='comfyui'&&$('kind').value==='image'&&$('protocol').value==='custom_http')){message('新增模型需要填写 API key。');return;}
 const payload={name:$('name').value.trim(),kind:$('kind').value,provider:$('provider').value.trim(),protocol:$('protocol').value,model:$('model').value.trim(),base_url:$('baseUrl').value.trim(),api_key:$('apiKey').value.trim()||null,enabled:$('enabled').checked,capabilities:$('capabilities').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),parameters,notes:$('notes').value,revision:editing?.revision||null};
 lock(true);message();
 try{const row=await api(editing?`${endpoint}/${editing.id}`:endpoint,{method:editing?'PUT':'POST',body:JSON.stringify(payload)});editing=row;$('apiKey').value='';$('revision').textContent=`版本 ${row.revision}`;$('formTitle').textContent=`编辑 · ${row.name}`;$('keyState').textContent='已保存 API key；留空保留，填写新值则替换。';message('配置已保存。首次使用时，在列表中点击“用于实体提取”。');offset=0;}
 catch(error){message(error.message);}finally{lock(false);await load();}
};
$('kind').onchange=()=>{$('protocol').value={text:'openai_chat',speech:'openai_audio',image:'openai_images',video:'custom_http'}[$('kind').value];};
$('newModel').onclick=reset;$('connect').onclick=()=>{offset=0;load();};$('refresh').onclick=load;$('prev').onclick=()=>{offset=Math.max(0,offset-pageSize);load();};$('next').onclick=()=>{offset+=pageSize;load();};
