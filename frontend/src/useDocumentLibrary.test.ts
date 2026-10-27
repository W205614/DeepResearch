import {describe,it,expect,vi} from 'vitest'
import {nextTick,ref} from 'vue'
const mock=vi.hoisted(()=>({api:vi.fn()}))
vi.mock('./api',()=>mock)
import {useDocumentLibrary} from './useDocumentLibrary'

describe('workspace document operations',()=>{
 it('does not display a late source response after a workspace switch',async()=>{
  let resolve!:(value:unknown)=>void
  mock.api.mockImplementationOnce(()=>new Promise(done=>{resolve=done}))
  const workspace=ref<{id:string}|null>({id:'first'})
  const library=useDocumentLibrary(workspace,async operation=>{await operation()})
  const pending=library.inspectDocument({id:'private-document',name:'private',status:'ready',index_version:1,pending_version:0,created_at:'',updated_at:''})
  workspace.value={id:'second'};await nextTick()
  resolve([{id:'secret',text:'private source'}]);await pending
  expect(library.selectedDocument.value).toBeNull()
  expect(library.documentChunks.value).toEqual([])
  expect(mock.api.mock.calls[0][1].headers['X-Workspace-ID']).toBe('first')
 })
 it('ignores an old request error without replacing the new workspace error state',async()=>{
  let reject!:(value:unknown)=>void
  mock.api.mockImplementationOnce(()=>new Promise((_,fail)=>{reject=fail}))
  const workspace=ref<{id:string}|null>({id:'first'})
  const library=useDocumentLibrary(workspace,async operation=>{await operation()})
  library.documentQuery.value='policy'
  const pending=library.searchDocuments();workspace.value={id:'second'};await nextTick()
  reject(new Error('old workspace transport error'));await pending
  expect(library.documentSearchBusy.value).toBe(false)
  expect(library.documentResults.value).toEqual([])
 })
})
