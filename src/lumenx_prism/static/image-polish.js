"use strict";
(()=>{
 const activeKey='prism.images.activePolish', pendingKey='prism.images.pendingPolish';
 let working=false,revision=0,review=null;
 const get=k=>{try{return JSON.parse(sessionStorage.getItem(k));}catch{return null;}};
 const set=(k,v)=>{try{sessionStorage.setItem(k,JSON.stringify(v));}catch{}};
 const remove=k=>{try{sessionStorage.removeItem(k);}catch{}};
 const message=t=>{$('status').textContent=t;};
 function refreshButton(){$('polishDescription').disabled=working||busy;$('polishDescription').textContent=working?'润色中…':'润色';}
 window.PrismPolish={refreshButton};
 function input(){const character=$('assetType').value==='character';return {
  asset_type:$('assetType').value,name:$('name').value,description:$('description').value,style:$('style').value,prompt:$('prompt').value,
  character_card:character?{name:$('name').value,role:$('role').value,age_range:$('age').value,body_type:$('body').value,hairstyle:$('hair').value,costume:$('costume').value,props:lines('props'),immutable_traits:lines('traits'),expressions:lines('expressions')}:null};}
 function readable(value){const card=value.character_card;let text='描述：'+(value.description||'未填写');if(card){for(const [key,label] of [['name','姓名'],['role','作用'],['age_range','年龄'],['body_type','体型'],['hairstyle','发型'],['costume','服装'],['immutable_traits','身份特征'],['props','道具'],['expressions','表情']])text+='\n'+label+'：'+(Array.isArray(card[key])?card[key].join(' / '):card[key]||'未填写');}return text;}
 function stale(){return !review||JSON.stringify(input())!==JSON.stringify(review.input);}
 function checkStale(){if(!review)return;const changed=stale();$('applyPolish').disabled=changed||working||busy;$('polishState').textContent=changed?'表单在润色后发生了变化，请关闭后基于当前输入重新润色。':'确认后将同步更新描述和建议中的角色细节，参考图、画风与生成参数保持当前选择。';}
 function show(row,snapshot){review={input:snapshot,job:row.id,suggestion:row.result.suggestion};const suggestion=review.suggestion;
  $('polishBefore').textContent=readable(snapshot);$('polishDescriptionText').value=suggestion.description;
  $('polishAfter').textContent=suggestion.character_card?readable({description:'见上方',character_card:suggestion.character_card}):'';
  $('polishWarnings').replaceChildren();if(suggestion.warnings.length){const title=document.createElement('p');title.textContent='冲突处理与待确认内容';const list=document.createElement('ul');for(const text of suggestion.warnings){const li=document.createElement('li');li.textContent=text.replace(/\bdescription\b/g,'外貌描述').replace(/\bage_range\b/g,'年龄').replace(/\bcostume\b/g,'服装').replace(/\bprops\b/g,'道具').replace(/\bimmutable_traits\b/g,'身份特征');list.append(li);}$('polishWarnings').append(title,list);}
  checkStale();if(!$('polishDialog').open)$('polishDialog').showModal();
 }
 async function poll(id,snapshot,seq){let pauses=0;while(seq===revision){
  try{const row=await(await api('/v1/jobs/'+encodeURIComponent(id))).json();pauses=0;
   if(row.status==='succeeded'){remove(activeKey);working=false;refreshButton();show(row,snapshot);message('润色完成，确认前后对比后再应用。');return;}
   if(row.status==='failed'){remove(activeKey);throw Error(row.error_code==='image_polish_changed_constraints'?'润色结果改变了已指定的角色细节，已拒绝应用，请重试。':row.error_code||'润色失败');}
   message('描述润色：'+(row.status==='queued'?'排队中':'处理中')+' · 可离开后重新打开页面查询');
  }catch(e){pauses++;if(pauses>=3||!get(activeKey)){working=false;refreshButton();message(e.message+'；图片生成未提交。');return;}}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }}
 $('polishDescription').onclick=async()=>{
  if(working||busy)return;const snapshot=input();if(!snapshot.description.trim()&&!snapshot.prompt.trim()&&!snapshot.character_card){message('请先填写描述。');return;}
  working=true;refreshButton();message('正在提交描述润色…');
  try{const body=JSON.stringify(snapshot);let item=get(pendingKey);if(!item||item.body!==body){item={body,key:PrismRequests.createRequestId()};set(pendingKey,item);}
   const row=await(await api('/v1/images/polish/jobs',{method:'POST',headers:{'Idempotency-Key':item.key},body})).json();remove(pendingKey);set(activeKey,{id:row.id,input:snapshot});const seq=++revision;await poll(row.id,snapshot,seq);
  }catch(e){working=false;refreshButton();message((e.message.includes('image_polish_model_not_configured')?'请在模型配置页将一个文本模型绑定为图像描述润色。':e.message)+'；提交未确认时保持输入重试会复用标识。');}
 };
 $('closePolish').onclick=$('cancelPolish').onclick=()=>$('polishDialog').close();
 $('polishDialog').addEventListener('click',event=>{if(event.target===$('polishDialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
 document.querySelector('main').addEventListener('input',checkStale);document.querySelector('main').addEventListener('change',checkStale);
 $('applyPolish').onclick=()=>{
  if(stale()||busy){checkStale();return;}const description=$('polishDescriptionText').value.trim();if(!description||description.length>6000){$('polishState').textContent='描述必须为1～6000字。';return;}
  const card=review.suggestion.character_card;$('description').value=description;
  if(card){for(const [field,key] of [['name','name'],['role','role'],['age','age_range'],['body','body_type'],['hair','hairstyle'],['costume','costume']])$(field).value=card[key];for(const [field,key] of [['props','props'],['traits','immutable_traits'],['expressions','expressions']])$(field).value=card[key].join('\n');}
  sourceContext={...sourceContext,polish_job_id:review.job};pending=null;$('sampleNote').textContent='已应用润色建议；请预览提示词，或提交新的图片任务。';$('polishDialog').close();message('已应用润色，尚未提交图片生成。');review=null;
 };
 refreshButton();const active=get(activeKey);if(active?.id&&active?.input){working=true;refreshButton();poll(active.id,active.input,++revision);}
})();
