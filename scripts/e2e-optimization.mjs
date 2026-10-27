/** Creates fixture identities only in the dedicated isolated candidate realm. */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { randomBytes } from 'node:crypto'
import { spawnSync } from 'node:child_process'

const root=resolve(import.meta.dirname,'..'),directory=resolve(root,'.cache/optimization-20261026')
const project='dr-opt-acceptance',web='http://localhost:28081',identity='http://localhost:18481'
const config=JSON.parse(readFileSync(resolve(directory,'acceptance-config.json'),'utf8'))
if(config.project!==project||config.web_url!==web||config.keycloak_url!==identity)throw new Error('Isolated candidate configuration mismatch')
const environment=Object.fromEntries(readFileSync(config.env_file,'utf8').split(/\r?\n/).filter(line=>line&&!line.startsWith('#')).map(line=>{const index=line.indexOf('=');return [line.slice(0,index),line.slice(index+1)]}))
const compose=['compose','--env-file',config.env_file,...config.compose_files.flatMap(file=>['-f',file]),'-p',project]
for(const [service,port,url] of [['web','80',web],['keycloak','8080',identity]]){
 const check=spawnSync('docker',[...compose,'port',service,port],{cwd:root,encoding:'utf8'})
 if(check.status!==0||check.stdout.trim().split(':').at(-1)!==new URL(url).port)throw new Error('Isolated published port mismatch')
}
const response=await fetch(`${identity}/realms/master/protocol/openid-connect/token`,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:'admin-cli',grant_type:'password',username:'admin',password:environment.KEYCLOAK_ADMIN_PASSWORD})})
if(!response.ok)throw new Error('Isolated identity bootstrap unavailable')
const token=(await response.json()).access_token,accounts={}
for(const role of ['author','reviewer','researcher','viewer','outsider']){
 const username=`opt-${role}-${randomBytes(4).toString('hex')}`,password=randomBytes(24).toString('base64url')
 const created=await fetch(`${identity}/admin/realms/deepresearch/users`,{method:'POST',headers:{authorization:`Bearer ${token}`,'content-type':'application/json'},body:JSON.stringify({username,enabled:true,emailVerified:true,email:`${username}@example.invalid`,firstName:'Isolated',lastName:'Acceptance',credentials:[{type:'password',value:password,temporary:false}]})})
 if(!created.ok)throw new Error(`Isolated ${role} fixture creation failed (${created.status})`)
 accounts[role]={username,password,id:created.headers.get('location').split('/').at(-1)}
}
mkdirSync(directory,{recursive:true});writeFileSync(resolve(directory,'accounts.private.json'),JSON.stringify(accounts,null,2))
const attemptDirectory=resolve(directory,`browser-attempt-${Date.now()}`)
mkdirSync(attemptDirectory,{recursive:true});writeFileSync(resolve(attemptDirectory,'accounts.private.json'),JSON.stringify(accounts,null,2))
const selection=process.env.OPT_E2E_CASE
if(selection&&!['roles','recovery','source','lifecycle','faults','live'].includes(selection))throw new Error('Unknown isolated selection')
const filter=['roles','recovery'].includes(selection)?['--grep',selection==='roles'?'five':'Java']:[]
const testFile=selection==='source'?'e2e/optimization-source.e2e.ts':selection==='faults'?'e2e/optimization-faults.e2e.ts':selection==='live'?'e2e/optimization-live.e2e.ts':selection==='lifecycle'?'e2e/lifecycle-live.e2e.ts':'e2e/optimization.e2e.ts'
const execution=spawnSync(process.execPath,[resolve(root,'frontend/node_modules/@playwright/test/cli.js'),'test',testFile,'--project=chromium','--workers=1','--output',attemptDirectory,...filter],{cwd:resolve(root,'frontend'),stdio:'inherit',windowsHide:true,env:{...process.env,E2E_BASE_URL:web,E2E_OPTIMIZATION:'1',E2E_LIFECYCLE:selection==='lifecycle'?'1':'0',E2E_PRESERVE_LIFECYCLE:'1',E2E_PRESERVE_DIRECTORY:resolve(attemptDirectory,'lifecycle'),E2E_COMPOSE_PROJECT:project,E2E_USERNAME:accounts.author.username,E2E_PASSWORD:accounts.author.password,E2E_AUTHOR_SUBJECT:accounts.author.id,E2E_REVIEWER_USERNAME:accounts.reviewer.username,E2E_REVIEWER_PASSWORD:accounts.reviewer.password,E2E_REVIEWER_SUBJECT:accounts.reviewer.id,E2E_VIEWER_USERNAME:accounts.viewer.username,E2E_VIEWER_PASSWORD:accounts.viewer.password,E2E_VIEWER_SUBJECT:accounts.viewer.id,OPT_ACCEPTANCE_CONFIG:resolve(directory,'acceptance-config.json')}})
process.exitCode=execution.status??1
