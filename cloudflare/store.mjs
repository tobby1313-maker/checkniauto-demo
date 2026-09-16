// Cloudflare D1 + private R2, deliberately bounded for a small free beta.
export const MAX_JOB_BYTES = 40_000_000;
export const MAX_JOBS = 50;
export const MAX_DAILY_JOBS = 10;
export const MAX_DAILY_REQUESTS = 5000; // Below R2 operation allowances for this service.
export const JOB_RE = /^beta-[a-f0-9]{32}$/;
const ASSET_RE = /^(?:images\/p[0-9]{3}\.jpg|sheets\/overview_[0-9]{3}\.jpg|(?:raw_data|listing_facts|market_data|photo_selection)\.json)$/;
export class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
export const requireThat = (ok, status, message) => { if (!ok) throw new HttpError(status, message); };
export const now = () => Math.floor(Date.now()/1000);
export const random = () => crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
export const digest = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', typeof value === 'string' ? new TextEncoder().encode(value) : value))].map(x=>x.toString(16).padStart(2,'0')).join('');
export const sameSecret = async (a,b) => typeof a === 'string' && typeof b === 'string' && b.length >= 32 && await digest(a) === await digest(b);
export const json = (value, status=200, headers={}) => new Response(JSON.stringify(value), {status, headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Robots-Tag':'noindex, nofollow', ...headers}});
export async function readBytes(request, limit=200_000) {
 const reader=request.body?.getReader(); if(!reader) return new Uint8Array();
 const chunks=[]; let total=0;
 while(true) { const {done,value}=await reader.read(); if(done) break; total+=value.byteLength;
  if(total>limit) {await reader.cancel(); throw new HttpError(413,'Request exceeds the beta size limit.');} chunks.push(value); }
 const out=new Uint8Array(total); let pos=0; for(const c of chunks){out.set(c,pos);pos+=c.length;} return out;
}
export async function readJSON(request, limit=200_000) {
 try { return JSON.parse(new TextDecoder().decode(await readBytes(request,limit))); }
 catch(e) {if(e instanceof HttpError) throw e; throw new HttpError(400,'Invalid JSON.');}
}
export async function quota(env, bucket, limit) {
 const row=await env.DB.prepare('INSERT INTO counters(bucket,used) VALUES(?,1) ON CONFLICT(bucket) DO UPDATE SET used=used+1 WHERE used < ? RETURNING used').bind(bucket,limit).first();
 requireThat(row,429,'Free beta request limit reached. Please try again tomorrow.');
}
export async function getJob(env,id) {
 requireThat(JOB_RE.test(id),404,'Analysis not found.');
 const job=await env.DB.prepare('SELECT * FROM jobs WHERE id=?').bind(id).first();
 requireThat(job,404,'Analysis not found.'); return job;
}
export function publicJob(job) {
 const manifest=job.manifest ? JSON.parse(job.manifest) : null;
 const report=job.report ? JSON.parse(job.report) : null;
 const stale=job.status==='PREPARING' && job.updated_at < now()-900;
 return {id:job.id,status:stale?'PREPARATION_INTERRUPTED':job.status,title:job.title,
  created_at:job.created_at,updated_at:job.updated_at,language:job.language,
  error:stale?'Príprava bola prerušená. Vlož inzerát znova.':job.error,
  photos:manifest?.photos || [],report,chargeable:false,ai_api_calls:0,
  warnings:manifest?.warnings || [], source_url:job.source_url};
}
export async function createJob(env,payload) {
 requireThat(payload && JOB_RE.test(payload.id),400,'Invalid analysis ID.');
 requireThat(['sk','cs','en'].includes(payload.language),400,'Unsupported language.');
 requireThat(typeof payload.source_url==='string' && payload.source_url.length<=2000,400,'Invalid source URL.');
 const existing=await env.DB.prepare('SELECT * FROM jobs WHERE id=?').bind(payload.id).first();
 if(existing) {
  requireThat(existing.source_url===payload.source_url && existing.language===payload.language,409,'Idempotency conflict.');
  return publicJob(existing);
 }
 const row=await env.DB.prepare(`INSERT INTO jobs(id,status,source_url,language,created_at,updated_at)
 SELECT ?,'PREPARING',?,?,?,? WHERE (SELECT count(*) FROM jobs) < ?
 AND (SELECT count(*) FROM jobs WHERE created_at>=?) < ? RETURNING *`)
 .bind(payload.id,payload.source_url,payload.language,now(),now(),MAX_JOBS,now()-86400,MAX_DAILY_JOBS).first();
 requireThat(row,429,'Beta capacity reached (10 analyses/day or 50 retained analyses). No AI API was called.');
 return publicJob(row);
}
export async function uploadAsset(env,id,name,bytes) {
 const job=await getJob(env,id);
 requireThat(job.status==='PREPARING',409,'Analysis no longer accepts source files.');
 requireThat(ASSET_RE.test(name),400,'Invalid asset name.');
 requireThat(bytes.byteLength>0 && bytes.byteLength<=4_000_000,413,'Asset exceeds the beta size limit.');
 const hash=await digest(bytes);
 const existing=await env.DB.prepare('SELECT * FROM assets WHERE job_id=? AND name=?').bind(id,name).first();
 if(existing) {
  requireThat(existing.sha256===hash && existing.bytes===bytes.byteLength,409,'Asset content conflict.');
  if(existing.ready) return {name,sha256:hash,bytes:bytes.byteLength};
 } else {
  const row=await env.DB.prepare(`INSERT INTO assets(job_id,name,bytes,sha256)
   SELECT ?,?,?,? WHERE (SELECT coalesce(sum(bytes),0) FROM assets WHERE job_id=?) + ? <= ?
   AND (SELECT count(*) FROM assets WHERE job_id=?) < 100 RETURNING name`)
   .bind(id,name,bytes.byteLength,hash,id,bytes.byteLength,MAX_JOB_BYTES,id).first();
  requireThat(row,413,'Analysis exceeds the 40 MB / 100 file beta limit.');
 }
 // Budget/metadata are reserved before touching R2. Failed writes remain reserved.
 await env.BUCKET.put(`${id}/${name}`,bytes,{httpMetadata:{contentType:name.endsWith('.jpg')?'image/jpeg':'application/json'}});
 await env.DB.prepare('UPDATE assets SET ready=1 WHERE job_id=? AND name=? AND sha256=?').bind(id,name,hash).run();
 return {name,sha256:hash,bytes:bytes.byteLength};
}
export function validateManifest(m) {
 requireThat(m && m.schema_version===1 && typeof m.title==='string' && m.title.length<=500,400,'Invalid manifest.');
 requireThat(Array.isArray(m.photos) && m.photos.length<=60 && Array.isArray(m.sheets) && m.sheets.length<=15,400,'Invalid gallery.');
 const ids=new Set();
 for(const p of m.photos) {
  requireThat(p && Number.isInteger(p.number) && p.number>=1 && p.number<=60 && p.id===`p${String(p.number).padStart(3,'0')}` && /^p[0-9]{3}$/.test(p.id) && !ids.has(p.id) && p.inspection==='not_inspected',400,'Invalid or pre-inspected photo.');
  requireThat(['available','unreadable','download_failed'].includes(p.status),400,'Invalid photo status.');
  requireThat(p.status!=='available' ? p.filename===null : p.filename===`images/${p.id}.jpg`,400,'Photo filename does not match ID.');
  ids.add(p.id);
 }
 const sids=new Set(),covered=new Set();
 for(const s of m.sheets) {
  requireThat(/^s[0-9]{3}$/.test(s.id) && !sids.has(s.id) && ASSET_RE.test(s.filename) && s.filename.startsWith('sheets/'),400,'Invalid sheet.');
  requireThat(Array.isArray(s.photo_ids) && s.photo_ids.length>0 && s.photo_ids.length<=4 && s.photo_ids.every(id=>ids.has(id)),400,'Invalid sheet mapping.');
  sids.add(s.id); s.photo_ids.forEach(id=>covered.add(id));
 }
 requireThat(m.photos.filter(p=>p.status==='available').every(p=>covered.has(p.id)),400,'Overview must cover every available photo.');
 requireThat(Array.isArray(m.files) && m.files.length<=100,400,'Invalid file manifest.');
 const names=new Set();
 for(const f of m.files) {
  requireThat(ASSET_RE.test(f.name) && !names.has(f.name) && Number.isSafeInteger(f.bytes) && f.bytes>0 && /^[a-f0-9]{64}$/.test(f.sha256),400,'Invalid file metadata.'); names.add(f.name);
 }
 requireThat(names.has('raw_data.json') && names.has('listing_facts.json'),400,'Listing snapshot is required.');
 requireThat([...m.photos.filter(p=>p.filename),...m.sheets].every(p=>names.has(p.filename)),400,'Gallery file missing.');
 requireThat(m.ai_calls===0 && m.chargeable===false,400,'This is an API-free beta.');
}
export async function markReady(env,id,m) {
 validateManifest(m);
 const job=await getJob(env,id), encoded=JSON.stringify(m), hash=await digest(encoded);
 requireThat(encoded.length<=180_000,413,'Manifest too large.');
 if(job.manifest_hash===hash && job.status!=='PREPARING') return publicJob(job);
 requireThat(job.status==='PREPARING',409,'Analysis cannot be published twice.');
 const {results}=await env.DB.prepare('SELECT * FROM assets WHERE job_id=?').bind(id).all();
 const files=new Map(results.map(f=>[f.name,f]));
 requireThat(m.files.every(f=>{const a=files.get(f.name);return a?.ready===1 && a.bytes===f.bytes && a.sha256===f.sha256;}),409,'Upload incomplete. Analysis was NOT queued.');
 requireThat(results.length===m.files.length,409,'Unlisted upload present.');
 await env.DB.prepare("UPDATE jobs SET status='WAITING_FOR_AI',manifest=?,manifest_hash=?,title=?,updated_at=? WHERE id=? AND status='PREPARING'")
  .bind(encoded,hash,m.title,now(),id).run();
 return publicJob(await getJob(env,id));
}
export async function listJobs(env) {
 const {results}=await env.DB.prepare('SELECT id,status,title,created_at,updated_at,lease_until FROM jobs ORDER BY created_at ASC LIMIT 50').all();
 return {jobs:results,limits:{daily:MAX_DAILY_JOBS,retained:MAX_JOBS,bytes_per_job:MAX_JOB_BYTES}};
}
export async function claimJob(env,id) {
 const token=random();
 const row=await env.DB.prepare(`UPDATE jobs SET status='PROCESSING',lease_token=?,lease_until=?,updated_at=? WHERE id=?
 AND (status='WAITING_FOR_AI' OR (status='PROCESSING' AND lease_until<?)) RETURNING id,lease_token,lease_until`)
 .bind(token,now()+5400,now(),id,now()).first();
 requireThat(row,409,'Analysis is not waiting, or another review has a valid lease.'); return row;
}
const exactKeys=(o,allowed)=>o && typeof o==='object' && !Array.isArray(o) && Object.keys(o).every(k=>allowed.includes(k));
const string=(s,max=4000)=>typeof s==='string' && s.trim().length>0 && s.length<=max;
export function validateReport(report,id,manifest) {
 requireThat(exactKeys(report,['schema_version','job_id','summary','verdict','confidence','findings','sources','questions','limitations','photo_review','market_summary']),400,'Unexpected report field.');
 requireThat(report && report.schema_version===1 && report.job_id===id,400,'Report belongs to a different analysis or schema.');
 requireThat(string(report.summary) && ['INSPECT','CAUTION','AVOID','INSUFFICIENT_DATA'].includes(report.verdict),400,'Summary and verdict are required.');
 requireThat(report.summary!=='Replace with an evidence-based summary.',400,'Replace the report template before publication.');
 requireThat(['LOW','MEDIUM','HIGH'].includes(report.confidence),400,'Invalid confidence.');
 for(const name of ['questions','limitations']) requireThat(Array.isArray(report[name]) && report[name].length>0 && report[name].length<=20 && report[name].every(x=>string(x,1500)),400,`${name} is required.`);
 requireThat(Array.isArray(report.sources) && report.sources.length<=30,400,'Invalid sources.');
 const sources=new Set();
 for(const s of report.sources) {
  requireThat(exactKeys(s,['id','title','url']),400,'Invalid source fields.');
  let url; try{url=new URL(s.url);}catch{throw new HttpError(400,'Invalid source URL.');}
  requireThat(string(s.id,40) && !sources.has(s.id) && string(s.title,400) && ['http:','https:'].includes(url.protocol) && !url.username && !url.password,400,'Invalid source.');sources.add(s.id);
 }
 const photos=new Set(manifest.photos.map(p=>p.id)), unavailable=new Set(manifest.photos.filter(p=>p.status!=='available').map(p=>p.id));
 requireThat(Array.isArray(report.photo_review) && report.photo_review.length===photos.size,400,'Every gallery photo needs an inspection label, including not_inspected.');
 const reviewed=new Map();
 for(const p of report.photo_review) {
  requireThat(exactKeys(p,['photo_id','level']),400,'Invalid photo-review fields.');
  requireThat(photos.has(p.photo_id) && !reviewed.has(p.photo_id) && ['not_inspected','overview','detail'].includes(p.level),400,'Invalid inspection label.');
  requireThat(!unavailable.has(p.photo_id) || p.level==='not_inspected',400,'An unavailable photo cannot be inspected.');reviewed.set(p.photo_id,p.level);
 }
 requireThat(Array.isArray(report.findings) && report.findings.length<=25,400,'Invalid findings.');
 for(const f of report.findings) {
  requireThat(exactKeys(f,['title','detail','next_step','evidence_type','confidence','photo_ids','source_ids']),400,'Invalid finding fields.');
  requireThat(string(f.title,300) && string(f.detail) && string(f.next_step,1500),400,'Finding needs evidence explanation and a next step.');
  requireThat(['listing','photo','web','unknown'].includes(f.evidence_type) && ['LOW','MEDIUM','HIGH'].includes(f.confidence),400,'Invalid finding classification.');
  requireThat(Array.isArray(f.photo_ids) && f.photo_ids.length<=60 && f.photo_ids.every(p=>photos.has(p) && reviewed.get(p)!=='not_inspected'),400,'Finding references an uninspected/unknown photo.');
  requireThat(Array.isArray(f.source_ids) && f.source_ids.length<=30 && f.source_ids.every(s=>sources.has(s)),400,'Finding references an unknown source.');
  requireThat(f.evidence_type!=='photo' || f.photo_ids.length>0,400,'Photo evidence needs a photo ID.');
  requireThat(f.evidence_type!=='web' || f.source_ids.length>0,400,'Web evidence needs a source.');
 }
 requireThat(report.market_summary===undefined || string(report.market_summary),400,'Invalid market summary.');
 requireThat(JSON.stringify(report).length<=100_000,413,'Report is too large.');
}
export async function completeJob(env,id,token,report) {
 const job=await getJob(env,id);
 requireThat(job.manifest,409,'Source data is not ready.');
 validateReport(report,id,JSON.parse(job.manifest));
 const encoded=JSON.stringify(report), hash=await digest(encoded);
 requireThat(await sameSecret(token,job.lease_token),409,'Review lease is invalid.');
 if(job.status==='DONE' && job.report_hash===hash) return publicJob(job);
 requireThat(job.status==='PROCESSING' && job.lease_until>=now(),409,'Review lease expired or report already completed.');
 const row=await env.DB.prepare("UPDATE jobs SET status='DONE',report=?,report_hash=?,updated_at=? WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_until>=? RETURNING id")
 .bind(encoded,hash,now(),id,token,now()).first();
 requireThat(row,409,'Concurrent report change.'); return publicJob(await getJob(env,id));
}
export async function failJob(env,id,message,lease=null) {
 requireThat(string(message,1000),400,'A short failure reason is required.');
 const row=await env.DB.prepare(`UPDATE jobs SET status='FAILED',error=?,updated_at=? WHERE id=?
 AND (status='PREPARING' OR (status='PROCESSING' AND lease_token=? AND lease_until>=?)) RETURNING id`)
 .bind(message,now(),id,lease,now()).first();requireThat(row,409,'Analysis cannot be marked failed in its current state.');
 return publicJob(await getJob(env,id));
}
export function reportTemplate(job) {
 const m=JSON.parse(job.manifest);
 return {schema_version:1,job_id:job.id,verdict:'INSUFFICIENT_DATA',confidence:'LOW',summary:'Replace with an evidence-based summary.',
  findings:[],sources:[],questions:['Which claims should the seller document?'],limitations:['This is not a physical inspection or a vehicle-history report.'],
  photo_review:m.photos.map(p=>({photo_id:p.id,level:'not_inspected'}))};
}
