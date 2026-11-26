import type { NsfwLevel } from '@/types/api';

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return '刚刚';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)} 天前`;
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null || Number.isNaN(bytes)) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDuration(sec?: number | null): string {
  if (!sec || sec <= 0) return '';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function nsfwMeta(level: NsfwLevel): {
  label: string;
  shortLabel: string;
  badgeClass: string;
  description: string;
} {
  switch (level) {
    case 0:
      return {
        label: '全年龄安全 (L0)',
        shortLabel: 'L0 安全',
        badgeClass: 'badge-success',
        description: '所有群聊默认可见',
      };
    case 1:
      return {
        label: '轻度敏感 (L1)',
        shortLabel: 'L1 轻度',
        badgeClass: 'badge-warning',
        description: '地狱笑话 / 轻度抽象 / 轻擦边（插件默认上限）',
      };
    case 2:
      return {
        label: '高能预警 (L2)',
        shortLabel: 'L2 高能',
        badgeClass: 'badge-danger',
        description: '重度抽象 / 明显擦边（需显式开启）',
      };
    default:
      return {
        label: `等级 ${level}`,
        shortLabel: `L${level}`,
        badgeClass: '',
        description: '',
      };
  }
}

export function detectVideoPlatform(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes('bilibili.com') || host.includes('b23.tv')) return 'bilibili';
    if (host.includes('youtube.com') || host.includes('youtu.be')) return 'youtube';
    if (host.includes('douyin.com')) return 'douyin';
    if (host.includes('x.com') || host.includes('twitter.com')) return 'x';
    if (host.includes('xiaohongshu.com')) return 'xiaohongshu';
    return host.replace(/^www\./, '');
  } catch {
    return 'link';
  }
}

export function safeHttpUrl(url?: string | null): string | null {
  if (!url) return null;
  const trimmed = url.trim();
  if (!trimmed || /[\u0000-\u001f\u007f\\]/.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    if (parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export interface LocalPrivacyMatch {
  label: string;
  excerpt: string;
}

/**
 * Client-side privacy pattern scanner (supplements backend warnings in real time during review/submission).
 */
export function scanLocalPrivacyRisks(text: string): LocalPrivacyMatch[] {
  const findings: LocalPrivacyMatch[] = [];
  if (!text) return findings;

  const seen = new Set<string>();
  const addFinding = (label: string, excerpt: string) => {
    const key = `${label}:${excerpt}`;
    if (!seen.has(key)) {
      seen.add(key);
      findings.push({ label, excerpt });
    }
  };

  const patterns: Array<{ label: string; regex: RegExp; filter?: (m: RegExpExecArray) => boolean }> = [
    { label: '疑似身份证号', regex: /(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)/g },
    { label: '疑似手机号', regex: /(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)/g },
    { label: '疑似邮箱', regex: /(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,24}(?![A-Za-z])/g },
    { label: '疑似微信号', regex: /(?:微信号|微信|(?<![A-Za-z0-9])(?:vx|VX|wx|WX|[Ww]e[Cc]hat))(?:\s{0,4}[:：]\s{0,4}|\s{0,4})([a-zA-Z][a-zA-Z0-9_-]{5,19})/g },
    { label: '疑似 QQ/群号', regex: /(?:QQ|qq|扣扣|群号|加群|联系|加我)[^\d]{0,6}([1-9]\d{4,11})(?!\d)/g },
    {
      label: '疑似 QQ/群号',
      regex: /(?<!\d)([1-9]\d{6,10})(?!\d)/g,
      filter: (m) => !(m[1].length === 8 && (m[1].startsWith('19') || m[1].startsWith('20'))),
    },
  ];

  for (const { label, regex, filter } of patterns) {
    let m: RegExpExecArray | null;
    while ((m = regex.exec(text)) !== null) {
      if (filter && !filter(m)) continue;
      addFinding(label, m[0]);
    }
  }
  return findings;
}
