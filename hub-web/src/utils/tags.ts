const RECENT_TAGS_KEY = 'sowing_hub_admin_recent_tags_v1';
const LAST_TAGS_KEY = 'sowing_hub_admin_last_tags_v1';
const MAX_RECENT_TAGS = 12;
const MAX_TAG_LENGTH = 20;

export const FALLBACK_QUICK_TAGS: readonly string[] = [
  '沙雕',
  '群聊日常',
  '地狱笑话',
  '二次元',
  '抽象',
  '程序员',
  '游戏',
  '猫猫狗狗',
  '科技',
  '影视',
  '生活',
  '搬史经典',
];

/**
 * Parse a free-form tag input string into clean, deduplicated tag names.
 * Supports spaces, English/Chinese commas, enumeration commas, semicolons, and optional leading '#'.
 */
export function parseCustomTagInput(raw: string): string[] {
  if (!raw) return [];
  const parts = raw
    .split(/[,，、;；\s]+/)
    .map((part) => part.trim().replace(/^#+/, '').trim())
    .filter((part) => part.length > 0)
    .map((part) => part.slice(0, MAX_TAG_LENGTH));

  const seen = new Set<string>();
  const result: string[] = [];
  for (const tag of parts) {
    if (!seen.has(tag)) {
      seen.add(tag);
      result.push(tag);
    }
  }
  return result;
}

/**
 * Merge server-configured tags, moderator's recently used custom tags, fallback presets,
 * and tags already attached to the current item into a stable deduplicated catalog.
 * Priority: serverTags -> fallback presets -> recentTags -> currentSelected.
 */
export function buildTagCatalog(
  serverTags: readonly string[] = [],
  recentTags: readonly string[] = [],
  currentSelected: readonly string[] = [],
): string[] {
  const seen = new Set<string>();
  const result: string[] = [];

  const pushUnique = (list: readonly string[]) => {
    for (const raw of list) {
      const clean = raw.trim().replace(/^#+/, '');
      if (clean && !seen.has(clean)) {
        seen.add(clean);
        result.push(clean);
      }
    }
  };

  if (serverTags.length > 0) {
    pushUnique(serverTags);
  }
  pushUnique(FALLBACK_QUICK_TAGS);
  pushUnique(recentTags);
  pushUnique(currentSelected);

  return result;
}

/**
 * Load moderator's recently used custom tags from localStorage safely.
 */
export function loadRecentTags(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_TAGS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((x): x is string => typeof x === 'string' && x.trim().length > 0)
      .slice(0, MAX_RECENT_TAGS);
  } catch {
    return [];
  }
}

/**
 * Record tags into moderator's recent tag history (MRU order) and persist to localStorage.
 */
export function saveRecentTags(added: readonly string[], existing: readonly string[] = []): string[] {
  const next: string[] = [];
  const seen = new Set<string>();

  for (const t of [...added, ...existing]) {
    const clean = t.trim().replace(/^#+/, '');
    if (clean && !seen.has(clean)) {
      seen.add(clean);
      next.push(clean);
    }
    if (next.length >= MAX_RECENT_TAGS) break;
  }

  try {
    localStorage.setItem(RECENT_TAGS_KEY, JSON.stringify(next));
  } catch {
    // Ignore storage quota / privacy mode errors
  }
  return next;
}

export function loadLastApprovedTags(): string[] {
  try {
    const raw = localStorage.getItem(LAST_TAGS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
  } catch {
    return [];
  }
}

export function saveLastApprovedTags(tags: readonly string[]): string[] {
  const clean = parseCustomTagInput(tags.join(' '));
  try {
    localStorage.setItem(LAST_TAGS_KEY, JSON.stringify(clean));
  } catch {
    // Ignore storage errors
  }
  return clean;
}
