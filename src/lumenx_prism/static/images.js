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
 const rawSeed=$('seed').value;const seed=Number(rawSeed);
 if(!/^\d+$/.test(rawSeed)||!Number.isSafeInteger(seed)||seed<0||seed>Number.MAX_SAFE_INTEGER)throw Error('种子必须是 0 ～ 9007199254740991 之间的整数。');
 if(character&&$('generationType').value==='three_view'&&!references.length)throw Error('三视图需要参考图：请上传图片，或将已生成的角色图作为身份参考。');
 return {asset_type:$('assetType').value,generation_type:character?$('generationType').value:'image',
 name:$('name').value,description:$('description').value,style:$('style').value,prompt:$('prompt').value,negative_prompt:$('negative').value,
 character_card:character?{name:$('name').value,role:$('role').value,age_range:$('age').value,body_type:$('body').value,hairstyle:$('hair').value,costume:$('costume').value,props:lines('props'),immutable_traits:lines('traits'),expressions:lines('expressions')}:null,
 reference_media_ids:references.map(x=>x.id),width:Number($('width').value),height:Number($('height').value),batch_size:Number($('batch').value),seed,model_config_id:restoredModelConfigId,source_context:sourceContext};
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
function lock(value){busy=value;$('restoreInput').disabled=value||!currentJob;for(const id of ['generate','preview','files','chooseCharacterSample','chooseSceneSample','choosePropSample','chooseStyle','clearRefs','randomSeed'])$(id).disabled=value;window.PrismPolish?.refreshButton();}
async function show(row){
 currentJob=row.type==='image_generation'?row:null;$('restoreInput').disabled=busy||!currentJob?.request;
 $('jobId').value=row.id;
 for(const item of $('history').children)item.setAttribute('aria-current',String(item.dataset.jobId===row.id));$('meta').textContent=`${row.scheduling_paused?'已暂缓 / ':''}${row.status} / ${row.phase} · ${row.duration_ms===null?'':(row.duration_ms/1000)+' 秒'} ${row.error_code||''}`;
 $('raw').textContent=JSON.stringify(row,null,2);
 if(row.status==='succeeded'||row.status==='failed')await loadImageHistory();
 if(row.status==='queued'||row.status==='running'){timer=setTimeout(()=>lookup(row.id),2000);return;}
 for(const url of urls)URL.revokeObjectURL(url);urls=[];$('results').replaceChildren();
 for(const artifact of row.result?.artifacts||[]){
  const response=await api(`/v1/media/${artifact.id}/download`);const url=URL.createObjectURL(await response.blob());urls.push(url);
  const figure=document.createElement('figure'),img=document.createElement('img'),a=document.createElement('a'),button=document.createElement('button');
  img.src=url;img.alt='生成候选';a.href=url;a.download=`${artifact.id}.png`;a.textContent='下载原图';a.target='_blank';a.rel='noopener';
  button.textContent=row.request?.asset_type==='character'?'作为身份参考':'作为参考图';button.onclick=()=>{references=[artifact];refLabel();$('status').textContent='已选择参考图，可生成派生视图。';};
  const view=document.createElement('button');view.className='result-preview';view.setAttribute('aria-label','查看生成图片原图');view.title='点击查看原图';view.append(img);view.onclick=()=>openImageViewer({url,title:row.request?.name||'生成图片',filename:artifact.id+'.png'});
  figure.append(view,a,button);
  if(['character','scene','prop'].includes(row.request?.asset_type)){
   const use=document.createElement('button');use.textContent='用于分镜';use.onclick=()=>{
    try{const name=row.request.name||row.request.character_card?.name||'素材';let chosen=JSON.parse(sessionStorage.getItem('prism.production.assets')||'[]');
     chosen=chosen.filter(x=>x.name!==name);chosen.push({name,kind:row.request.asset_type,media_id:artifact.id,description:row.request.description||JSON.stringify(row.request.character_card||{})});
     sessionStorage.setItem('prism.production.assets',JSON.stringify(chosen));location.href='/tools/storyboards';
    }catch(e){$('status').textContent=e.message;}
   };figure.append(use);
  }
  const next={full_body:['headshot','准备下一步：头像'],headshot:['three_view','准备下一步：三视图'],three_view:['reference_sheet','准备下一步：参考卡']}[row.request?.generation_type];
  if(next&&row.request?.asset_type==='character'){const advance=document.createElement('button');advance.textContent=next[1];advance.onclick=()=>prepareNextStage(row,artifact,next[0]);figure.append(advance);}
  $('results').append(figure);
 }
}
async function lookup(id=$('jobId').value.trim()){
 clearTimeout(timer);if(!id)return;
 try{const changed=id!==$('jobId').value;const row=await(await api('/v1/jobs/'+encodeURIComponent(id))).json();await show(row);if(changed){$('snapshot').open=false;document.querySelector('.result-scroll').scrollTop=0;}}catch(e){$('meta').textContent=e.message;}
}
let historyOffset=0,historyKind='',historyScopeKey='',historySearchTimer;
async function loadImageHistory(){
 const sequence=++historySequence,scope=window.PrismSelectedWork;
 $('historyPrev').disabled=true;$('historyNext').disabled=true;
 if(!scope){$('connect').disabled=false;$('historyPage').textContent='第 1 页';for(const b of $('historyKinds').children)b.textContent=({character:'人物',scene:'场景',prop:'道具'}[b.dataset.kind]||'全部')+'（0）';$('history').replaceChildren();$('historyScope').textContent='请选择作品和集';$('historyStatus').textContent='选择后显示该作品与所选集的图片任务。';$('connection').textContent='';return;}
 const scopeKey=JSON.stringify(scope);if(scopeKey!==historyScopeKey){historyScopeKey=scopeKey;historyOffset=0;}
 const picker=document.getElementById('workSource');const title=picker.querySelector('[aria-label="选择作品"]')?.selectedOptions[0]?.text||'当前作品';
 $('historyScope').textContent=title+' · '+scope.episodes.map(e=>'第 '+Number(e.slice(1))+' 集').join('、');
 $('connect').disabled=true;$('connection').textContent='正在读取历史…';
 try{
  const query=new URLSearchParams({limit:30,offset:historyOffset,type:'image_generation',work_id:scope.work,edition_id:scope.edition,episode_ids:scope.episodes.join(','),name_query:$('historySearch').value.trim()});if(historyKind)query.set('asset_type',historyKind);
  const data=await(await api('/v1/jobs?'+query)).json();if(sequence!==historySequence||JSON.stringify(window.PrismSelectedWork)!==scopeKey)return;
  savedToken($('token').value.trim());$('history').replaceChildren();
  const states={queued:'排队中',running:'处理中',succeeded:'成功',failed:'失败'},kinds={character:'人物',scene:'场景',prop:'道具',image:'图片'},types={full_body:'主体全身图',headshot:'头像',three_view:'三视图',reference_sheet:'完整角色参考卡',image:'单张图片'};
  for(const b of $('historyKinds').children){const kind=b.dataset.kind;b.textContent=({character:'人物',scene:'场景',prop:'道具'}[kind]||'全部')+'（'+(data.asset_counts?.[kind||'all']||0)+'）';b.setAttribute('aria-pressed',String(kind===historyKind));}
  for(const row of data.items){const b=document.createElement('button');b.className='history-row';b.dataset.jobId=row.id;b.setAttribute('aria-current',String($('jobId').value===row.id));b.title='任务编号：'+row.id;
   const name=document.createElement('strong');name.textContent=(row.display_title||'未命名图片')+' · '+(types[row.generation_type]||kinds[row.asset_type]||'图片');
   const detail=document.createElement('small');detail.textContent=(row.scheduling_paused?'已暂缓':states[row.status]||row.status)+' · '+new Date(row.created_at).toLocaleString('zh-CN',{hour12:false});
   b.append(name,detail);b.onclick=()=>lookup(row.id);$('history').append(b);
  }
  $('connection').textContent='已连接';$('historyStatus').textContent=data.total?`共 ${data.total} 条匹配任务，点击查看结果。`:'当前作品与所选集暂无匹配的图片任务。';$('historyPage').textContent='第 '+(historyOffset/30+1)+' 页';$('historyPrev').disabled=historyOffset===0;$('historyNext').disabled=!data.has_more;
 }catch(e){if(sequence===historySequence){$('connection').textContent=e.message;$('historyStatus').textContent='历史加载失败：'+e.message;}}
 finally{if(sequence===historySequence)$('connect').disabled=false;}
}
for(const b of $('historyKinds').children)b.onclick=()=>{historyKind=b.dataset.kind;historyOffset=0;loadImageHistory();};
$('historySearch').oninput=()=>{clearTimeout(historySearchTimer);historyOffset=0;historySearchTimer=setTimeout(loadImageHistory,250);};
$('historyPrev').onclick=()=>{historyOffset=Math.max(0,historyOffset-30);loadImageHistory();};$('historyNext').onclick=()=>{historyOffset+=30;loadImageHistory();};

$('generate').onclick=async()=>{
 if(busy)return;lock(true);$('status').textContent='上传参考图片并提交任务…';
 try{await upload();const body=JSON.stringify(payload());if(!pending||pending.body!==body)pending={body,key:PrismRequests.createRequestId()};
 const row=await(await api('/v1/images/generate/jobs',{method:'POST',headers:{'Idempotency-Key':pending.key},body})).json();
 pending=null;$('status').textContent='已提交，刷新后可用任务编号继续查询。';await show(row);await loadImageHistory();
 }catch(e){$('status').textContent=e.message+(pending?'；如提交未确认，请保持输入不变重试，将复用提交标识。':'');}finally{lock(false);}
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
function updateDescriptionLabel(){
 const kind=$('assetType').value;
 const names={character:'外貌描述',scene:'场景描述',prop:'道具描述',image:'画面描述'};
 const hints={character:'描述五官、发型、体型和服装',scene:'描述空间、布局、光线和固定地标',prop:'描述外形、材质、颜色和功能',image:'描述希望生成的画面'};
 $('descriptionLabel').textContent=names[kind];$('description').placeholder=hints[kind];
 $('polishDescription').setAttribute('aria-label',names[kind]+'润色，确认后应用');
}
$('randomSeed').onclick=()=>{
 if(busy)return;
 try{let seed=PrismRequests.randomSeed();if(seed===Number($('seed').value))seed=seed===Number.MAX_SAFE_INTEGER?0:seed+1;$('seed').value=String(seed);pending=null;$('status').textContent='已生成随机种子：'+seed+'。';}
 catch(e){$('status').textContent=e.message;}
};
function updateAssetFields(){updateDescriptionLabel();const character=$('assetType').value==='character';$('generationType').disabled=!character;document.querySelector('.character-details').hidden=!character;for(const field of document.querySelectorAll('.character-field'))field.hidden=!character;updateGenerationHint();}
$('generationType').onchange=()=>{updateGenerationHint();if(autoSize)useRecommendedSize();};
$('recommendedSize').onclick=useRecommendedSize;
for(const id of ['width','height'])$(id).addEventListener('input',()=>{autoSize=false;});
$('assetType').onchange=()=>{updateAssetFields();if(autoSize)useRecommendedSize();};

$('token').value=savedToken();
$('token').addEventListener('input',()=>savedToken(''));
loadImageHistory();

// Samples are loaded from Prism MongoDB; selecting one never starts generation.
let sampleCatalog=[],sampleKind='character',sampleSequence=0,currentJob=null,restoredModelConfigId=null,sourceContext={};
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
 if(!rows.length){$('sampleGrid').textContent='暂无匹配样本，请调整筛选条件。';return;}
 for(const sample of rows){
  const card=document.createElement('article');card.className='sample-card';
  const image=sample.preview_media_id?document.createElement('img'):null;
  if(image){image.alt=sample.name+'风格参考图';image.loading='lazy';image.style.objectPosition=sample.object_position||'center';const view=document.createElement('button');view.className='sample-image-button';view.setAttribute('aria-label','查看'+sample.name+'原图');view.title='点击查看原图';view.append(image);view.onclick=()=>openImageViewer({mediaId:sample.original_media_id||sample.preview_media_id,title:sample.name});card.append(view);
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
async function openSamples(mode='character'){
 if(busy)return;
 const styleMode=mode==='style';
 sampleKind=mode;
 $('sampleCategory').hidden=!styleMode;
 $('sampleSearch').value='';$('sampleCategory').value='';
 $('sampleDialogTitle').textContent={style:'选择视觉风格',character:'人物样本选择',scene:'场景样本选择',prop:'道具样本选择'}[mode];
 $('sampleDialogDescription').textContent=styleMode?'点击配图查看原图；应用风格后可继续编辑提示词。':`选择${sampleKindNames[mode]}样本，保留当前风格；载入后可继续编辑。`;
 $('sampleSearch').placeholder=styleMode?'搜索风格、描述或题材':'搜索人物、场景或道具';
 $('sampleFooter').textContent=styleMode?'风格来自 Prism 数据库 · 应用风格提示词和负面词 · 保留主体输入':'样本来自 Prism 数据库 · 保留当前风格，可自由编辑主体';
 $('sampleGrid').replaceChildren();$('samplesDialog').showModal();$('sampleCount').textContent='正在读取样本库…';
 try{
  const data=await(await api('/v1/image-samples?limit=100')).json();sampleCatalog=data.items;
  const selected=$('sampleCategory').value;$('sampleCategory').replaceChildren(new Option('全部风格分类',''));
  const categories=new Map(sampleCatalog.filter(x=>x.kind==='style').map(x=>[x.category,x.category_name]));
  for(const [value,name] of categories)$('sampleCategory').append(new Option(name,value));
  $('sampleCategory').value=selected;await renderSamples();
 }catch(e){$('sampleCount').textContent='样本加载失败：'+e.message;}
}
$('chooseCharacterSample').onclick=()=>openSamples('character');$('chooseSceneSample').onclick=()=>openSamples('scene');$('choosePropSample').onclick=()=>openSamples('prop');$('chooseStyle').onclick=()=>openSamples('style');$('closeSamples').onclick=()=>$('samplesDialog').close();
$('sampleSearch').oninput=renderSamples;$('sampleCategory').onchange=renderSamples;
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

let viewerSequence=0,viewerOwnedUrl=null;
function resetViewer(){
 viewerSequence++;
 $('viewerImage').removeAttribute('src');$('viewerImage').onload=null;
 if(viewerOwnedUrl){URL.revokeObjectURL(viewerOwnedUrl);viewerOwnedUrl=null;}
}
async function openImageViewer({url,mediaId,title='原图',filename='image.png'}){
 resetViewer();const sequence=viewerSequence;
 $('viewerTitle').textContent=title;$('viewerStatus').textContent='正在加载原图…';
 $('viewerStage').classList.remove('original-size');$('viewerZoom').textContent='100% 原始尺寸';$('viewerZoom').disabled=true;$('viewerDownload').hidden=true;
 $('viewerImage').alt=title;
 if(!$('imageViewer').open)$('imageViewer').showModal();
 try{
  if(mediaId){
   const blob=await(await api(`/v1/media/${mediaId}/download`)).blob();
   if(sequence!==viewerSequence)return;
   url=URL.createObjectURL(blob);viewerOwnedUrl=url;
   const ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[blob.type]||'png';
   filename=title+'.'+ext;
  }
  if(sequence!==viewerSequence)return;
  $('viewerImage').onload=()=>{
   if(sequence!==viewerSequence)return;
   $('viewerStatus').textContent=`${$('viewerImage').naturalWidth} × ${$('viewerImage').naturalHeight} · 可切换原始尺寸查看细节`;
   $('viewerZoom').disabled=false;
  };
  $('viewerImage').onerror=()=>{if(sequence===viewerSequence)$('viewerStatus').textContent='图片解码失败，请重新打开。';};
  $('viewerImage').src=url;$('viewerDownload').href=url;$('viewerDownload').download=filename;$('viewerDownload').hidden=false;
 }catch(e){if(sequence===viewerSequence)$('viewerStatus').textContent='原图加载失败：'+e.message;}
}
$('closeViewer').onclick=()=>$('imageViewer').close();
$('imageViewer').addEventListener('close',resetViewer);
$('imageViewer').addEventListener('click',event=>{
 const bounds=$('imageViewer').getBoundingClientRect();
 if(event.target===$('imageViewer')&&(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom))$('imageViewer').close();
});
$('viewerZoom').onclick=()=>{
 const original=$('viewerStage').classList.toggle('original-size');
 $('viewerZoom').textContent=original?'适应窗口':'100% 原始尺寸';$('viewerStage').scrollTop=0;$('viewerStage').scrollLeft=0;
};

{const id=new URLSearchParams(location.search).get('job');if(id)lookup(id);}
