// Cloudflare Pages Function: inject dynamic OpenGraph metadata for /item/:id share previews
// while preserving the static SPA shell and security headers.

interface PagesEnv {
  ASSETS: { fetch: (input: Request | string) => Promise<Response> };
  API_BASE_URL?: string;
}

interface PagesContext {
  request: Request;
  env: PagesEnv;
  params: { id?: string | string[] };
}

interface ApiBlock {
  type?: string;
  url?: string;
  text?: string;
  cover_url?: string;
  rendered_images?: Array<{ url?: string }>;
}

interface ApiItem {
  id?: string;
  title?: string | null;
  summary?: string | null;
  tags?: string[];
  blocks?: ApiBlock[];
}

const ITEM_ID_RE = /^[A-Za-z0-9_-]{4,64}$/;
const DEFAULT_API_BASE = 'https://banshi-api.ankaanka.org';
const SITE_ORIGIN = 'https://banshi.ankaanka.org';
const SITE_NAME = 'Sowing Discord Hub · 搬史中枢';

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function isAllowedImageHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return h === 'banshi-api.ankaanka.org' || h.endsWith('.ankaanka.org') || h.endsWith('.r2.dev');
}

function pickPreviewImage(item: ApiItem, apiBase: string): string | null {
  const blocks = Array.isArray(item.blocks) ? item.blocks : [];
  const candidates: string[] = [];
  for (const b of blocks) {
    if ((b.type === 'image' || b.type === 'gif') && b.url) {
      candidates.push(b.url);
    } else if (b.type === 'chat_record' && Array.isArray(b.rendered_images)) {
      for (const img of b.rendered_images) {
        if (img?.url) candidates.push(img.url);
      }
    } else if (b.type === 'video_link' && b.cover_url) {
      candidates.push(b.cover_url);
    }
  }

  for (const raw of candidates) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith('//') || trimmed.includes('\\')) continue;
    try {
      const resolved = trimmed.startsWith('/') ? new URL(trimmed, apiBase) : new URL(trimmed);
      if (resolved.protocol !== 'https:' || resolved.username || resolved.password) continue;
      if (!isAllowedImageHost(resolved.hostname)) continue;
      return resolved.href;
    } catch {
      continue;
    }
  }
  return null;
}

function pickDescription(item: ApiItem): string {
  const parts: string[] = [];
  if (item.summary?.trim()) {
    parts.push(item.summary.trim());
  } else {
    const firstText = (item.blocks ?? []).find((b) => b.type === 'text' && b.text?.trim())?.text?.trim();
    if (firstText) parts.push(firstText);
  }
  if (Array.isArray(item.tags) && item.tags.length > 0) {
    parts.push(item.tags.slice(0, 5).map((t) => `#${t}`).join(' '));
  }
  const joined = parts.join(' · ').replace(/\s+/g, ' ').trim();
  if (!joined) {
    return '来自搬史中枢的精选脱敏内容 · 逛史库 / 投稿 / 开源群聊搬史 Bot';
  }
  return joined.length > 140 ? `${joined.slice(0, 139)}…` : joined;
}

const SECURITY_HEADERS: Record<string, string> = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
  'Content-Security-Policy':
    "default-src 'self'; object-src 'none'; script-src 'self' https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' https://banshi-api.ankaanka.org https://*.ankaanka.org https://*.r2.dev blob:; connect-src 'self' https://banshi-api.ankaanka.org https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
};

function withSecurityHeaders(resp: Response): Response {
  const headers = new Headers(resp.headers);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) {
    if (!headers.has(k)) {
      headers.set(k, v);
    }
  }
  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText,
    headers,
  });
}

export const onRequestGet = async (context: PagesContext): Promise<Response> => {
  const { request, env, params } = context;
  const rawId = Array.isArray(params.id) ? params.id[0] : params.id || '';
  const spaUrl = new URL('/index.html', request.url);
  const assetResp = await env.ASSETS.fetch(new Request(spaUrl.toString()));

  if (!ITEM_ID_RE.test(rawId) || !assetResp.ok) {
    return withSecurityHeaders(assetResp);
  }

  const apiBase = (env.API_BASE_URL || DEFAULT_API_BASE).replace(/\/+$/, '');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);

  let item: ApiItem | null = null;
  try {
    const apiResp = await fetch(`${apiBase}/api/v1/items/${encodeURIComponent(rawId)}`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (apiResp.ok) {
      item = (await apiResp.json()) as ApiItem;
    }
  } catch {
    return withSecurityHeaders(assetResp);
  } finally {
    clearTimeout(timer);
  }

  if (!item) {
    return withSecurityHeaders(assetResp);
  }

  const pageTitle = item.title?.trim()
    ? `${item.title.trim()} · 搬史中枢`
    : `精选史料 #${rawId.slice(-6)} · 搬史中枢`;
  const description = pickDescription(item);
  const ogImage = pickPreviewImage(item, apiBase);
  const canonicalUrl = `${SITE_ORIGIN}/item/${encodeURIComponent(rawId)}`;
  const twitterCard = ogImage ? 'summary_large_image' : 'summary';

  const metaTags = [
    `<meta property="og:site_name" content="${escapeHtml(SITE_NAME)}" />`,
    `<meta property="og:type" content="article" />`,
    `<meta property="og:url" content="${escapeHtml(canonicalUrl)}" />`,
    `<meta property="og:title" content="${escapeHtml(pageTitle)}" />`,
    `<meta property="og:description" content="${escapeHtml(description)}" />`,
    ogImage ? `<meta property="og:image" content="${escapeHtml(ogImage)}" />` : '',
    `<meta name="twitter:card" content="${twitterCard}" />`,
    `<meta name="twitter:title" content="${escapeHtml(pageTitle)}" />`,
    `<meta name="twitter:description" content="${escapeHtml(description)}" />`,
    ogImage ? `<meta name="twitter:image" content="${escapeHtml(ogImage)}" />` : '',
  ]
    .filter(Boolean)
    .join('\n    ');

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const RewriterCtor = (globalThis as any).HTMLRewriter;
  if (!RewriterCtor) {
    return withSecurityHeaders(assetResp);
  }

  const rewritten = new RewriterCtor()
    .on('title', {
      element(el: { setInnerContent: (content: string) => void }) {
        el.setInnerContent(pageTitle);
      },
    })
    .on('meta[property^="og:"], meta[name^="twitter:"], meta[name="description"]', {
      element(el: { remove: () => void }) {
        el.remove();
      },
    })
    .on('head', {
      element(el: { append: (content: string, opts?: { html?: boolean }) => void }) {
        el.append(
          `\n    <meta name="description" content="${escapeHtml(description)}" />\n    ${metaTags}\n`,
          { html: true },
        );
      },
    })
    .transform(assetResp);

  return withSecurityHeaders(rewritten);
};
