import { describe, expect, it } from 'vitest'
import { parseFrame, terminal } from './api'
describe('durable event stream',()=>{
  it('ignores heartbeats',()=>expect(parseFrame(': keep-alive')).toBeNull())
  it('parses unicode event data',()=>expect(parseFrame('id: 4\ndata: {"id":4,"type":"node_start","data":{"node":"证据"}}')?.valueOf()).toEqual({id:4,type:'node_start',data:{node:'证据'}}))
  it('closes explicitly',()=>expect(parseFrame('event: close\ndata: {}')).toBe('close'))
  it('recognizes interrupted and insufficient tasks as terminal',()=>{expect(terminal('interrupted')).toBe(true);expect(terminal('insufficient')).toBe(true);expect(terminal('running')).toBe(false)})
})
