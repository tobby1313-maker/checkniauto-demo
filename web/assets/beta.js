/* Render all report fields as text, never model-generated HTML. */
(() => {
 'use strict';
 const app=document.getElementById('app');
 const el=(tag,value,cls)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;if(cls)n.className=cls;return n;};
 const add=(parent,...nodes)=>{parent.append(...nodes);return parent;};
 const link=(label,href)=>{const a=el('a',label);a.href=href;return a;};
 const button=(label,fn)=>{const b=el('button',label);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){showError(e.message);}finally{b.disabled=false;}});return b;};
 const labels={PREPARING:'Pripravujeme podklady',WAITING_FOR_AI:'Pripravené na pokyn v ChatGPT',PROCESSING:'Analýza je prevzatá na spracovanie',DONE:'Report je pripravený',FAILED:'Príprava alebo analýza zlyhala',PREPARATION_INTERRUPTED:'Príprava bola prerušená'};
 let errorBox,timer;
 function reset(title){app.replaceChildren(el('h1',title));errorBox=el('p','','error');errorBox.hidden=true;errorBox.setAttribute('role','alert');app.append(errorBox);}
 function showError(s){errorBox.textContent=s;errorBox.hidden=false;}
 async function api(path,options={}){const r=await fetch(path,{cache:'no-store',...options});const d=await r.json().catch(()=>({error:'Server nevrátil platnú odpoveď.'}));if(!r.ok)throw new Error(d.error || `Chyba ${r.status}`);return d;}
 const remember=id=>{try{const all=JSON.parse(localStorage.getItem('checkni-beta-jobs') || '[]');localStorage.setItem('checkni-beta-jobs',JSON.stringify([id,...all.filter(x=>x!==id)].slice(0,50)));}catch{}};
 function field(form,label,name,type='text',required=false){const l=el('label',label),input=el(type==='textarea'?'textarea':'input');input.id=name;input.name=name;if(type!=='textarea')input.type=type;input.required=required;l.htmlFor=name;add(form,l,input);return input;}
 function download(name,data,type){const u=URL.createObjectURL(new Blob([data],{type}));const a=link('',u);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),10000);}
 function policy(parent,config){
  if(config.storage_notice)parent.append(el('p',config.storage_notice,'notice'));
  parent.append(el('p','Model v ChatGPT: GPT-6 Pro. Ak nie je dostupný, ručne vyber GPT-5.6 Sol a Extra High. Web model neprepína a nepoužíva platený API fallback.','muted'));
 }
 function promptFor(id){return `Spracuj CheckniAuto analýzu ${id} cez pripojenie CheckniAuto. Načítaj pôvodný inzerát, všetky prehľadové koláže a potrebné detailné fotografie. Rozlišuj tvrdenia predajcu a overené zistenia. Zaznamenaj skutočne posúdené fotografie. Ulož validný report, ak máš zapisovacie nástroje; inak vráť JSON súbor na import v /beta-admin. Preferencia operátora: GPT-6 Pro; pri nedostupnosti ručne zvolený GPT-5.6 Sol / Extra High. Nemáš oprávnenie spúšťať platené API.`;}
 function promptActions(parent,id){
  const box=el('details');add(box,el('summary','Pokyn pre ChatGPT'),el('pre',promptFor(id)));parent.append(box);
  parent.append(button('Skopírovať pokyn pre ChatGPT',async()=>{await navigator.clipboard.writeText(promptFor(id));}));
 }
 async function landing(){
  reset('Prever auto ešte pred obhliadkou.');
  app.append(el('p','Bezplatná interná beta: web pripraví inzerát, fotografie a dostupné trhové podklady. Analýzu potom hneď spustíš správou v ChatGPT, nie automaticky týmto tlačidlom.','notice'));
  const config=await api('/_beta/config'),ready=config.storage_ready ?? config.cloud_configured;
  policy(app,config);
  if(!ready)app.append(el('p','Úložisko nie je dostupné. Nové analýzy sú pozastavené; platené AI API je vypnuté.','notice'));
  const form=el('form');field(form,'Odkaz na inzerát','url','url',true);
  const submit=el('button','Pripraviť podklady');submit.type='submit';submit.disabled=!ready;
  add(form,submit,el('p','Bazoš.sk/.cz, Autobazar.sk/.eu. Najviac 60 fotografií; galériu neorežeme potichu.','muted'));app.append(form);
  const details=el('details');details.append(el('summary','Alebo vložiť popis a fotografie ručne'));
  const manual=el('form');field(manual,'Názov auta','title','text',true);field(manual,'Celý popis vrátane ceny a údajov','description','textarea',true);field(manual,'Odkaz na zdroj (voliteľný)','source_url','url');
  const files=field(manual,'Fotografie, najviac 60','images','file');files.multiple=true;files.accept='image/jpeg,image/png,image/webp,image/avif';
  const mb=el('button','Pripraviť z vložených údajov');mb.type='submit';mb.disabled=!ready;manual.append(mb);details.append(manual);app.append(details);
  const progress=el('section','','card');progress.hidden=true;const message=el('p'),saved=el('p');add(progress,message,saved);app.append(progress);
  async function run(isManual){
   errorBox.hidden=true;submit.disabled=true;mb.disabled=true;progress.hidden=false;message.textContent='Pripravujeme…';let jobId='';
   try{
    const options=isManual?{method:'POST',body:new FormData(manual)}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:form.elements.url.value,output_language:'sk'})};
    const response=await fetch(isManual?'/api/demo/analyze-manual':'/api/demo/analyze',options);
    if(!response.ok)throw new Error((await response.json()).error || 'Príprava zlyhala.');
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const events=buffer.split('\n\n');buffer=events.pop();
     for(const event of events){const line=event.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;const raw=line.slice(6);
      if(raw==='[DONE]'){if(!jobId)throw new Error('Chýba ID analýzy.');location.assign('/analysis/'+jobId);return;}
      const data=JSON.parse(raw);if(data.slug){jobId=data.slug;remember(jobId);saved.replaceChildren(link('Otvoriť stav analýzy','/analysis/'+jobId));}
      if(data.error)throw new Error(data.error);message.textContent=data.message || labels[data.status] || data.status;
     }
    }throw new Error('Spojenie sa prerušilo. Skontroluj stav analýzy; pri strate údajov vlož inzerát znova.');
   }catch(e){showError(e.message);}finally{submit.disabled=!ready;mb.disabled=!ready;}
  }
  form.addEventListener('submit',e=>{e.preventDefault();run(false);});manual.addEventListener('submit',e=>{e.preventDefault();run(true);});
  try{const jobs=JSON.parse(localStorage.getItem('checkni-beta-jobs')||'[]').filter(id=>/^beta-[a-f0-9]{32}$/.test(id));if(jobs.length){app.append(el('h2','Posledné analýzy (odkazy môžu expirovať)'));const list=el('ul');jobs.slice(0,8).forEach(id=>list.append(add(el('li'),link(id,'/analysis/'+id))));app.append(list);}}catch{}
 }
 function gallery(job){
  app.append(el('h2',`Všetky fotografie (${job.photos.length})`));
  const review=new Map((job.report?.photo_review || []).map(p=>[p.photo_id,p.level]));
  const names={not_inspected:'Neposúdená',overview:'Posúdená v prehľade',detail:'Detailne posúdená'};
  const grid=el('section','','gallery');
  for(const photo of job.photos){const f=el('figure');f.id=photo.id;
   if(photo.status==='available'){const a=link('','/_beta/jobs/'+job.id+'/photos/'+photo.id);a.target='_blank';a.rel='noopener';const img=el('img');img.src=a.href;img.alt='Fotografia '+photo.number;img.loading='lazy';a.append(img);f.append(a);}else f.append(el('p','Fotografia nie je dostupná.'));
   f.append(el('figcaption',`Foto ${photo.number} · ${names[review.get(photo.id) || 'not_inspected']}`));grid.append(f);
  }app.append(grid);
 }
 async function reportPage(id){
  clearTimeout(timer);const job=await api('/_beta/jobs/'+id);reset(job.title || 'Analýza auta');
  app.append(el('p',labels[job.status] || job.status,'pill'));if(job.error)showError(job.error);
  if(job.status==='WAITING_FOR_AI'){
   app.append(el('p','Podklady sú pripravené. Teraz skopíruj pokyn a pošli ho v ChatGPT s pripojenou aplikáciou CheckniAuto. Bez správy v chate sa analýza sama nespustí.','notice'));promptActions(app,id);
  }
  for(const warning of (job.warnings || []).filter(w=>job.status!=='DONE' || !w.startsWith('Fotografie pripravené')))app.append(el('p',warning,'muted'));
  const r=job.report;
  if(r){
   const verdicts={INSPECT:'Má zmysel pokračovať obhliadkou',CAUTION:'Pokračovať s opatrnosťou',AVOID:'Závažné dôvody na opatrnosť',INSUFFICIENT_DATA:'Nedostatok podkladov na záver'};
   add(app,el('h2',verdicts[r.verdict]),el('p',r.summary,'result-text'),el('p','Istota záveru: '+({LOW:'nízka',MEDIUM:'stredná',HIGH:'vysoká'}[r.confidence]),'muted'));
   const cards=el('section','','findings');for(const f of r.findings){const c=el('article','','card');add(c,el('h3',f.title),el('p',f.detail),el('p','Ako overiť: '+f.next_step));for(const pid of f.photo_ids)c.append(link('Foto '+pid.slice(1)+' ','#'+pid));for(const sid of f.source_ids)c.append(link('Zdroj '+sid+' ','#source-'+sid));cards.append(c);}app.append(cards);
   if(r.market_summary)add(app,el('h2','Cena a trh'),el('p',r.market_summary));
   for(const [key,title]of[['questions','Otázky pre predajcu'],['limitations','Čo analýza neoverila']]){const ul=el('ul');r[key].forEach(x=>ul.append(el('li',x)));add(app,el('h2',title),ul);}
   if(r.sources.length){const ul=el('ul');for(const s of r.sources){const li=el('li');li.id='source-'+s.id;const a=link(s.title,s.url);a.target='_blank';a.rel='noopener noreferrer';li.append(a);ul.append(li);}add(app,el('h2','Zdroje uvádzané v reporte'),ul,el('p','Samotný odkaz nie je nezávislé overenie zdroja aplikáciou.','muted'));}
   add(app,button('Tlačiť report',()=>window.print()),button('Uložiť report JSON',()=>download(id+'-report.json',JSON.stringify(r,null,2),'application/json')));
  }
  gallery(job);
  if(['PREPARING','WAITING_FOR_AI','PROCESSING'].includes(job.status))timer=setTimeout(()=>{if(document.visibilityState==='visible')reportPage(id).catch(e=>showError(e.message));},10000);
 }
 async function adminPage(){
  reset('Beta administrácia');const config=await api('/_beta/config');policy(app,config);
  app.append(el('p','Použi CHECKNI_OPERATOR_TOKEN alebo existujúce heslo token dashboardu. Kľúč zostáva len v pamäti tejto karty, nie v localStorage.','muted'));
  if(config.operator_configured===false)app.append(el('p','Na Renderi nastav CHECKNI_OPERATOR_TOKEN. Je to ochrana administrácie, nie kľúč plateného AI modelu.','notice'));
  if(config.mcp_url)add(app,el('h2','Pripojenie do ChatGPT'),el('code',config.mcp_url),el('p','Pridaj túto URL ako vlastnú MCP aplikáciu s OAuth v Developer mode. Prihlás sa administračným kľúčom CheckniAuto. Dostupnosť zápisových nástrojov treba overiť v tvojom účte.'));
  const form=el('form'),input=field(form,'Administračný kľúč','admin_secret','password',true),login=el('button','Načítať frontu');login.type='submit';form.append(login);app.append(form);
  const queue=el('section'),detail=el('section');add(app,queue,detail);let token='',selected='',lease='';
  const auth=()=>({'Authorization':'Bearer '+token});
  const op=(path,method='GET',body)=>api('/_beta/operator/'+path,{method,headers:{...auth(),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  async function refresh(){const data=await op('jobs');queue.replaceChildren(el('h2','Fronta analýz'),el('p',`Limit: ${data.limits.daily}/deň, ${data.limits.retained} uložených analýz.`));for(const job of data.jobs)queue.append(button(`${job.title || job.id} · ${labels[job.status] || job.status}`,()=>openJob(job.id)));}
  async function openJob(id){
   const data=await op('jobs/'+id);selected=id;lease='';detail.replaceChildren(el('h2',data.title),link('Otvoriť report','/analysis/'+id));promptActions(detail,id);
   const status=el('p','','notice'),actions=el('div','','actions');
   actions.append(button('Prevziať analýzu na 90 minút',async()=>{const x=await op('jobs/'+id+'/claim','POST',{});lease=x.lease_token;status.textContent='Analýza je rezervovaná. Môžeš uložiť report.';}));
   actions.append(button('Stiahnuť podklady pre ChatGPT (.zip)',async()=>{const res=await fetch('/_beta/operator/jobs/'+id+'/bundle',{headers:auth()});if(!res.ok)throw new Error((await res.json()).error || 'Export zlyhal.');download(id+'.zip',await res.blob(),'application/zip');}));
   const area=el('textarea');area.rows=22;area.value=JSON.stringify(data.report || data.report_template,null,2);area.setAttribute('aria-label','JSON report na uloženie');
   add(detail,actions,status,el('p','MCP môže report uložiť so zápisovými oprávneniami. Bez nich nahraj ZIP do ChatGPT a výsledný JSON vlož sem.'),area);
   detail.append(button('Validovať a uložiť report',async()=>{if(!lease)throw new Error('Najprv prevezmi analýzu.');await op('jobs/'+selected+'/complete','POST',{lease_token:lease,report:JSON.parse(area.value)});status.textContent='Report je uložený. Nezabudni: úložisko je dočasné.';await refresh();}));
   const raw=el('details');add(raw,el('summary','Podklady a manifest'),el('pre',JSON.stringify(data.manifest,null,2)));detail.append(raw);
  }
  form.addEventListener('submit',async e=>{e.preventDefault();token=input.value.trim();try{await refresh();input.value='';form.hidden=true;app.insertBefore(button('Obnoviť frontu',refresh),queue);}catch(err){showError(err.message);}});
 }
 const path=location.pathname,id=path.split('/')[2];
 (path==='/beta-admin'?adminPage():path.startsWith('/analysis/beta-')?reportPage(id):landing()).catch(e=>{if(!errorBox)reset('CheckniAuto');showError(e.message);});
 document.addEventListener('visibilitychange',()=>{if(path.startsWith('/analysis/beta-') && document.visibilityState==='visible')reportPage(id).catch(e=>showError(e.message));});
})();
