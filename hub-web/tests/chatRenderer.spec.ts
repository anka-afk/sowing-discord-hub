import { describe, expect, it, vi } from 'vitest';
import type { ChatMessageNode } from '@/types/api';
import {
  CHAT_CANVAS_WIDTH,
  CHAT_MAX_SLICE_HEIGHT,
  DEFAULT_CHAT_LAYOUT,
  fitInlineImageDimensions,
  getAvatarColor,
  getAvatarLabel,
  measureBubble,
  paginateChatBubbles,
  renderChatRecordToSlices,
  revokeRenderedSlices,
  wrapTextLines,
  type LoadedImageInfo,
} from '@/utils/chatRenderer';

// Deterministic monospace-like width estimator for pure layout tests (18px CJK, 10px ASCII)
const fakeMeasure = (str: string) => {
  let width = 0;
  for (const ch of Array.from(str)) {
    width += ch.charCodeAt(0) > 255 ? 18 : 10;
  }
  return width;
};

describe('chatRenderer helpers', () => {
  it('derives crisp avatar labels and deterministic colors', () => {
    expect(getAvatarLabel('群友A')).toBe('A');
    expect(getAvatarLabel('群友 b')).toBe('B');
    expect(getAvatarLabel('群友12')).toBe('12');
    expect(getAvatarLabel('摸鱼大王')).toBe('摸');
    expect(getAvatarLabel('')).toBe('?');

    expect(getAvatarColor(0)).toMatch(/^#[0-9a-f]{6}$/i);
    expect(getAvatarColor(10)).toBe(getAvatarColor(0));
    expect(getAvatarColor(-3)).toBe(getAvatarColor(3));
  });

  it('wraps CJK text, explicit newlines, and preserves emojis intact', () => {
    const lines = wrapTextLines('第一行\n这是一段特别长的中文测试消息😂🔥用来验证自动换行', 160, fakeMeasure);
    expect(lines[0]).toBe('第一行');
    expect(lines.length).toBeGreaterThanOrEqual(3);
    // Rejoined text should contain intact emojis with no broken surrogate code units
    const rejoined = lines.slice(1).join('');
    expect(rejoined).toContain('😂🔥');
    for (const line of lines) {
      expect(fakeMeasure(line)).toBeLessThanOrEqual(160);
    }
  });

  it('fits inline image dimensions without exceeding bounds or upscaling', () => {
    expect(fitInlineImageDimensions(200, 150, 500, 480)).toEqual({ width: 200, height: 150 });
    const tall = fitInlineImageDimensions(800, 2400, 500, 480);
    expect(tall.height).toBe(480);
    expect(tall.width).toBe(160);
    const wide = fitInlineImageDimensions(2000, 1000, 500, 480);
    expect(wide.width).toBe(500);
    expect(wide.height).toBe(250);
  });
});

describe('chatRenderer pagination engine', () => {
  it('keeps short conversations on a single page <= 1800px', () => {
    const nodes: ChatMessageNode[] = [
      {
        sender_alias: '群友A',
        avatar_color_idx: 0,
        time_label: '14:20',
        content: [{ type: 'text', text: '今天又是充满抽象的一天' }],
      },
      {
        sender_alias: '群友B',
        avatar_color_idx: 1,
        time_label: '14:21',
        content: [{ type: 'text', text: '确实，绷不住了' }],
      },
    ];

    const loaded = new Map<string, LoadedImageInfo>();
    const measured = nodes.map((n, i) =>
      measureBubble(n, i, fakeMeasure, loaded, (p) => p.url || ''),
    );
    const pages = paginateChatBubbles(measured, DEFAULT_CHAT_LAYOUT);

    expect(pages).toHaveLength(1);
    expect(pages[0].bubbles).toHaveLength(2);
    expect(pages[0].height).toBeLessThanOrEqual(CHAT_MAX_SLICE_HEIGHT);
  });

  it('strictly paginates tall chats on whole bubble boundaries with every page <= 1800px', () => {
    // 16 messages each with text + a 480px tall inline image -> ~580px per bubble -> ~9300px total
    const nodes: ChatMessageNode[] = Array.from({ length: 16 }, (_, idx) => ({
      sender_alias: `群友${String.fromCharCode(65 + (idx % 6))}`,
      avatar_color_idx: idx % 6,
      time_label: `14:${String(10 + idx).padStart(2, '0')}`,
      content: [
        { type: 'text', text: `第 ${idx + 1} 条消息：这是一条带大图的长对话测试内容` },
        { type: 'image', url: `https://cdn.example/img_${idx}.webp`, w: 900, h: 900 },
      ],
    }));

    const loaded = new Map<string, LoadedImageInfo>();
    for (let i = 0; i < 16; i++) {
      loaded.set(`https://cdn.example/img_${i}.webp`, { width: 900, height: 900 });
    }

    const measured = nodes.map((n, i) =>
      measureBubble(n, i, fakeMeasure, loaded, (p) => p.url || ''),
    );
    const pages = paginateChatBubbles(measured, DEFAULT_CHAT_LAYOUT);

    expect(pages.length).toBeGreaterThanOrEqual(5);

    const seenIndices: number[] = [];
    for (let p = 0; p < pages.length; p++) {
      const page = pages[p];
      expect(page.pageIndex).toBe(p);
      expect(page.bubbles.length).toBeGreaterThanOrEqual(1);
      expect(page.height).toBeLessThanOrEqual(CHAT_MAX_SLICE_HEIGHT);
      for (const b of page.bubbles) {
        seenIndices.push(b.index);
      }
    }

    // Verify no bubble was lost, duplicated, or reordered
    expect(seenIndices).toEqual(Array.from({ length: 16 }, (_, i) => i));
  });

  it('keeps tall phone screenshots crisp (684x1520 instead of tiny 216px postage stamp)', () => {
    const node: ChatMessageNode = {
      sender_alias: '群友A',
      avatar_color_idx: 0,
      content: [{ type: 'image', url: 'https://cdn.example/phone.webp', w: 864, h: 1920 }],
    };
    const loaded = new Map<string, LoadedImageInfo>([
      ['https://cdn.example/phone.webp', { width: 864, height: 1920 }],
    ]);
    const measured = measureBubble(node, 0, fakeMeasure, loaded, (p) => p.url || '');
    const imgPart = measured.parts[0];
    expect(imgPart.type).toBe('image');
    if (imgPart.type === 'image') {
      expect(imgPart.drawWidth).toBeGreaterThanOrEqual(680);
      expect(imgPart.drawHeight).toBe(1520);
    }
    const pages = paginateChatBubbles([measured], DEFAULT_CHAT_LAYOUT);
    expect(pages).toHaveLength(1);
    expect(pages[0].height).toBeLessThanOrEqual(CHAT_MAX_SLICE_HEIGHT);
  });

  it('handles empty chat arrays gracefully', () => {
    const pages = paginateChatBubbles([], DEFAULT_CHAT_LAYOUT);
    expect(pages).toHaveLength(1);
    expect(pages[0].bubbles).toHaveLength(0);
    expect(pages[0].height).toBeLessThanOrEqual(CHAT_MAX_SLICE_HEIGHT);
  });
});

describe('renderChatRecordToSlices canvas pipeline', () => {
  it('renders 720px-wide WebP blobs across multiple slices and revokes object URLs', async () => {
    const fakeCtx = {
      font: '',
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 1,
      textBaseline: 'top',
      textAlign: 'left',
      shadowColor: '',
      shadowBlur: 0,
      shadowOffsetY: 0,
      measureText: (txt: string) => ({ width: fakeMeasure(txt) }),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      beginPath: vi.fn(),
      arc: vi.fn(),
      arcTo: vi.fn(),
      moveTo: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      clip: vi.fn(),
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D;

    const getCtxSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(fakeCtx);
    const toBlobSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'toBlob')
      .mockImplementation(function (cb, mime) {
        cb(new Blob(['webp-bytes'], { type: mime || 'image/webp' }));
      });
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    const createUrlMock = vi.fn(() => `blob:slice-${Math.random()}`);
    const revokeUrlMock = vi.fn();
    URL.createObjectURL = createUrlMock;
    URL.revokeObjectURL = revokeUrlMock;

    try {
      const messages: ChatMessageNode[] = Array.from({ length: 10 }, (_, idx) => ({
        sender_alias: `群友${idx + 1}`,
        avatar_color_idx: idx,
        content: [
          { type: 'text', text: `消息 #${idx + 1}` },
          { type: 'image', url: `mock://pic/${idx}`, w: 600, h: 600 },
        ],
      }));

      const slices = await renderChatRecordToSlices(messages, {
        title: '凌晨三点技术群',
        loadImage: async () => ({ width: 600, height: 600 }),
      });

      expect(slices.length).toBeGreaterThan(1);
      const drawnTexts = (fakeCtx.fillText as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
      expect(drawnTexts).toContain('搬史中枢 · banshi.ankaanka.org');
      expect(drawnTexts.some((t) => t.includes('脱敏存档 · 逛史库 / 投稿'))).toBe(true);
      for (const s of slices) {
        expect(s.width).toBe(CHAT_CANVAS_WIDTH);
        expect(s.height).toBeLessThanOrEqual(CHAT_MAX_SLICE_HEIGHT);
        expect(s.mime).toBe('image/webp');
        expect(s.blob.size).toBeGreaterThan(0);
        expect(s.totalPages).toBe(slices.length);
      }

      revokeRenderedSlices(slices);
      expect(revokeUrlMock).toHaveBeenCalledTimes(slices.length);
    } finally {
      getCtxSpy.mockRestore();
      toBlobSpy.mockRestore();
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
