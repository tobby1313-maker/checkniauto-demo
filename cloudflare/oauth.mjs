// Single-operator beta OAuth: PKCE, one-use codes, rotating refresh tokens.
// No ChatGPT password, session cookie or AI API credential is ever collected.
import {HttpError,requireThat,json,readJSON,readBytes,digest,random,sameSecret,now,quota} from './store.mjs';
const enc=new TextEncoder();
const b64=bytes=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
async function signature(env,text) {
 requireThat(env.SIGNING_KEY?.length>=32,503,'OAuth signing key is not configured.');
 const key=await crypto.subtle.importKey('raw',enc.encode(env.SIGNING_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 return b64(new Uint8Array(await crypto.subtle.sign('HMAC',key,enc.encode(text))));
}
export const resource=env=>`${env.PUBLIC_ORIGIN}/mcp`;
export const scopes=env=>env.MCP_READ_ONLY==='true'?['analysis:read','offline_access']:['analysis:read','analysis:write','offline_access'];
export function challenge(env,scope='analysis:read') {return `Bearer resource_metadata="${env.PUBLIC_ORIGIN}/.well-known/oauth-protected-resource", scope="${scope}", error="insufficient_scope", error_description="Connect your CheckniAuto operator account"`;}
export async function authenticate(request,env,required='analysis:read') {
 const token=(request.headers.get('Authorization') || '').replace(/^Bearer /i,'');
 if(!token || token.length>300) return null;
 const row=await env.DB.prepare("SELECT * FROM oauth_tokens WHERE hash=? AND kind='access' AND expires_at>?").bind(await digest(token),now()).first();
 return row?.resource===resource(env) && row.scope.split(' ').includes(required)?row:null;
}
function allowedRedirect(uri) {
 try {const u=new URL(uri);return u.origin==='https://chatgpt.com' && !u.search && !u.hash &&
  (u.pathname==='/connector_platform_oauth_redirect' || /^\/connector\/oauth\/[a-zA-Z0-9_-]+$/.test(u.pathname));} catch{return false;}
}
async function issueTokens(env,row) {
 const access=random(),refresh=random();
 await env.DB.batch([
  env.DB.prepare('INSERT INTO oauth_tokens(hash,client_id,kind,resource,scope,expires_at) VALUES(?,?,?,?,?,?)').bind(await digest(access),row.client_id,'access',row.resource,row.scope,now()+3600),
  env.DB.prepare('INSERT INTO oauth_tokens(hash,client_id,kind,resource,scope,expires_at) VALUES(?,?,?,?,?,?)').bind(await digest(refresh),row.client_id,'refresh',row.resource,row.scope,now()+604800),
 ]);
 return json({access_token:access,token_type:'Bearer',expires_in:3600,refresh_token:refresh,scope:row.scope});
}
export async function oauth(request,env,path) {
 const origin=env.PUBLIC_ORIGIN;
 if(path==='/.well-known/oauth-protected-resource' || path==='/.well-known/oauth-protected-resource/mcp')
  return json({resource:resource(env),authorization_servers:[origin],scopes_supported:scopes(env)});
 if(path==='/.well-known/oauth-authorization-server')
  return json({issuer:origin,authorization_endpoint:`${origin}/oauth/authorize`,token_endpoint:`${origin}/oauth/token`,
   registration_endpoint:`${origin}/oauth/register`,revocation_endpoint:`${origin}/oauth/revoke`,
   response_types_supported:['code'],grant_types_supported:['authorization_code','refresh_token'],
   token_endpoint_auth_methods_supported:['none'],code_challenge_methods_supported:['S256'],
   authorization_response_iss_parameter_supported:true,scopes_supported:scopes(env)});
 if(path==='/oauth/register' && request.method==='POST') {
  await quota(env,`register:${new Date().toISOString().slice(0,10)}`,20);
  const data=await readJSON(request,5000);
  requireThat(Array.isArray(data.redirect_uris) && data.redirect_uris.length>0 && data.redirect_uris.length<=3 && data.redirect_uris.every(allowedRedirect),400,'Only ChatGPT OAuth redirect URIs can register.');
  const id=random();
  await env.DB.prepare('INSERT INTO oauth_clients(id,redirect_uris,created_at) VALUES(?,?,?)').bind(id,JSON.stringify(data.redirect_uris),now()).run();
  return json({client_id:id,redirect_uris:data.redirect_uris,token_endpoint_auth_method:'none',grant_types:['authorization_code','refresh_token'],response_types:['code']},201);
 }
 if(path==='/oauth/authorize' && request.method==='GET') {
  const p=new URL(request.url).searchParams;
  const client=await env.DB.prepare('SELECT * FROM oauth_clients WHERE id=?').bind(p.get('client_id') || '').first();
  requireThat(client && JSON.parse(client.redirect_uris).includes(p.get('redirect_uri')),400,'Unknown OAuth client or callback.');
  requireThat(p.get('response_type')==='code' && p.get('code_challenge_method')==='S256' && /^[a-zA-Z0-9_-]{43}$/.test(p.get('code_challenge') || ''),400,'PKCE S256 is required.');
  requireThat(p.get('resource')===resource(env),400,'Invalid OAuth resource.');
  const scope=(p.get('scope') || 'analysis:read offline_access').split(' ').filter(Boolean);
  requireThat(scope.length>0 && scope.every(s=>scopes(env).includes(s)),400,'Unsupported scope.');
  const nonce=random();
  const state={client_id:client.id,redirect_uri:p.get('redirect_uri'),challenge:p.get('code_challenge'),
   resource:resource(env),scope:scope.join(' '),state:p.get('state') || '',nonce,expires:now()+300};
  const payload=b64(enc.encode(JSON.stringify(state))), signed=`${payload}.${await signature(env,payload)}`;
  const html=`<!doctype html><html lang="sk"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>CheckniAuto: pripojenie</title><body><h1>Pripojiť CheckniAuto k ChatGPT</h1><p>Prístup: ${escape(scope.join(', '))}. Pokračuj iba vtedy, ak si pripojenie spustil ty.</p><form method="post" action="/oauth/authorize"><input type="hidden" name="signed" value="${escape(signed)}"><label>Beta administračný kľúč <input type="password" name="secret" required autocomplete="off"></label><button>Schváliť pripojenie</button></form><p>Nie heslo do ChatGPT ani kľúč plateného AI API.</p></body></html>`;
  return new Response(html,{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",'Referrer-Policy':'no-referrer',
   'Set-Cookie':`__Host-checkni-oauth=${nonce}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=300`}});
 }
 if(path==='/oauth/authorize' && request.method==='POST') {
  requireThat(request.headers.get('Origin')===origin,403,'Invalid authorization origin.');
  await quota(env,`login:${new Date().toISOString().slice(0,10)}`,60);
  const form=new URLSearchParams(new TextDecoder().decode(await readBytes(request,10000)));
  const [payload,sig,...rest]=(form.get('signed') || '').split('.');
  requireThat(payload && sig && rest.length===0 && await sameSecret(sig,await signature(env,payload)),400,'Authorization form expired or invalid.');
  let row;try {row=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(payload.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0))));}catch{throw new HttpError(400,'Invalid authorization form.');}
  const cookie=(request.headers.get('Cookie') || '').split(';').map(x=>x.trim()).find(x=>x.startsWith('__Host-checkni-oauth='))?.split('=')[1];
  requireThat(row.expires>=now() && await sameSecret(cookie,row.nonce),403,'Authorization browser session expired.');
  requireThat(await sameSecret(form.get('secret'),env.ADMIN_TOKEN),401,'Invalid beta administrator key.');
  const code=random();
  await env.DB.prepare('INSERT INTO oauth_codes(hash,client_id,redirect_uri,challenge,resource,scope,expires_at) VALUES(?,?,?,?,?,?,?)')
   .bind(await digest(code),row.client_id,row.redirect_uri,row.challenge,row.resource,row.scope,now()+300).run();
  const target=new URL(row.redirect_uri);target.searchParams.set('code',code);target.searchParams.set('state',row.state);target.searchParams.set('iss',origin);
  return new Response(null,{status:303,headers:{Location:target.href,'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
   'Set-Cookie':'__Host-checkni-oauth=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0'}});
 }
 if(path==='/oauth/token' && request.method==='POST') {
  const p=new URLSearchParams(new TextDecoder().decode(await readBytes(request,10000)));
  if(p.get('grant_type')==='authorization_code') {
   const hash=await digest(p.get('code') || '');
   const row=await env.DB.prepare('SELECT * FROM oauth_codes WHERE hash=? AND expires_at>?').bind(hash,now()).first();
   requireThat(row && row.client_id===p.get('client_id') && row.redirect_uri===p.get('redirect_uri') && row.resource===p.get('resource'),400,'invalid_grant');
   const verifier=p.get('code_verifier') || '';
   requireThat(/^[a-zA-Z0-9._~-]{43,128}$/.test(verifier),400,'invalid_grant');
   const actual=b64(new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(verifier))));
   requireThat(actual===row.challenge,400,'invalid_grant');
   const consumed=await env.DB.prepare('DELETE FROM oauth_codes WHERE hash=? RETURNING hash').bind(hash).first();
   requireThat(consumed,400,'invalid_grant');return issueTokens(env,row);
  }
  if(p.get('grant_type')==='refresh_token') {
   const hash=await digest(p.get('refresh_token') || '');
   const row=await env.DB.prepare("SELECT * FROM oauth_tokens WHERE hash=? AND kind='refresh' AND expires_at>?").bind(hash,now()).first();
   requireThat(row && row.client_id===p.get('client_id') && row.resource===p.get('resource'),400,'invalid_grant');
   const consumed=await env.DB.prepare("DELETE FROM oauth_tokens WHERE hash=? AND kind='refresh' RETURNING hash").bind(hash).first();
   requireThat(consumed,400,'invalid_grant'); return issueTokens(env,row);
  }
  throw new HttpError(400,'unsupported_grant_type');
 }
 if(path==='/oauth/revoke' && request.method==='POST') {
  const p=new URLSearchParams(new TextDecoder().decode(await readBytes(request,10000)));
  await env.DB.prepare('DELETE FROM oauth_tokens WHERE hash=? AND client_id=?').bind(await digest(p.get('token') || ''),p.get('client_id') || '').run();return json({});
 }
 return null;
}
