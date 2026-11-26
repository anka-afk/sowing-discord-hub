import { describe, expect, it } from 'vitest';
import { buildTagCatalog, parseCustomTagInput } from '@/utils/tags';

describe('tags utils', () => {
  it('parses multi-separator custom tags cleanly and strips leading #', () => {
    expect(parseCustomTagInput('#沙雕, 抽象、程序员  #猫猫；沙雕')).toEqual([
      '沙雕',
      '抽象',
      '程序员',
      '猫猫',
    ]);
  });

  it('truncates overly long tags and ignores empty tokens', () => {
    const longTag = '一二三四五六七八九十一二三四五六七八九十超长尾巴';
    const parsed = parseCustomTagInput(`  ###  , ${longTag} `);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].length).toBe(20);
  });

  it('builds deduplicated tag catalog prioritizing server tags then presets and recents', () => {
    const catalog = buildTagCatalog(
      ['搬史经典', '沙雕'],
      ['自定义热梗', '沙雕'],
      ['冷门标签'],
    );
    expect(catalog[0]).toBe('搬史经典');
    expect(catalog[1]).toBe('沙雕');
    expect(catalog).toContain('自定义热梗');
    expect(catalog).toContain('冷门标签');
    expect(new Set(catalog).size).toBe(catalog.length);
  });
});
