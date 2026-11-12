import { describe, expect, test } from 'vitest'
import { brief, textWidth, wrap } from './format'

describe('wrap: SVG text in lines, broken where Chinese breaks', () => {
  test('a line that fits stays one; a band subtitle breaks before its ` · `, never inside a stage', () => {
    expect(wrap('发布审批 · 产品验收', 16, 2)).toEqual(['发布审批 · 产品验收'])
    expect(wrap('2.3 需求分析 · 2.6 应用设计 · 2.7 拆分单元', 17.4, 2)).toEqual(['2.3 需求分析 · 2.6 应用设计', '2.7 拆分单元'])
  })

  test('a skipped list breaks after its 、 and keeps every word; lines come out about one length; a Latin word stays whole', () => {
    const lines = wrap('跳过：CI 流水线 · 每个单元都跳过了功能设计、非功能设计、基础设施设计', 16.6, 3)
    expect(lines).toEqual(['跳过：CI 流水线', '每个单元都跳过了功能设计、', '非功能设计、基础设施设计'])
    expect(wrap('跟大需求一起回写 Brain', 5.1, 5)).toEqual(['跟大需求', '一起回写', 'Brain'])
  })

  test('a file name breaks after a hyphen; what does not fit in the lines it has is clipped, never lost silently', () => {
    expect(wrap('application-design.md', 9.6, 2)).toEqual(['application-', 'design.md'])
    const two = wrap('跳过：CI 流水线 · 每个单元都跳过了功能设计、非功能设计、基础设施设计', 16.6, 2)
    expect(two).toHaveLength(2)
    expect(two[1]).toMatch(/…$/)
    for (const l of two) expect(textWidth(l)).toBeLessThanOrEqual(16.6)
  })
})

describe('brief: one line, the core words first', () => {
  test('whole when it fits; else without its closing （…）; else before its ：; else clipped', () => {
    expect(brief('等产品验收', 10)).toBe('等产品验收')
    expect(brief('等产品在 MR 上验收 D-3（approve / reject）', 14)).toBe('等产品在 MR 上验收 D-3')
    expect(brief('等你批准需求和设计：读完再在会话里自己打那一行', 12)).toBe('等你批准需求和设计')
    expect(brief('等小需求合进来：已合并 1/3（1 个待认领，1 个在做）', 8)).toBe('等小需求合进来')
    expect(brief('等你批准 refund-api 的代码生成计划：读完再在会话里自己打那一行', 12, '等你批 · 代码生成计划')).toBe('等你批 · 代码生成计划')
    expect(brief('一句很长很长很长很长的话没有可以省掉的部分', 8)).toMatch(/…$/)
  })
})
