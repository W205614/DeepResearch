import { expect, test, type Page } from '@playwright/test'
import { fixtureIssuer, simulateIdentity } from './fixtures/identity'

async function account(page:Page, operator=true, readonly=false) {
  await simulateIdentity(page)
  const token=`a.${Buffer.from(JSON.stringify({sub:'operator',iss:fixtureIssuer,exp:Math.floor(Date.now()/1000)+3600})).toString('base64url')}.b`
  await page.addInitScript(value=>sessionStorage.setItem('dr-token',value),token)
  const calls:{path:string;body:any}[]=[], user={subject:'alice',display_name:'Alice',status:'active',revoked_before:0,owned_workspaces:0,membership_count:1}
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname
    const reply=(json:unknown)=>route.fulfill({json})
    if(path==='/api/auth/config')return reply({mode:'oidc',issuer:fixtureIssuer})
    if(path==='/api/auth/session')return reply({subject:'operator',status:'active',platform_operator:operator})
    if(path==='/api/workspaces')return reply([{id:'w1',name:'团队研究',role:'admin'}])
    if(path==='/api/capabilities')return reply({reasons:[],submit_research:!readonly,read_only:readonly})
    if(path==='/api/status')return reply({missing:[]})
    if(path==='/api/admin/overview')return reply({operator:true,read_only:readonly,read_only_reasons:readonly?['redis_unavailable']:[],dependencies:{database:'reachable',redis:readonly?'unavailable':'reachable',agent:'not_checked'},usage:{recorded:true,period_start:1794009600,calls:12,reserved_tokens:2000,pending_tokens:100,unknown_tokens:40,actual_input:800,actual_output:200},limits:{active_runs:5,queued_runs:20,workspace_concurrent_runs:2},runs:{running:1,queued:2},users:{active:2},workspaces:[{id:'w1',name:'团队研究',created_by:'operator',owner_subject:'operator',active_admins:1}],recent_runs:[{id:'run1',created_by:'alice',status:'running',updated_at:'2026-11-07'}]})
    if(path==='/api/admin/users')return reply({items:[user,{...user,subject:'bob',display_name:'Bob'}],next_cursor:null})
    if(path==='/api/admin/audit')return reply({items:[],next_cursor:null})
    if(path.startsWith('/api/admin/')) {
      calls.push({path,body:req.postDataJSON()})
      if(path.endsWith('/status'))user.status=req.postDataJSON().status
      return reply({ok:true})
    }
    return reply([])
  })
  page.on('dialog',dialog=>dialog.accept())
  await page.goto('/')
  await expect(page.getByRole('button',{name:/退出/})).toBeVisible()
  return calls
}

test('operator records reasons for account status, ownership and cancellation',async({page})=>{
  const calls=await account(page)
  await page.getByRole('button',{name:'平台管理',exact:true}).click()
  await expect(page.getByRole('heading',{name:'平台管理',exact:true})).toBeVisible()
  await expect(page.getByText('800 / 200',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'停用账号 Alice'})).toBeDisabled()
  await page.locator('#platform-reason').fill('离职交接已核对')
  await page.getByRole('button',{name:'停用账号 Alice'}).click()
  await expect(page.getByRole('button',{name:'恢复账号 Alice'})).toBeVisible()
  await page.locator('#platform-reason').fill('恢复已完成')
  await page.getByRole('button',{name:'恢复账号 Alice'}).click()
  await page.locator('#platform-reason').fill('空间负责人调整')
  await page.getByRole('combobox',{name:'为 团队研究 选择新所有者'}).selectOption('bob')
  await page.getByRole('button',{name:'交接空间',exact:true}).click()
  await page.locator('#platform-reason').fill('取消重复任务')
  await page.getByRole('button',{name:'取消任务 run1'}).click()
  await expect.poll(()=>calls.length).toBe(4)
  expect(calls.map(call=>call.body)).toEqual([{status:'disabled',reason:'离职交接已核对'},{status:'active',reason:'恢复已完成'},{target_subject:'bob',reason:'空间负责人调整'},{reason:'取消重复任务'}])
})

test('readonly keeps governance visible and disables mutations',async({page})=>{
  const calls=await account(page,true,true)
  await page.getByRole('button',{name:'平台管理',exact:true}).click()
  await page.locator('#platform-reason').fill('仍不可写')
  await expect(page.getByRole('button',{name:'停用账号 Alice'})).toBeDisabled()
  await expect(page.getByRole('button',{name:'取消任务 run1'})).toBeDisabled()
  await page.getByRole('button',{name:'研究记忆',exact:true}).click()
  await page.getByLabel('添加一条研究偏好').fill('只读演练')
  await expect(page.getByRole('button',{name:'保存偏好'})).toBeDisabled()
  expect(calls).toEqual([])
})

test('workspace admin cannot see platform operator entry',async({page})=>{
  await account(page,false)
  await expect(page.getByRole('button',{name:'平台管理',exact:true})).toHaveCount(0)
})

test('platform governance stays usable in a narrow viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await account(page)
  await page.getByRole('button',{name:'打开导航',exact:true}).click()
  await page.getByRole('button',{name:'平台管理',exact:true}).click()
  await expect(page.getByRole('heading',{name:'平台管理',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
  await page.locator('#platform-reason').fill('移动端交接核对')
  await expect(page.getByRole('button',{name:'停用账号 Alice'})).toBeEnabled()
})
