'use strict';
const $=id=>document.getElementById(id);
let references=[],pending=null,timer=null,urls=[],busy=false,historySequence=0,autoSize=true;
const tokenStorageKey='prism.images.serviceToken';
function savedToken(value){try{if(value===undefined)return sessionStorage.getItem(tokenStorageKey)||'';if(value)sessionStorage.setItem(tokenStorageKey,value);else sessionStorage.removeItem(tokenStorageKey);}catch{}return '';}
const lines=id=>$(id).value.split('\n').map(x=>x.trim()).filter(Boolean);
async function api(path,options={}){
 const headers={...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers};
 if($('token').value.trim())headers.Authorization='Bearer '+$('token').value.trim();
 const response=await fetch(path,{...options,headers,cache:'no-store',credentials:'omit'});
 if(!response.ok){let data;try{data=await response.json();}catch{}if(response.status===401)throw Error('请填写正确的服务访问令牌（AI_SERVICE_API_KEY），然后点击连接 / 刷新历史。');throw Error(data?.detail?.code||JSON.stringify(data?.detail)||`HTTP ${response.status}`);}
 return response;
}
function payload(){
 const character=$('assetType').value==='character';
 if(character&&$('generationType').value==='three_view'&&!references.length)throw Error('三视图需要参考图：请上传图片，或将已生成的角色图作为身份参考。');
 return {asset_type:$('assetType').value,generation_type:character?$('generationType').value:'image',
 name:$('name').value,description:$('description').value,style:$('style').value,prompt:$('prompt').value,negative_prompt:$('negative').value,
 character_card:character?{name:$('name').value,role:$('role').value,age_range:$('age').value,body_type:$('body').value,hairstyle:$('hair').value,costume:$('costume').value,props:lines('props'),immutable_traits:lines('traits'),expressions:lines('expressions')}:null,
 reference_media_ids:references.map(x=>x.id),width:Number($('width').value),height:Number($('height').value),batch_size:Number($('batch').value),seed:Number($('seed').value),model_config_id:restoredModelConfigId,source_context:sourceContext};
}
function refLabel(){$('references').textContent=references.length?references.map((r,i)=>`${i+1}: ${r.id}`).join(' / '):'无参考图片';}
async function upload(){
 for(const file of $('files').files){
  if(references.length>=4)throw Error('最多 4 张参考图片');
  const form=new FormData();form.append('file',file);
  references.push(await (await api('/v1/media',{method:'POST',body:form})).json());refLabel();
 }
 $('files').value='';
}
function lock(value){busy=value;$('restoreInput').disabled=value||!currentJob;for(const id of ['generate','preview','files','sample','clearRefs'])$(id).disabled=value;}
async function show(row){
 currentJob=row.type==='image_generation'?row:null;$('restoreInput').disabled=busy||!currentJob?.request;
 $('jobId').value=row.id;
 for(const item of $('history').children)item.setAttribute('aria-current',String(item.dataset.jobId===row.id));$('meta').textContent=`${row.status} / ${row.phase} · ${row.duration_ms===null?'':(row.duration_ms/1000)+' 秒'} ${row.error_code||''}`;
 $('raw').textContent=JSON.stringify(row,null,2);
 if(row.status==='succeeded'||row.status==='failed')await loadImageHistory();
 if(row.status==='queued'||row.status==='running'){timer=setTimeout(()=>lookup(row.id),2000);return;}
 for(const url of urls)URL.revokeObjectURL(url);urls=[];$('results').replaceChildren();
 for(const artifact of row.result?.artifacts||[]){
  const response=await api(`/v1/media/${artifact.id}/download`);const url=URL.createObjectURL(await response.blob());urls.push(url);
  const figure=document.createElement('figure'),img=document.createElement('img'),a=document.createElement('a'),button=document.createElement('button');
  img.src=url;img.alt='生成候选';a.href=url;a.download=`${artifact.id}.png`;a.textContent='查看 / 下载原图';a.target='_blank';a.rel='noopener';
  button.textContent=row.request?.asset_type==='character'?'作为身份参考':'作为参考图';button.onclick=()=>{references=[artifact];refLabel();$('status').textContent='已选择参考图，可生成派生视图。';};
  figure.append(img,a,button);
  const next={full_body:['headshot','准备下一步：头像'],headshot:['three_view','准备下一步：三视图'],three_view:['reference_sheet','准备下一步：参考卡']}[row.request?.generation_type];
  if(next&&row.request?.asset_type==='character'){const advance=document.createElement('button');advance.textContent=next[1];advance.onclick=()=>prepareNextStage(row,artifact,next[0]);figure.append(advance);}
  $('results').append(figure);
 }
}
async function lookup(id=$('jobId').value.trim()){
 clearTimeout(timer);if(!id)return;
 try{const changed=id!==$('jobId').value;const row=await(await api('/v1/jobs/'+encodeURIComponent(id))).json();await show(row);if(changed){$('snapshot').open=false;document.querySelector('.result-scroll').scrollTop=0;}}catch(e){$('meta').textContent=e.message;}
}
async function loadImageHistory(){
 const sequence=++historySequence;
 $('connect').disabled=true;$('connection').textContent='正在读取历史…';$('historyStatus').textContent='正在读取历史…';
 try{
  const data=await(await api('/v1/jobs?limit=30&type=image_generation')).json();
  if(sequence!==historySequence)return;
  savedToken($('token').value.trim());$('history').replaceChildren();
  const rows=data.items.filter(x=>x.type==='image_generation');
  const states={queued:'排队中',running:'处理中',succeeded:'成功',failed:'失败'};
  for(const row of rows){
   const b=document.createElement('button');b.className='history-row';b.dataset.jobId=row.id;b.setAttribute('aria-current',String($('jobId').value===row.id));
   const date=row.created_at?new Date(row.created_at).toLocaleString('zh-CN',{hour12:false}):'';
   b.textContent=`${row.id.slice(0,8)} · ${states[row.status]||row.status} · ${date}`;
   b.onclick=()=>lookup(row.id);$('history').append(b);
  }
  $('connection').textContent='已连接';
  $('historyStatus').textContent=rows.length?`最近 ${rows.length} 条图片任务，点击查看结果。`:'暂无图片任务。提交生成后可在这里查看。';
 }catch(e){
  if(sequence!==historySequence)return;
  $('connection').textContent=e.message;$('historyStatus').textContent='历史加载失败：'+e.message;
 }finally{if(sequence===historySequence)$('connect').disabled=false;}
}

$('generate').onclick=async()=>{
 if(busy)return;lock(true);$('status').textContent='上传参考图片并提交任务…';
 try{await upload();const body=JSON.stringify(payload());if(!pending||pending.body!==body)pending={body,key:crypto.randomUUID()};
 const row=await(await api('/v1/images/generate/jobs',{method:'POST',headers:{'Idempotency-Key':pending.key},body})).json();
 pending=null;$('status').textContent='已提交，刷新后可用任务编号继续查询。';await show(row);await loadImageHistory();
 }catch(e){$('status').textContent=e.message+'；提交超时后原输入重试会复用提交标识。';}finally{lock(false);}
};
$('preview').onclick=async()=>{lock(true);$('status').textContent='正在预览提示词…';try{await upload();$('raw').textContent=JSON.stringify(await(await api('/v1/images/preview',{method:'POST',body:JSON.stringify(payload())})).json(),null,2);$('snapshot').open=true;$('snapshot').scrollIntoView({block:'nearest',behavior:'smooth'});$('status').textContent='提示词已显示在右侧任务快照中。';}catch(e){$('status').textContent=e.message;}finally{lock(false);}};
$('connect').onclick=()=>loadImageHistory();$('lookup').onclick=()=>lookup();$('clearRefs').onclick=()=>{references=[];$('files').value='';refLabel();};
function recommendedDimensions(){
 if($('assetType').value==='scene')return [1344,768];
 if($('assetType').value!=='character')return [1024,1024];
 return {full_body:[768,1344],headshot:[768,1024],three_view:[1344,768],reference_sheet:[1344,768]}[$('generationType').value]||[768,1344];
}
function updateGenerationHint(){
 const help={full_body:'单人、单视角、从头到脚。可从文字开始设计；选定形象后再生成其他角度。',headshot:'单人头肩近景。可直接文生图；延续已有角色时请添加参考图。',three_view:'同一角色的正面、侧面、背面。需上传或选择已确认的角色参考图。',reference_sheet:'汇总主 3/4、正侧背、表情、服装、道具和身份特征。建议使用已确认的参考素材。'};
 $('generationHelp').textContent=help[$('generationType').value]||'';
 const [w,h]=recommendedDimensions();$('sizeHint').textContent=`推荐 ${w} × ${h}；尺寸可手动修改。`;
}
function useRecommendedSize(){const [w,h]=recommendedDimensions();$('width').value=w;$('height').value=h;autoSize=true;updateGenerationHint();}
function updateAssetFields(){const character=$('assetType').value==='character';$('generationType').disabled=!character;document.querySelector('.character-details').hidden=!character;for(const field of document.querySelectorAll('.character-field'))field.hidden=!character;updateGenerationHint();}
$('generationType').onchange=()=>{updateGenerationHint();if(autoSize)useRecommendedSize();};
$('recommendedSize').onclick=useRecommendedSize;
for(const id of ['width','height'])$(id).addEventListener('input',()=>{autoSize=false;});
$('assetType').onchange=()=>{updateAssetFields();if(autoSize)useRecommendedSize();};

$('token').value=savedToken();
$('token').addEventListener('input',()=>savedToken(''));
loadImageHistory();

// Samples are loaded from Prism MongoDB; selecting one never starts generation.
let sampleCatalog=[],sampleKind='style',sampleSequence=0,currentJob=null,restoredModelConfigId=null,sourceContext={};
const sampleImageUrls=new Map();
const sampleKindNames={style:'风格',character:'人物',scene:'场景',prop:'道具'};
function clearInputReferences(){references=[];$('files').value='';refLabel();}
function applySubject(request){
 const card=request.character_card||{};
 const values={assetType:request.asset_type||'image',generationType:request.asset_type==='character'?request.generation_type:'full_body',
  name:request.name||'',role:card.role||'',description:request.description||'',age:card.age_range||'',body:card.body_type||'',hair:card.hairstyle||'',costume:card.costume||'',
  props:(card.props||[]).join('\n'),traits:(card.immutable_traits||[]).join('\n'),expressions:(card.expressions||['neutral','happy','sad','angry']).join('\n')};
 for(const [id,value] of Object.entries(values))$(id).value=value;
 $('generationType').disabled=request.asset_type!=='character';
 updateAssetFields();
}
function useSample(sample){
 if(busy)return;
 pending=null;
 if(sample.kind==='style'){
  $('style').value=sample.positive_prompt;$('negative').value=sample.negative_prompt||'';
  $('selectedStyle').textContent='风格：'+sample.name;
  sourceContext.style_sample_id=sample.id;
 }else{
  applySubject({...sample.request,generation_type:sample.kind==='character'?'full_body':sample.request.generation_type});if(autoSize)useRecommendedSize();clearInputReferences();$('prompt').value='';
  sourceContext.asset_sample_id=sample.id;
  sourceContext.sample_source_job_id=sample.source.job_id||'';
  $('selectedSubject').textContent='样本：'+sample.name;
 }
 $('sampleNote').textContent=sample.kind==='style'?'已应用风格提示词和负面词；配图用于选型预览。':(sample.sample_notes||'样本已填入，可自由编辑。');
 $('status').textContent=`已载入${sampleKindNames[sample.kind]}「${sample.name}」，点击提交生成后执行。`;
 $('samplesDialog').close();document.querySelector('.editor-fields').scrollTop=0;
}
async function renderSamples(){
 const sequence=++sampleSequence;
 const search=$('sampleSearch').value.trim().toLowerCase(),category=$('sampleCategory').value;
 const rows=sampleCatalog.filter(r=>r.kind===sampleKind&&(!category||r.category===category)&&
  [r.name,r.description,r.category_name,...(r.best_for||[])].join(' ').toLowerCase().includes(search));
 $('sampleGrid').replaceChildren();$('sampleCount').textContent=`${rows.length} 个${sampleKindNames[sampleKind]}样本`;
 $('sampleCategory').hidden=sampleKind!=='style';
 for(const tab of $('sampleTabs').children)tab.setAttribute('aria-pressed',String(tab.dataset.kind===sampleKind));
 if(!rows.length){$('sampleGrid').textContent='暂无匹配样本，请调整筛选条件。';return;}
 for(const sample of rows){
  const card=document.createElement('article');card.className='sample-card';
  const image=sample.preview_media_id?document.createElement('img'):null;
  if(image){image.alt=sample.name+'风格参考图';image.loading='lazy';image.style.objectPosition=sample.object_position||'center';card.append(image);
   (async()=>{try{let url=sampleImageUrls.get(sample.preview_media_id);if(!url){url=URL.createObjectURL(await(await api(`/v1/media/${sample.preview_media_id}/download`)).blob());sampleImageUrls.set(sample.preview_media_id,url);}if(sequence===sampleSequence)image.src=url;}catch{image.alt=sample.name+'（配图加载失败）';}})();
  }
  const title=document.createElement('h3');title.textContent=sample.name;
  const categoryText=document.createElement('span');categoryText.className='hint';categoryText.textContent=sample.category_name||`来源：实体提取任务 ${sample.source.job_id?.slice(0,8)||''}`;
  const description=document.createElement('p');description.textContent=sample.description||sample.sample_notes||'';
  const note=document.createElement('small');note.textContent=sample.sample_notes||(sample.best_for?.length?'适合：'+sample.best_for.join(' / '):'');
  const button=document.createElement('button');button.textContent=sample.kind==='style'?'应用此风格':'载入此样本';button.onclick=()=>useSample(sample);
  card.append(title,categoryText,description,note,button);$('sampleGrid').append(card);
 }
}
async function openSamples(){
 if(busy)return;
 $('samplesDialog').showModal();$('sampleCount').textContent='正在读取样本库…';
 try{
  const data=await(await api('/v1/image-samples?limit=100')).json();sampleCatalog=data.items;
  const selected=$('sampleCategory').value;$('sampleCategory').replaceChildren(new Option('全部风格分类',''));
  const categories=new Map(sampleCatalog.filter(x=>x.kind==='style').map(x=>[x.category,x.category_name]));
  for(const [value,name] of categories)$('sampleCategory').append(new Option(name,value));
  $('sampleCategory').value=selected;await renderSamples();
 }catch(e){$('sampleCount').textContent='样本加载失败：'+e.message;}
}
$('sample').onclick=openSamples;$('closeSamples').onclick=()=>$('samplesDialog').close();
$('sampleSearch').oninput=renderSamples;$('sampleCategory').onchange=renderSamples;
for(const tab of $('sampleTabs').children)tab.onclick=()=>{sampleKind=tab.dataset.kind;$('sampleCategory').value='';renderSamples();};
function restoreRequest(request){
 applySubject(request);
 for(const id of ['style','prompt'])$(id).value=request[id]||'';
 $('negative').value=request.negative_prompt||'';
 for(const id of ['width','height','seed'])$(id).value=request[id]??({width:1344,height:768,seed:0}[id]);
 $('batch').value=request.batch_size||1;
 references=(request.reference_media_ids||[]).map(id=>({id}));$('files').value='';refLabel();
 restoredModelConfigId=request.model_config_id||null;sourceContext={...(request.source_context||{})};pending=null;autoSize=false;
 const style=sampleCatalog.find(x=>x.id===sourceContext.style_sample_id);
 $('selectedStyle').textContent='风格：'+(style?.name||'历史输入');$('selectedSubject').textContent='样本：'+(request.name||'历史输入');
 document.querySelector('.editor-fields').scrollTop=0;updateGenerationHint();
}
$('restoreInput').onclick=()=>{
 if(busy||!currentJob?.request)return;
 restoreRequest(currentJob.request);
 $('sampleNote').textContent='已恢复原始输入、参考图与生成参数，可编辑后创建新任务。';
 $('status').textContent='原输入已恢复；点击提交生成才会创建新任务。';
};
function prepareNextStage(row,artifact,target){
 if(busy)return;
 restoreRequest(row.request);$('generationType').value=target;
 const previous=target==='headshot'?[]:(row.request.reference_media_ids||[]);
 references=[...new Set([...previous,artifact.id])].slice(0,4).map(id=>({id}));
 if(!references.some(r=>r.id===artifact.id))references[references.length-1]={id:artifact.id};
 refLabel();useRecommendedSize();updateAssetFields();
 $('sampleNote').textContent='已沿用该任务的角色、画风与服装，并带入参考图。';
 $('status').textContent='下一步输入已准备好，检查后点击“提交生成”。';
}
updateAssetFields();
window.addEventListener('beforeunload',()=>{for(const url of sampleImageUrls.values())URL.revokeObjectURL(url);});
