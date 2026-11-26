import type {
  AdminLoginResponse,
  AdminQueueItem,
  AdminQueueResponse,
  AdminReviewRequest,
  AdminReviewResponse,
  AdminStatsResponse,
  AdminTakedownResponse,
  FeedResponse,
  HubMetaResponse,
  MediaUploadResponse,
  MemeItem,
  NsfwLevel,
  SubmissionCreateRequest,
  SubmissionCreateResponse,
} from '@/types/api';

const TOKEN_STORAGE_KEY = 'sowing_hub_admin_token';
const ADMIN_USER_KEY = 'sowing_hub_admin_user';

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiClientConfig {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
}

export class HubApiClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly getTokenFn: () => string | null;

  constructor(config: ApiClientConfig = {}) {
    const envBase =
      typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL
        ? String(import.meta.env.VITE_API_BASE_URL)
        : '';
    this.baseUrl = (config.baseUrl ?? envBase).replace(/\/+$/, '');
    this.fetchImpl = config.fetchImpl ?? ((...args) => globalThis.fetch(...args));
    this.getTokenFn =
      config.getToken ??
      (() => {
        try {
          return globalThis.localStorage?.getItem(TOKEN_STORAGE_KEY) ?? null;
        } catch {
          return null;
        }
      });
  }

  buildUrl(path: string, params?: Record<string, string | number | boolean | undefined | null>): string {
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    const full = `${this.baseUrl}${normalizedPath}`;
    if (!params) return full;

    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue;
      search.set(key, String(value));
    }
    const qs = search.toString();
    return qs ? `${full}?${qs}` : full;
  }

  resolveMediaUrl(urlOrPath?: string | null, mediaId?: string | null, isAdminContext = false): string {
    const token = this.getTokenFn();
    if (urlOrPath) {
      if (/^https?:\/\//i.test(urlOrPath) || urlOrPath.startsWith('blob:') || urlOrPath.startsWith('data:')) {
        if (isAdminContext && token && urlOrPath.includes('/api/v1/admin/media/') && !urlOrPath.includes('token=')) {
          const sep = urlOrPath.includes('?') ? '&' : '?';
          return `${urlOrPath}${sep}token=${encodeURIComponent(token)}`;
        }
        return urlOrPath;
      }
      const prefixed = `${this.baseUrl}${urlOrPath.startsWith('/') ? '' : '/'}${urlOrPath}`;
      if (isAdminContext && token && urlOrPath.includes('/api/v1/admin/media/') && !prefixed.includes('token=')) {
        const sep = prefixed.includes('?') ? '&' : '?';
        return `${prefixed}${sep}token=${encodeURIComponent(token)}`;
      }
      return prefixed;
    }
    if (mediaId) {
      if (isAdminContext) {
        return this.buildUrl(`/api/v1/admin/media/${encodeURIComponent(mediaId)}`, {
          token: token ?? undefined,
        });
      }
      return this.buildUrl(`/media/public/${encodeURIComponent(mediaId)}`);
    }
    return '';
  }

  private async request<T>(
    path: string,
    options: {
      method?: string;
      params?: Record<string, string | number | boolean | undefined | null>;
      body?: unknown;
      formData?: FormData;
      auth?: boolean;
      headers?: Record<string, string>;
    } = {},
  ): Promise<T> {
    const url = this.buildUrl(path, options.params);
    const headers: Record<string, string> = { ...(options.headers ?? {}) };

    if (options.auth) {
      const token = this.getTokenFn();
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }
    }

    let bodyPayload: BodyInit | undefined;
    if (options.formData) {
      bodyPayload = options.formData;
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      bodyPayload = JSON.stringify(options.body);
    }

    const res = await this.fetchImpl(url, {
      method: options.method ?? 'GET',
      headers,
      body: bodyPayload,
    });

    if (!res.ok) {
      let detail: unknown = undefined;
      let message = `请求失败 (${res.status})`;
      try {
        const errJson = await res.json();
        detail = errJson;
        if (typeof errJson?.detail === 'string') {
          message = errJson.detail;
        } else if (Array.isArray(errJson?.detail) && errJson.detail[0]?.msg) {
          message = errJson.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ');
        } else if (typeof errJson?.message === 'string') {
          message = errJson.message;
        }
      } catch {
        const text = await res.text().catch(() => '');
        if (text) message = text.slice(0, 200);
      }
      throw new ApiError(message, res.status, detail);
    }

    if (res.status === 204) {
      return undefined as T;
    }
    return (await res.json()) as T;
  }

  // --- Public Endpoints ---

  async getMeta(): Promise<HubMetaResponse> {
    return this.request<HubMetaResponse>('/api/v1/meta');
  }

  async getFeed(params: {
    after_seq?: number;
    before_seq?: number;
    limit?: number;
    tags?: string[];
    exclude_tags?: string[];
    max_nsfw?: NsfwLevel;
  } = {}): Promise<FeedResponse> {
    return this.request<FeedResponse>('/api/v1/feed', {
      params: {
        after_seq: params.after_seq,
        before_seq: params.before_seq,
        limit: params.limit ?? 20,
        tags: params.tags?.length ? params.tags.join(',') : undefined,
        exclude_tags: params.exclude_tags?.length ? params.exclude_tags.join(',') : undefined,
        max_nsfw: params.max_nsfw ?? 1,
      },
    });
  }

  async getRandom(params: {
    limit?: number;
    tags?: string[];
    max_nsfw?: NsfwLevel;
  } = {}): Promise<{ items: MemeItem[] }> {
    return this.request<{ items: MemeItem[] }>('/api/v1/random', {
      params: {
        limit: params.limit ?? 12,
        tags: params.tags?.length ? params.tags.join(',') : undefined,
        max_nsfw: params.max_nsfw ?? 1,
      },
    });
  }

  async getItem(id: string): Promise<MemeItem> {
    return this.request<MemeItem>(`/api/v1/items/${encodeURIComponent(id)}`);
  }

  // --- Submissions ---

  async uploadSubmissionMedia(file: File | Blob, turnstileToken?: string, filename?: string): Promise<MediaUploadResponse> {
    const formData = new FormData();
    const resolvedName = filename ?? (file instanceof File ? file.name : 'upload.webp');
    formData.append('file', file, resolvedName);
    if (turnstileToken) {
      formData.append('turnstile_token', turnstileToken);
    }
    return this.request<MediaUploadResponse>('/api/v1/submissions/media', {
      method: 'POST',
      formData,
    });
  }

  async createSubmission(payload: SubmissionCreateRequest): Promise<SubmissionCreateResponse> {
    return this.request<SubmissionCreateResponse>('/api/v1/submissions', {
      method: 'POST',
      body: payload,
    });
  }

  // --- Admin Endpoints ---

  async adminLogin(username: string, password: string): Promise<AdminLoginResponse> {
    return this.request<AdminLoginResponse>('/api/v1/admin/login', {
      method: 'POST',
      body: { username, password },
    });
  }

  async getAdminQueue(params: {
    status?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<AdminQueueResponse> {
    const raw = await this.request<AdminQueueResponse | AdminQueueItem[]>('/api/v1/admin/queue', {
      auth: true,
      params: {
        status: params.status ?? 'pending',
        limit: params.limit ?? 20,
        offset: params.offset ?? 0,
      },
    });
    if (Array.isArray(raw)) {
      return { items: raw, total: raw.length };
    }
    return raw;
  }

  async uploadAdminMedia(blob: Blob, filename = 'chat_slice.webp'): Promise<MediaUploadResponse> {
    const formData = new FormData();
    formData.append('file', blob, filename);
    return this.request<MediaUploadResponse>('/api/v1/admin/media', {
      method: 'POST',
      auth: true,
      formData,
    });
  }

  async reviewItem(itemId: string, payload: AdminReviewRequest): Promise<AdminReviewResponse> {
    return this.request<AdminReviewResponse>(`/api/v1/admin/items/${encodeURIComponent(itemId)}/review`, {
      method: 'POST',
      auth: true,
      body: payload,
    });
  }

  async takedownItem(itemId: string, reason: string): Promise<AdminTakedownResponse> {
    return this.request<AdminTakedownResponse>(`/api/v1/admin/items/${encodeURIComponent(itemId)}/takedown`, {
      method: 'POST',
      auth: true,
      body: { reason },
    });
  }

  async getAdminStats(): Promise<AdminStatsResponse> {
    return this.request<AdminStatsResponse>('/api/v1/admin/stats', {
      auth: true,
    });
  }

  async fetchBlobWithAuth(url: string): Promise<Blob> {
    const token = this.getTokenFn();
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const res = await this.fetchImpl(url, { headers });
    if (!res.ok) {
      throw new ApiError(`加载媒体失败 (${res.status})`, res.status);
    }
    return await res.blob();
  }
}

export const api = new HubApiClient();

export { TOKEN_STORAGE_KEY, ADMIN_USER_KEY };
