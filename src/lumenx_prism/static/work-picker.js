(()=>{
const root=document.getElementById('workSource');if(!root)return;
const make=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
const multi=root.dataset.multiple==='true',work=make('select'),edition=make('select'),episode=make('select');
for(const [select,label] of [[work,'选择作品'],[edition,'切换分集方案'],[episode,'选择集']])select.setAttribute('aria-label',label);
if(multi){episode.multiple=true;episode.size=4;}
const hint=make('p','先选择作品，再选择要提取或制作的集。'),row=make('div');row.className='row';row.append(work,episode);
const info=make('p','尚未选择作品');info.className='work-edition-info';
const advanced=make('details');advanced.className='work-edition-history';const summary=make('summary','切换历史分集方案');advanced.append(summary,make('p','分集方案是在作品库确认的每集标题、原文范围与制作约定。默认使用最新方案；历史任务仍使用创建时的方案。'),edition);
const load=make('button','载入选择'),refresh=make('button','刷新作品列表'),actions=make('div');actions.className='row';if(root.dataset.autoApply==='true'){hint.textContent='选择作品和集后自动更新镜头方案，无需再次载入。';actions.append(refresh);}else actions.append(load,refresh);
if(multi){const all=make('button','全选集');all.onclick=()=>run(async()=>{for(const o of episode.options)o.selected=!!o.value;await apply();});actions.append(all);}
root.replaceChildren(hint,row,info,advanced,actions);
let busy=false,editionsList=[];
async function get(url){const token=document.getElementById('token')?.value||'';const r=await fetch(url,{cache:'no-store',headers:token?{Authorization:'Bearer '+token}:{}});const d=await r.json();if(!r.ok)throw Error(d.detail?.code||'作品读取失败');return d;}
const run=async fn=>{if(busy)return;busy=true;for(const n of [work,edition,episode,load,refresh])n.disabled=true;try{await fn();}catch(e){hint.textContent=e.message;}finally{busy=false;for(const n of [work,episode,refresh])n.disabled=false;edition.disabled=editionsList.length<2;load.disabled=!edition.value||!episode.value;}};
function clearScope(){window.PrismSelectedWork=null;window.PrismClearWorkSelection?.();if(location.pathname.endsWith('/images')){if(typeof loadImageHistory==='function')loadImageHistory();}}
async function works(){let rows=[],offset=0,d;do{d=await get('/v1/works?limit=100&offset='+offset);rows.push(...d.items);offset+=100;}while(d.has_more&&offset<=10000);work.replaceChildren(new Option('选择作品',''));for(const w of rows)work.append(new Option(w.title,w.id));}
async function editions(preferred){edition.replaceChildren();episode.replaceChildren();editionsList=[];advanced.hidden=true;advanced.open=false;info.textContent='尚未选择作品';if(!work.value)return;const d=await get(`/v1/works/${work.value}/editions`);editionsList=d.items;
if(!editionsList.length){info.replaceChildren(make('span','该作品还没有确认的分集方案。 '));const a=make('a','前往作品库确认分集');a.href='/tools/works?work='+work.value;info.append(a);return;}
for(const [i,e] of editionsList.entries())edition.append(new Option((i===0?'最新方案':'历史方案')+' · '+new Date(e.created_at).toLocaleString('zh-CN'),e.id));
if(preferred){if(!editionsList.some(e=>e.id===preferred))throw Error('原分集方案不可用，请选择现有方案；不会自动替换历史来源。');edition.value=preferred;}
advanced.hidden=false;summary.textContent=editionsList.length>1?'切换历史分集方案（'+editionsList.length+' 份）':'了解分集方案';
}
async function episodes(preferred=[]){episode.replaceChildren();if(!multi)episode.append(new Option('选择集',''));if(!edition.value)return;const d=await get(`/v1/works/${work.value}/editions/${edition.value}`);for(const e of d.content.episodes)episode.append(new Option('第 '+Number(e.id.slice(1))+' 集 · '+e.title,e.id));
const index=editionsList.findIndex(e=>e.id===edition.value);info.textContent='分集方案：'+(index===0?'最新确认':'历史确认')+' · 共 '+d.content.episodes.length+' 集 · '+new Date(d.created_at).toLocaleString('zh-CN');
const selected=preferred.length?preferred:[d.content.episodes[0]?.id];for(const o of episode.options)o.selected=selected.includes(o.value);
if(preferred.some(id=>!Array.from(episode.options).some(o=>o.value===id)))throw Error('原选择的集不在该分集方案中，请重新选择。');
}
async function apply(){const ids=Array.from(episode.selectedOptions).map(o=>o.value).filter(Boolean);if(!work.value||!edition.value||!ids.length)throw Error('请选择作品与集');if(window.PrismWorkPick)await window.PrismWorkPick({work:work.value,edition:edition.value,episodes:ids});else await importWork(work.value,edition.value,ids[0]);hint.textContent='当前：'+work.selectedOptions[0].text+' · '+ids.map(id=>'第 '+Number(id.slice(1))+' 集').join('、')+(multi?'（可多选或全选集）':'');}
work.onchange=()=>run(async()=>{clearScope();await editions();await episodes();if(episode.value)await apply();});
edition.onchange=()=>run(async()=>{clearScope();await episodes();if(episode.value)await apply();});episode.onchange=()=>run(apply);load.onclick=()=>run(apply);
refresh.title='更新作品和分集选项，保留当前选择与制作内容';
refresh.onclick=()=>run(async()=>{
 const old=work.value,version=edition.value,ids=Array.from(episode.selectedOptions).map(o=>o.value).filter(Boolean);
 await works();work.value=old;
 if(old&&!work.value){clearScope();await editions();if(window.PrismRefreshWorkSelection)await window.PrismRefreshWorkSelection();hint.textContent='原作品已不可用，请重新选择。';return;}
 await editions(version);await episodes(ids);
 if(window.PrismRefreshWorkSelection&&await window.PrismRefreshWorkSelection()){hint.textContent=old?'作品列表已更新，当前集与制作内容已保留。':'作品列表已更新，可选择作品与集缩小制作范围。';return;}
 if(episode.value)await apply();
});
window.PrismSyncPicker=async context=>{for(let i=0;busy&&i<100;i++)await new Promise(r=>setTimeout(r,30));return run(async()=>{if(!context)return;work.value=context.work_id;await editions(context.edition_id);await episodes([context.episode_id]);hint.textContent='当前：'+work.selectedOptions[0]?.text+' · 第 '+Number(context.episode_id.slice(1))+' 集';});};
run(async()=>{await works();const q=new URLSearchParams(location.search);if(q.get('work')){work.value=q.get('work');await editions(q.get('edition'));await episodes(q.get('episode')?[q.get('episode')]:[]);if(episode.value)await apply();}else await editions();});
})();
