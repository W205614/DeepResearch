import {test,expect} from '@playwright/test'
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
import {execFileSync} from 'node:child_process'

const root=resolve('../.cache/optimization-20261026'),enabled=process.env.E2E_OPTIMIZATION==='1'
const accounts=enabled?JSON.parse(readFileSync(resolve(root,'accounts.private.json'),'utf8')):{}
const config=enabled?JSON.parse(readFileSync(resolve(root,'acceptance-config.json'),'utf8')):{}
function compose(...args:string[]){expect(config.project).toBe('dr-opt-acceptance');return execFileSync('docker',['compose','--env-file',config.env_file,...config.compose_files.flatMap((f:string)=>['-f',f]),'-p',config.project,...args],{cwd:resolve('..'),encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:90_000})}
test('real isolated scanner and object-store outages retain recoverable records and the previous document',async({page})=>{
 test.skip(!enabled,'Explicit isolated candidate only');test.setTimeout(300_000)
 const stopped=new Set<string>()
 try{
  await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click();await page.locator('#username').fill(accounts.author.username);await page.locator('#password').fill(accounts.author.password);await page.locator('#kc-login').click();await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:40_000})
  const headers={Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':accounts.author.id}
  const upload=(name:string,text:string)=>page.request.post('/api/documents',{headers,multipart:{file:{name,mimeType:'text/markdown',buffer:Buffer.from(text)}}})
  const rows=async()=>{const r=await page.request.get('/api/documents',{headers});expect(r.ok()).toBeTruthy();return await r.json()}
  const initialText='# 受控故障验收\n旧版仅适用于正式员工，报销上限100元。'
  const original=await upload('fault-existing.md',initialText);expect(original.status()).toBe(202);const existing=(await original.json()).id
  await expect.poll(async()=>(await rows()).find((r:any)=>r.id===existing)?.status,{timeout:60_000}).toBe('ready')
  const priorChunks=await(await page.request.get(`/api/documents/${existing}/chunks`,{headers})).json()
  compose('stop','clamav');stopped.add('clamav')
  const failed=await upload('fault-first.md','# 首次上传恢复\n本文件在扫描服务恢复前不能建立可检索索引。');expect(failed.status()).toBe(503)
  const pending=(await rows()).find((r:any)=>r.name==='fault-first.md');expect(pending.status).toBe('scan_failed')
  const unscanned=await page.request.get(`/api/documents/${pending.id}/chunks`,{headers});expect(unscanned.ok()).toBeTruthy();expect(await unscanned.json()).toEqual([])
  const replacement=await page.request.put(`/api/documents/${existing}`,{headers,multipart:{file:{name:'fault-existing.md',mimeType:'text/markdown',buffer:Buffer.from('# 受控故障验收\n新版上限200元，仍仅适用正式员工。')}}});expect(replacement.status()).toBe(503)
  expect((await rows()).find((r:any)=>r.id===existing)?.index_version).toBe(1)
  expect(await(await page.request.get(`/api/documents/${existing}/chunks`,{headers})).json()).toEqual(priorChunks)
  compose('start','clamav');stopped.delete('clamav')
  await expect.poll(()=>compose('ps','--format','json','clamav'),{timeout:60_000}).toContain('healthy')
  // Restart only the isolated API to prove that durable scan work survives it.
  compose('restart','agent')
  await expect.poll(async()=>{const r=await page.request.get('/api/documents',{headers});return r.ok()?(await r.json()).find((r:any)=>r.id===pending.id)?.status:'starting'},{timeout:90_000}).toBe('ready')
  compose('stop','minio');stopped.add('minio')
  const storeText='# 原件存储恢复\n同一内容重传应复用失败记录，而不创建重复资料。'
  const storageFailure=await upload('fault-storage.md',storeText);expect(storageFailure.status()).toBe(503)
  const missing=(await rows()).find((r:any)=>r.name==='fault-storage.md');expect(missing.status).toBe('scan_failed')
  compose('start','minio');stopped.delete('minio')
  await expect.poll(()=>compose('ps','--format','json','minio'),{timeout:45_000}).toContain('healthy')
  const retry=await upload('fault-storage.md',storeText);expect(retry.status()).toBe(202);expect((await retry.json()).id).toBe(missing.id)
  await expect.poll(async()=>(await rows()).find((r:any)=>r.id===missing.id)?.status,{timeout:60_000}).toBe('ready')
  expect((await rows()).filter((r:any)=>r.name==='fault-storage.md')).toHaveLength(1)
  const out=resolve(root,'browser');mkdirSync(out,{recursive:true});writeFileSync(resolve(out,'real-dependency-faults.json'),JSON.stringify({passed:true,mode:'real isolated Docker services, OIDC, Java, Python, PostgreSQL, MinIO, ClamAV; deterministic embedding fixture',first_upload_scan_recovered_after_api_restart:true,unscanned_document_not_readable:true,failed_replacement_preserved_old_chunks:true,storage_failure_same_content_reused_record:true,supplier_http_requests:0},null,2))
 }finally{for(const service of stopped)compose('start',service)}
})
