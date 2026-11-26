import type { ChatMessageNode, ChatContentPart } from '@/types/api';

export const CHAT_CANVAS_WIDTH = 720;
export const CHAT_MAX_SLICE_HEIGHT = 1800;

export const CHAT_FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif';

export const AVATAR_PALETTE = [
  '#4f46e5', // indigo
  '#0891b2', // cyan
  '#059669', // emerald
  '#d97706', // amber
  '#db2777', // pink
  '#7c3aed', // violet
  '#2563eb', // blue
  '#ea580c', // orange
  '#0d9488', // teal
  '#be123c', // rose
] as const;

export interface ChatRenderLayoutConfig {
  canvasWidth: number;
  maxSliceHeight: number;
  paddingX: number;
  headerHeight: number;
  footerHeight: number;
  topPaddingAfterHeader: number;
  bottomPaddingBeforeFooter: number;
  bubbleGap: number;
  avatarSize: number;
  avatarGap: number;
  senderRowHeight: number;
  bubblePadX: number;
  bubblePadY: number;
  lineHeight: number;
  partGap: number;
  maxInlineImageHeight: number;
  fontSize: number;
  senderFontSize: number;
}

export const DEFAULT_CHAT_LAYOUT: ChatRenderLayoutConfig = {
  canvasWidth: CHAT_CANVAS_WIDTH,
  maxSliceHeight: CHAT_MAX_SLICE_HEIGHT,
  paddingX: 28,
  headerHeight: 62,
  footerHeight: 42,
  topPaddingAfterHeader: 20,
  bottomPaddingBeforeFooter: 18,
  bubbleGap: 18,
  avatarSize: 44,
  avatarGap: 14,
  senderRowHeight: 24,
  bubblePadX: 16,
  bubblePadY: 12,
  lineHeight: 28,
  partGap: 10,
  maxInlineImageHeight: 480,
  fontSize: 18,
  senderFontSize: 14,
};

export interface LoadedImageInfo {
  element?: HTMLImageElement | ImageBitmap;
  width: number;
  height: number;
  failed?: boolean;
}

export interface MeasuredTextPart {
  type: 'text';
  lines: string[];
  width: number;
  height: number;
}

export interface MeasuredImagePart {
  type: 'image';
  url?: string;
  media_id?: string;
  drawWidth: number;
  drawHeight: number;
  image?: LoadedImageInfo;
}

export type MeasuredPart = MeasuredTextPart | MeasuredImagePart;

export interface MeasuredBubble {
  index: number;
  node: ChatMessageNode;
  parts: MeasuredPart[];
  bubbleWidth: number;
  bubbleHeight: number;
  totalHeight: number;
}

export interface ChatSlicePage {
  pageIndex: number;
  bubbles: MeasuredBubble[];
  height: number;
}

export interface RenderedChatSlice {
  pageIndex: number;
  totalPages: number;
  width: number;
  height: number;
  blob: Blob;
  mime: string;
  objectUrl: string;
}

export interface RenderChatOptions {
  title?: string;
  theme?: 'light' | 'dark';
  layout?: Partial<ChatRenderLayoutConfig>;
  resolveImageUrl?: (part: { url?: string; media_id?: string }) => string;
  loadImage?: (url: string) => Promise<LoadedImageInfo>;
  quality?: number;
}

/**
 * Derive a clean 1-2 character avatar label from sender_alias (e.g. "群友A" -> "A").
 */
export function getAvatarLabel(alias: string): string {
  const trimmed = (alias || '?').trim();
  if (!trimmed) return '?';
  const match = trimmed.match(/^群友\s*([A-Za-z0-9\u4e00-\u9fa5]{1,2})$/);
  if (match) return match[1].toUpperCase();
  const chars = Array.from(trimmed);
  return chars[0].toUpperCase();
}

export function getAvatarColor(colorIdx: number): string {
  const safeIdx = Number.isFinite(colorIdx) ? Math.abs(Math.floor(colorIdx)) : 0;
  return AVATAR_PALETTE[safeIdx % AVATAR_PALETTE.length];
}

/**
 * Wrap text into visual lines respecting explicit newlines, CJK characters, emojis, and long Latin tokens.
 */
export function wrapTextLines(
  text: string,
  maxWidth: number,
  measureWidth: (str: string) => number,
): string[] {
  const normalized = (text ?? '').replace(/\r\n/g, '\n');
  const paragraphs = normalized.split('\n');
  const result: string[] = [];

  for (const para of paragraphs) {
    if (para.length === 0) {
      result.push('');
      continue;
    }

    // Use Array.from so surrogate pairs (emojis) are not split mid-codepoint
    const chars = Array.from(para);
    let current = '';

    for (const ch of chars) {
      const next = current + ch;
      if (current.length > 0 && measureWidth(next) > maxWidth) {
        // Soft break at last whitespace for ASCII words if available and reasonable
        const lastSpace = current.lastIndexOf(' ');
        if (lastSpace > 0 && /^[A-Za-z0-9_-]$/.test(ch) && lastSpace > current.length * 0.4) {
          const head = current.slice(0, lastSpace);
          const tail = current.slice(lastSpace + 1);
          result.push(head);
          current = tail + ch;
        } else {
          result.push(current);
          current = ch === ' ' ? '' : ch;
        }
      } else {
        current = next;
      }
    }

    if (current.length > 0) {
      result.push(current);
    }
  }

  return result.length > 0 ? result : [''];
}

/**
 * Compute fitted display dimensions for an inline chat image while preserving aspect ratio.
 */
export function fitInlineImageDimensions(
  naturalW: number,
  naturalH: number,
  maxWidth: number,
  maxHeight: number,
): { width: number; height: number } {
  const w = naturalW > 0 ? naturalW : 320;
  const h = naturalH > 0 ? naturalH : 240;
  const scale = Math.min(1, maxWidth / w, maxHeight / h);
  return {
    width: Math.max(48, Math.round(w * scale)),
    height: Math.max(48, Math.round(h * scale)),
  };
}

/**
 * Measure a single chat message node without drawing to canvas.
 */
export function measureBubble(
  node: ChatMessageNode,
  index: number,
  measureTextWidth: (str: string) => number,
  loadedImages: Map<string, LoadedImageInfo>,
  resolveKey: (part: { url?: string; media_id?: string }) => string,
  layout: ChatRenderLayoutConfig = DEFAULT_CHAT_LAYOUT,
): MeasuredBubble {
  const maxBubbleOuterWidth =
    layout.canvasWidth - layout.paddingX * 2 - layout.avatarSize - layout.avatarGap - 16;
  const maxInnerWidth = maxBubbleOuterWidth - layout.bubblePadX * 2;

  const measuredParts: MeasuredPart[] = [];
  let maxContentW = 0;
  let totalContentH = 0;

  const parts: ChatContentPart[] = node.content?.length
    ? node.content
    : [{ type: 'text', text: '' }];

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (i > 0) {
      totalContentH += layout.partGap;
    }

    if (part.type === 'text') {
      const lines = wrapTextLines(part.text, maxInnerWidth, measureTextWidth);
      let partW = 0;
      for (const line of lines) {
        partW = Math.max(partW, measureTextWidth(line));
      }
      partW = Math.min(maxInnerWidth, Math.max(partW, 24));
      const partH = Math.max(1, lines.length) * layout.lineHeight;
      measuredParts.push({
        type: 'text',
        lines,
        width: partW,
        height: partH,
      });
      maxContentW = Math.max(maxContentW, partW);
      totalContentH += partH;
    } else if (part.type === 'image') {
      const key = resolveKey(part);
      const loaded = key ? loadedImages.get(key) : undefined;
      const natW = loaded?.width || part.w || 360;
      const natH = loaded?.height || part.h || 240;
      const fitted = fitInlineImageDimensions(
        natW,
        natH,
        maxInnerWidth,
        layout.maxInlineImageHeight,
      );
      measuredParts.push({
        type: 'image',
        url: part.url,
        media_id: part.media_id,
        drawWidth: fitted.width,
        drawHeight: fitted.height,
        image: loaded,
      });
      maxContentW = Math.max(maxContentW, fitted.width);
      totalContentH += fitted.height;
    }
  }

  const bubbleWidth = Math.min(
    maxBubbleOuterWidth,
    Math.max(84, Math.ceil(maxContentW + layout.bubblePadX * 2)),
  );
  const bubbleHeight = Math.ceil(totalContentH + layout.bubblePadY * 2);
  const totalHeight = Math.max(layout.avatarSize, layout.senderRowHeight + bubbleHeight);

  return {
    index,
    node,
    parts: measuredParts,
    bubbleWidth,
    bubbleHeight,
    totalHeight,
  };
}

/**
 * Pure pagination engine: groups whole chat bubbles into pages with height <= maxSliceHeight.
 * Never splits a bubble across pages!
 */
export function paginateChatBubbles(
  bubbles: MeasuredBubble[],
  layout: ChatRenderLayoutConfig = DEFAULT_CHAT_LAYOUT,
): ChatSlicePage[] {
  const fixedChromeHeight =
    layout.headerHeight +
    layout.topPaddingAfterHeader +
    layout.bottomPaddingBeforeFooter +
    layout.footerHeight;

  if (bubbles.length === 0) {
    return [
      {
        pageIndex: 0,
        bubbles: [],
        height: Math.min(layout.maxSliceHeight, fixedChromeHeight + 120),
      },
    ];
  }

  const pages: ChatSlicePage[] = [];
  let currentBubbles: MeasuredBubble[] = [];
  let currentContentHeight = 0;

  for (const bubble of bubbles) {
    const gap = currentBubbles.length > 0 ? layout.bubbleGap : 0;
    const projectedPageHeight = fixedChromeHeight + currentContentHeight + gap + bubble.totalHeight;

    if (currentBubbles.length > 0 && projectedPageHeight > layout.maxSliceHeight) {
      pages.push({
        pageIndex: pages.length,
        bubbles: currentBubbles,
        height: fixedChromeHeight + currentContentHeight,
      });
      currentBubbles = [bubble];
      currentContentHeight = bubble.totalHeight;
    } else {
      currentBubbles.push(bubble);
      currentContentHeight += gap + bubble.totalHeight;
    }
  }

  if (currentBubbles.length > 0) {
    pages.push({
      pageIndex: pages.length,
      bubbles: currentBubbles,
      height: fixedChromeHeight + currentContentHeight,
    });
  }

  return pages;
}

async function defaultLoadImage(url: string): Promise<LoadedImageInfo> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      resolve({
        element: img,
        width: img.naturalWidth || 360,
        height: img.naturalHeight || 240,
      });
    };
    img.onerror = () => {
      resolve({ width: 320, height: 180, failed: true });
    };
    img.src = url;
  });
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function canvasToWebpBlob(canvas: HTMLCanvasElement, quality = 0.86): Promise<{ blob: Blob; mime: string }> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob && blob.size > 0) {
          resolve({ blob, mime: blob.type || 'image/webp' });
          return;
        }
        // Fallback to PNG only if browser fails WebP encoding
        canvas.toBlob((pngBlob) => {
          if (pngBlob) {
            resolve({ blob: pngBlob, mime: pngBlob.type || 'image/png' });
          } else {
            reject(new Error('Canvas 导出图片失败'));
          }
        }, 'image/png');
      },
      'image/webp',
      quality,
    );
  });
}

/**
 * Render structured ChatMessageNode[] into paginated 720px-wide WebP image slices (each <= 1800px height).
 */
export async function renderChatRecordToSlices(
  messages: ChatMessageNode[],
  options: RenderChatOptions = {},
): Promise<RenderedChatSlice[]> {
  const layout: ChatRenderLayoutConfig = {
    ...DEFAULT_CHAT_LAYOUT,
    ...(options.layout ?? {}),
    canvasWidth: CHAT_CANVAS_WIDTH,
    maxSliceHeight: Math.min(
      CHAT_MAX_SLICE_HEIGHT,
      options.layout?.maxSliceHeight ?? CHAT_MAX_SLICE_HEIGHT,
    ),
  };

  const resolveUrl =
    options.resolveImageUrl ?? ((part) => part.url || '');
  const imageLoader = options.loadImage ?? defaultLoadImage;

  // 1. Collect & preload all unique inline images
  const uniqueUrls = new Set<string>();
  for (const msg of messages) {
    for (const part of msg.content ?? []) {
      if (part.type === 'image') {
        const u = resolveUrl(part);
        if (u) uniqueUrls.add(u);
      }
    }
  }

  const loadedImages = new Map<string, LoadedImageInfo>();
  await Promise.all(
    Array.from(uniqueUrls).map(async (url) => {
      const info = await imageLoader(url);
      loadedImages.set(url, info);
    }),
  );

  // 2. Measure bubbles using an offscreen canvas context
  const measureCanvas = document.createElement('canvas');
  measureCanvas.width = layout.canvasWidth;
  measureCanvas.height = 100;
  const measureCtx = measureCanvas.getContext('2d');
  if (!measureCtx) {
    throw new Error('当前浏览器不支持 Canvas 2D 渲染');
  }
  measureCtx.font = `400 ${layout.fontSize}px ${CHAT_FONT_FAMILY}`;
  const measureWidth = (str: string) => measureCtx.measureText(str).width;

  const measuredBubbles = messages.map((node, idx) =>
    measureBubble(node, idx, measureWidth, loadedImages, resolveUrl, layout),
  );

  // 3. Paginate strictly by whole bubble boundaries
  const pages = paginateChatBubbles(measuredBubbles, layout);
  const totalPages = pages.length;

  // Color palette for rendered image (crisp QQ/editorial light card by default for max readability in chat apps)
  const isDark = options.theme === 'dark';
  const palette = isDark
    ? {
        bg: '#131722',
        headerBg: '#191f2e',
        headerBorder: '#283147',
        title: '#f1f5f9',
        subtitle: '#94a3b8',
        sender: '#cbd5e1',
        time: '#64748b',
        bubbleBg: '#1e2538',
        bubbleBorder: '#2d3752',
        bubbleText: '#f1f5f9',
        footerText: '#64748b',
        placeholderBg: '#273049',
        placeholderText: '#94a3b8',
      }
    : {
        bg: '#f2f4f8',
        headerBg: '#ffffff',
        headerBorder: '#e5e9f2',
        title: '#161b26',
        subtitle: '#667085',
        sender: '#475467',
        time: '#98a2b3',
        bubbleBg: '#ffffff',
        bubbleBorder: '#e4e7ec',
        bubbleText: '#101828',
        footerText: '#98a2b3',
        placeholderBg: '#e4e7ec',
        placeholderText: '#667085',
      };

  const slices: RenderedChatSlice[] = [];

  for (const page of pages) {
    const canvas = document.createElement('canvas');
    canvas.width = layout.canvasWidth;
    canvas.height = page.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D 上下文初始化失败');

    // Background
    ctx.fillStyle = palette.bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Header Bar
    ctx.fillStyle = palette.headerBg;
    ctx.fillRect(0, 0, canvas.width, layout.headerHeight);
    ctx.fillStyle = palette.headerBorder;
    ctx.fillRect(0, layout.headerHeight - 1, canvas.width, 1);

    // Header Accent Dot + Title
    ctx.fillStyle = '#4f46e5';
    ctx.beginPath();
    ctx.arc(layout.paddingX + 6, layout.headerHeight / 2, 5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = palette.title;
    ctx.font = `600 18px ${CHAT_FONT_FAMILY}`;
    ctx.textBaseline = 'middle';
    const headerTitle = options.title?.trim() || '群聊记录';
    const clippedTitle =
      headerTitle.length > 26 ? `${headerTitle.slice(0, 25)}…` : headerTitle;
    ctx.fillText(clippedTitle, layout.paddingX + 20, layout.headerHeight / 2);

    // Page indicator pill when multi-page or message count
    const rightBadge =
      totalPages > 1
        ? `第 ${page.pageIndex + 1} / ${totalPages} 页`
        : `${messages.length} 条记录`;
    ctx.font = `500 13px ${CHAT_FONT_FAMILY}`;
    const badgeW = ctx.measureText(rightBadge).width + 20;
    const badgeX = canvas.width - layout.paddingX - badgeW;
    const badgeY = (layout.headerHeight - 26) / 2;
    drawRoundedRect(ctx, badgeX, badgeY, badgeW, 26, 13);
    ctx.fillStyle = isDark ? 'rgba(129, 140, 248, 0.16)' : '#eef2ff';
    ctx.fill();
    ctx.fillStyle = isDark ? '#a5b4fc' : '#4f46e5';
    ctx.fillText(rightBadge, badgeX + 10, layout.headerHeight / 2);

    // Draw Bubbles
    let cursorY = layout.headerHeight + layout.topPaddingAfterHeader;

    for (const bubble of page.bubbles) {
      const avatarX = layout.paddingX;
      const avatarY = cursorY + 2;
      const avatarCenterX = avatarX + layout.avatarSize / 2;
      const avatarCenterY = avatarY + layout.avatarSize / 2;

      // Avatar Circle
      ctx.fillStyle = getAvatarColor(bubble.node.avatar_color_idx);
      ctx.beginPath();
      ctx.arc(avatarCenterX, avatarCenterY, layout.avatarSize / 2, 0, Math.PI * 2);
      ctx.fill();

      // Avatar Initial
      ctx.fillStyle = '#ffffff';
      ctx.font = `700 18px ${CHAT_FONT_FAMILY}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(getAvatarLabel(bubble.node.sender_alias), avatarCenterX, avatarCenterY + 1);
      ctx.textAlign = 'left';

      // Sender Alias + Time
      const contentStartX = avatarX + layout.avatarSize + layout.avatarGap;
      ctx.font = `600 ${layout.senderFontSize}px ${CHAT_FONT_FAMILY}`;
      ctx.fillStyle = palette.sender;
      ctx.textBaseline = 'top';
      const aliasText = bubble.node.sender_alias || '群友';
      ctx.fillText(aliasText, contentStartX + 2, cursorY);

      if (bubble.node.time_label) {
        const aliasW = ctx.measureText(aliasText).width;
        ctx.font = `400 12px ${CHAT_FONT_FAMILY}`;
        ctx.fillStyle = palette.time;
        ctx.fillText(bubble.node.time_label, contentStartX + aliasW + 10, cursorY + 2);
      }

      // Bubble Card
      const bubbleY = cursorY + layout.senderRowHeight;
      ctx.save();
      ctx.shadowColor = isDark ? 'rgba(0,0,0,0.28)' : 'rgba(16, 24, 40, 0.06)';
      ctx.shadowBlur = 8;
      ctx.shadowOffsetY = 2;
      drawRoundedRect(ctx, contentStartX, bubbleY, bubble.bubbleWidth, bubble.bubbleHeight, 14);
      ctx.fillStyle = palette.bubbleBg;
      ctx.fill();
      ctx.restore();

      drawRoundedRect(ctx, contentStartX, bubbleY, bubble.bubbleWidth, bubble.bubbleHeight, 14);
      ctx.strokeStyle = palette.bubbleBorder;
      ctx.lineWidth = 1;
      ctx.stroke();

      // Bubble Content Parts
      let innerY = bubbleY + layout.bubblePadY;
      const innerX = contentStartX + layout.bubblePadX;

      for (let i = 0; i < bubble.parts.length; i++) {
        if (i > 0) innerY += layout.partGap;
        const part = bubble.parts[i];

        if (part.type === 'text') {
          ctx.font = `400 ${layout.fontSize}px ${CHAT_FONT_FAMILY}`;
          ctx.fillStyle = palette.bubbleText;
          ctx.textBaseline = 'top';
          for (const line of part.lines) {
            ctx.fillText(line, innerX, innerY + 3);
            innerY += layout.lineHeight;
          }
        } else if (part.type === 'image') {
          ctx.save();
          drawRoundedRect(ctx, innerX, innerY, part.drawWidth, part.drawHeight, 10);
          ctx.clip();
          if (part.image?.element && !part.image.failed) {
            ctx.drawImage(part.image.element, innerX, innerY, part.drawWidth, part.drawHeight);
          } else {
            ctx.fillStyle = palette.placeholderBg;
            ctx.fillRect(innerX, innerY, part.drawWidth, part.drawHeight);
            ctx.fillStyle = palette.placeholderText;
            ctx.font = `500 14px ${CHAT_FONT_FAMILY}`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(
              '图片加载失败',
              innerX + part.drawWidth / 2,
              innerY + part.drawHeight / 2,
            );
            ctx.textAlign = 'left';
          }
          ctx.restore();
          innerY += part.drawHeight;
        }
      }

      cursorY += bubble.totalHeight + layout.bubbleGap;
    }

    // Footer Watermark
    const footerY = canvas.height - layout.footerHeight / 2;
    ctx.fillStyle = palette.footerText;
    ctx.font = `400 12px ${CHAT_FONT_FAMILY}`;
    ctx.textBaseline = 'middle';
    ctx.fillText('Sowing Discord Hub · 脱敏群聊存档', layout.paddingX, footerY);
    if (totalPages > 1) {
      ctx.textAlign = 'right';
      ctx.fillText(`${page.pageIndex + 1}/${totalPages}`, canvas.width - layout.paddingX, footerY);
      ctx.textAlign = 'left';
    }

    const { blob, mime } = await canvasToWebpBlob(canvas, options.quality ?? 0.86);
    slices.push({
      pageIndex: page.pageIndex,
      totalPages,
      width: canvas.width,
      height: canvas.height,
      blob,
      mime,
      objectUrl: URL.createObjectURL(blob),
    });
  }

  return slices;
}

export function revokeRenderedSlices(slices: RenderedChatSlice[]) {
  for (const s of slices) {
    if (s.objectUrl) {
      try {
        URL.revokeObjectURL(s.objectUrl);
      } catch {
        // ignore
      }
    }
  }
}
