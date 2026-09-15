/* No model-generated HTML is rendered. Report strings are textContent only. */
(() => {
 'use strict';
 const app=document.getElementById('app');
 const el=(tag,value,cls)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;if(cls)n.className=cls;return n;};
 const add=(parent,...nodes)=>{parent.append(...nodes);return parent;};
 const link=(label,href)=>{const a=el('a',label);a.href=href;return a;};
 const button=(label,fn)=>{const b=el('button',label);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){showError(e.message);}finally{b.disabled=false;}});return b;};
 const labels={PREPARING:'Pripravujeme podklady',WAITING_FOR_AI:'Čaká na spracovanie v ChatGPT',PROCESSING:'Operátor spracováva analýzu',DONE:'Report je pripravený',FAILED:'Príprava alebo analýza zlyhala',PREPARATION_INTERRUPTED:'Príprava bola prerušená'};
 let errorBox, timer;
 function reset(title){app.replaceChildren(el('h1',title));errorBox=el('p','','error');errorBox.hidden=true;errorBox.setAttribute('role','alert');app.append(errorBox);}
 function showError(s){errorBox.textContent=s;errorBox.hidden=false;}
 async function api(path,options={}){const r=await fetch(path,{cache:'no-store',...options});const d=await r.json().catch(()=>({error:'Server nevrátil platnú odpoveď.'}));if(!r.ok)throw new Error(d.error || `Chyba ${r.status}`);return d;}
 const remember=id=>{try{const all=JSON.parse(localStorage.getItem('checkni-beta-jobs') || '[]');localStorage.setItem('checkni-beta-jobs',JSON.stringify([id,...all.filter(x=>x!==id)].slice(0,50)));}catch{}};
 function field(form,label,name,type='text',required=false){const l=el('label',label),input=el(type==='textarea'?'textarea':'input');input.id=name;input.name=name;if(type!=='textarea')input.type=type;input.required=required;l.htmlFor=name;add(form,l,input);return input;}
 async function landing(){
  reset('Prever auto ešte pred obhliadkou.');
  app.append(el('p','Bezplatná beta: pripravíme inzerát, fotografie a dostupné trhové podklady. Report následne manuálne spracuje operátor v ChatGPT.','notice'));
  const config=await api('/_beta/config');
  if(!config.cloud_configured)app.append(el('p','Čakáme na pripojenie bezplatného úložiska. Nové analýzy sú zatiaľ pozastavené; platené AI API je vypnuté.','notice'));
  const form=el('form');field(form,'Odkaz na inzerát','url','url',true);
  const submit=el('button','Pripraviť analýzu');submit.type='submit';submit.disabled=!config.cloud_configured;
  add(form,submit,el('p','Bazoš.sk, Bazoš.cz, Autobazar.sk a Autobazar.eu. Najviac 60 fotografií; pri prekročení limitu galériu neorežeme potichu.','muted'));app.append(form);
  const details=el('details');details.append(el('summary','Alebo vložiť popis a fotografie ručne'));
  const manual=el('form');field(manual,'Názov auta','title','text',true);field(manual,'Celý popis inzerátu vrátane ceny a údajov','description','textarea',true);field(manual,'Odkaz na zdroj (voliteľný)','source_url','url');
  const files=field(manual,'Fotografie, najviac 60','images','file');files.multiple=true;files.accept='image/jpeg,image/png,image/webp,image/avif';
  const mb=el('button','Pripraviť z vložených údajov');mb.type='submit';mb.disabled=!config.cloud_configured;manual.append(mb);details.append(manual);app.append(details);
  const progress=el('section','','card');progress.hidden=true;const message=el('p'),saved=el('p');add(progress,message,saved);app.append(progress);
  async function run(isManual){
   errorBox.hidden=true;submit.disabled=true;mb.disabled=true;progress.hidden=false;message.textContent='Pripravujeme…';
   let jobId='';
   try {
    const options=isManual?{method:'POST',body:new FormData(manual)}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:form.elements.url.value,output_language:'sk'})};
    const response=await fetch(isManual?'/api/demo/analyze-manual':'/api/demo/analyze',options);
    if(!response.ok){const data=await response.json();throw new Error(data.error || 'Príprava zlyhala.');}
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const events=buffer.split('\n\n');buffer=events.pop();
     for(const event of events){const line=event.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;const raw=line.slice(6);
      if(raw==='[DONE]'){if(!jobId)throw new Error('Chýba odkaz na uloženú analýzu.');location.assign('/analysis/'+jobId);return;}
      const data=JSON.parse(raw);if(data.slug){jobId=data.slug;remember(jobId);saved.replaceChildren(link('Odkaz na uložený stav analýzy','/analysis/'+jobId));}
      if(data.error)throw new Error(data.error);message.textContent=data.message || labels[data.status] || data.status;
     }
    }
    throw new Error('Spojenie sa prerušilo. Skontroluj uložený stav analýzy.');
   }catch(e){showError(e.message);}finally{submit.disabled=!config.cloud_configured;mb.disabled=!config.cloud_configured;}
  }
  form.addEventListener('submit',e=>{e.preventDefault();run(false);});manual.addEventListener('submit',e=>{e.preventDefault();run(true);});
  try{const jobs=JSON.parse(localStorage.getItem('checkni-beta-jobs')||'[]').filter(id=>/^beta-[a-f0-9]{32}$/.test(id));if(jobs.length){app.append(el('h2','Tvoje posledné analýzy'));const list=el('ul');jobs.slice(0,8).forEach(id=>list.append(add(el('li'),link(id,'/analysis/'+id))));app.append(list);}}catch{}
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
  app.append(el('p',labels[job.status] || job.status,'pill'));
  if(job.error)showError(job.error);
  if(job.status!=='DONE')app.append(el('p','Podklady spracujeme po manuálnom pokyne operátora v ChatGPT. Táto beta nemá garantovaný čas dokončenia. Túto stránku môžeš zavrieť a vrátiť sa cez uložený odkaz.','notice'));
  for(const warning of (job.warnings || []).filter(w=>job.status!=='DONE' || !w.startsWith('Fotografie pripravené')))app.append(el('p',warning,'muted'));
  const r=job.report;
  if(r){
   const verdicts={INSPECT:'Má zmysel pokračovať obhliadkou',CAUTION:'Pokračovať s opatrnosťou',AVOID:'Závažné dôvody na opatrnosť',INSUFFICIENT_DATA:'Nedostatok podkladov na záver'};
   add(app,el('h2',verdicts[r.verdict]),el('p',r.summary,'result-text'),el('p','Istota záveru: '+({LOW:'nízka',MEDIUM:'stredná',HIGH:'vysoká'}[r.confidence]),'muted'));
   const cards=el('section','','findings');
   for(const f of r.findings){const c=el('article','','card');add(c,el('h3',f.title),el('p',f.detail),el('p','Ako overiť: '+f.next_step),el('p','Typ dôkazu: '+({listing:'tvrdenie inzerátu',photo:'fotografia',web:'webový zdroj',unknown:'neoverené'}[f.evidence_type]),'muted'));
    for(const pid of f.photo_ids)c.append(link('Foto '+pid.slice(1)+' ', '#'+pid));
    for(const sid of f.source_ids)c.append(link('Zdroj '+sid+' ', '#source-'+sid));cards.append(c);
   }app.append(cards);
   if(r.market_summary)add(app,el('h2','Cena a trh'),el('p',r.market_summary));
   for(const [key,title]of[['questions','Otázky pre predajcu'],['limitations','Čo analýza neoverila']]){const ul=el('ul');r[key].forEach(x=>ul.append(el('li',x)));add(app,el('h2',title),ul);}
   if(r.sources.length){const ul=el('ul');for(const s of r.sources){const li=el('li');li.id='source-'+s.id;const a=link(s.title,s.url);a.target='_blank';a.rel='noopener noreferrer';li.append(a);ul.append(li);}add(app,el('h2','Zdroje uvádzané v reporte'),ul,el('p','Samotné uvedenie odkazu nie je nezávislé overenie zdroja aplikáciou.','muted'));}
   app.append(button('Tlačiť report',()=>window.print()));
  }
  gallery(job);
  if(['PREPARING','WAITING_FOR_AI','PROCESSING'].includes(job.status))timer=setTimeout(()=>{if(document.visibilityState==='visible')reportPage(id).catch(e=>showError(e.message));},60000);
 }
 async function adminPage(){
  reset('Beta administrácia');app.append(el('p','Kľúč sa drží len v pamäti tejto karty. Nie je to heslo do ChatGPT ani AI API kľúč.','muted'));
  const form=el('form'),input=field(form,'Beta administračný kľúč','admin_secret','password',true),login=el('button','Načítať frontu');login.type='submit';form.append(login);app.append(form);
  const queue=el('section'),detail=el('section');add(app,queue,detail);let token='',selected='',lease='';
  const auth=()=>({'Authorization':'Bearer '+token});
  const op=(path,method='GET',body)=>api('/_beta/operator/'+path,{method,headers:{...auth(),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  async function refresh(){const data=await op('jobs');queue.replaceChildren(el('h2','Fronta analýz'),el('p',`Limit: ${data.limits.daily}/deň, ${data.limits.retained} uložených analýz.`));for(const job of data.jobs)queue.append(button(`${job.title || job.id} · ${labels[job.status] || job.status}`,()=>openJob(job.id)));}
  async function openJob(id){
   const data=await op('jobs/'+id);selected=id;lease='';detail.replaceChildren(el('h2',data.title),link('Otvoriť verejný report','/analysis/'+id));
   const actions=el('div','','actions');actions.append(button('Prevziať analýzu na 90 minút',async()=>{const x=await op('jobs/'+id+'/claim','POST',{});lease=x.lease_token;status.textContent='Analýza je rezervovaná. Teraz môžeš uložiť report.';}));
   actions.append(button('Stiahnuť podklady pre ChatGPT (.zip)',async()=>{const res=await fetch('/_beta/operator/jobs/'+id+'/bundle',{headers:auth()});if(!res.ok)throw new Error((await res.json()).error || 'Export zlyhal.');const url=URL.createObjectURL(await res.blob());const a=link('',url);a.download=id+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}));
   const status=el('p','','notice'),area=el('textarea');area.rows=22;area.value=JSON.stringify(data.report || data.report_template,null,2);area.setAttribute('aria-label','JSON report na uloženie');
   add(detail,actions,status,el('p','MCP môže report uložiť priamo, keď má zápisové oprávnenia. Alternatíva: nahraj ZIP do ChatGPT a výsledný JSON vlož sem.'),area);
   detail.append(button('Validovať a uložiť report',async()=>{if(!lease)throw new Error('Najprv prevezmi analýzu.');const r=JSON.parse(area.value);await op('jobs/'+selected+'/complete','POST',{lease_token:lease,report:r});status.textContent='Report je uložený. Verejná stránka ho už môže načítať.';await refresh();}));
   const raw=el('details');add(raw,el('summary','Podklady a manifest'),el('pre',JSON.stringify(data.manifest,null,2)));detail.append(raw);
  }
  form.addEventListener('submit',async e=>{e.preventDefault();token=input.value.trim();try{await refresh();input.value='';form.hidden=true;app.insertBefore(button('Obnoviť frontu',refresh),queue);}catch(err){showError(err.message);}});
 }
 const path=location.pathname,id=path.split('/')[2];
 (path==='/beta-admin'?adminPage():path.startsWith('/analysis/beta-')?reportPage(id):landing()).catch(e=>{if(!errorBox)reset('CheckniAuto');showError(e.message);});
 document.addEventListener('visibilitychange',()=>{if(path.startsWith('/analysis/beta-') && document.visibilityState==='visible')reportPage(id).catch(e=>showError(e.message));});
})();
