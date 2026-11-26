import { describe, expect, it, vi } from 'vitest';
import { ApiError, HubApiClient } from '@/api/client';
import { detectVideoPlatform, scanLocalPrivacyRisks } from '@/utils/format';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('HubApiClient', () => {
  it('builds URLs and serializes feed filter query parameters', async () => {
    const mockFetch = vi.fn(async () =>
      jsonResponse({ items: [], tombstones: [], next_seq: 10, has_more: false }),
    );
    const client = new HubApiClient({
      baseUrl: 'https://hub.example.org/',
      fetchImpl: mockFetch,
    });

    await client.getFeed({
      tags: ['沙雕', '群聊日常'],
      max_nsfw: 2,
      before_seq: 55,
      limit: 15,
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [calledUrl] = mockFetch.mock.calls[0] as unknown as [string];
    const parsed = new URL(calledUrl);
    expect(parsed.origin).toBe('https://hub.example.org');
    expect(parsed.pathname).toBe('/api/v1/feed');
    expect(parsed.searchParams.get('tags')).toBe('沙雕,群聊日常');
    expect(parsed.searchParams.get('max_nsfw')).toBe('2');
    expect(parsed.searchParams.get('before_seq')).toBe('55');
    expect(parsed.searchParams.get('limit')).toBe('15');
  });

  it('resolves public and admin private media URLs with JWT query token', () => {
    const client = new HubApiClient({
      baseUrl: 'https://hub.example.org',
      getToken: () => 'jwt-secret-123',
    });

    expect(client.resolveMediaUrl('/media/public/abc.webp')).toBe(
      'https://hub.example.org/media/public/abc.webp',
    );
    expect(client.resolveMediaUrl('/api/v1/admin/media/m_1', undefined, true)).toBe(
      'https://hub.example.org/api/v1/admin/media/m_1?token=jwt-secret-123',
    );
    expect(client.resolveMediaUrl(undefined, 'm_2', true)).toBe(
      'https://hub.example.org/api/v1/admin/media/m_2?token=jwt-secret-123',
    );
  });

  it('uploads submission media and handles duplicate responses cleanly', async () => {
    const mockFetch = vi.fn(async () =>
      jsonResponse({
        media_id: 'm_dup',
        duplicate: true,
        match_type: 'similar',
        existing_status: 'pending',
        existing_item_id: null,
      }),
    );
    const client = new HubApiClient({ baseUrl: '', fetchImpl: mockFetch });

    const blob = new Blob(['fake-image'], { type: 'image/png' });
    const res = await client.uploadSubmissionMedia(blob, 'cf-token-xyz', 'meme.png');

    expect(res.duplicate).toBe(true);
    expect(res.existing_status).toBe('pending');
    expect(res.existing_item_id).toBeNull();

    const [url, init] = mockFetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/submissions/media');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    const fd = init.body as FormData;
    expect(fd.get('turnstile_token')).toBe('cf-token-xyz');
    expect(fd.get('file')).toBeTruthy();
  });

  it('sends Bearer JWT on admin queue, slice upload, review, and takedown endpoints', async () => {
    const mockFetch = vi.fn(async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes('/admin/media')) {
        return jsonResponse({ media_id: 'slice_01', mime: 'image/webp', w: 720, h: 1200, duplicate: false });
      }
      if (urlStr.includes('/review')) {
        return jsonResponse({ item_id: '01JTEST', status: 'approved', seq: 101 });
      }
      if (urlStr.includes('/takedown')) {
        return jsonResponse({ item_id: '01JTEST', status: 'takedown', tombstone_seq: 102 });
      }
      return jsonResponse({ items: [], total: 0 });
    });

    const client = new HubApiClient({
      baseUrl: 'http://127.0.0.1:8000',
      fetchImpl: mockFetch,
      getToken: () => 'admin-jwt-token',
    });

    await client.getAdminQueue({ status: 'pending', limit: 20 });
    const slice = await client.uploadAdminMedia(new Blob(['webp'], { type: 'image/webp' }), 'p1.webp');
    expect(slice.media_id).toBe('slice_01');

    const reviewRes = await client.reviewItem('01JTEST', {
      action: 'approve',
      tags: ['群聊日常'],
      nsfw_level: 1,
      rendered_images: [{ media_id: slice.media_id }],
    });
    expect(reviewRes.status).toBe('approved');
    expect(reviewRes.seq).toBe(101);

    const tdRes = await client.takedownItem('01JTEST', '隐私泄露紧急下架');
    expect(tdRes.tombstone_seq).toBe(102);

    // Every admin call must carry Authorization: Bearer admin-jwt-token
    for (const call of mockFetch.mock.calls) {
      const init = (call as unknown as [string, RequestInit])[1];
      const headers = init.headers as Record<string, string>;
      expect(headers.Authorization).toBe('Bearer admin-jwt-token');
    }
  });

  it('throws structured ApiError on 410 Gone and 422 validation errors', async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Item has been taken down' }, 410))
      .mockResolvedValueOnce(
        jsonResponse({ detail: [{ msg: 'rendered_images required for chat_record' }] }, 422),
      );

    const client = new HubApiClient({ baseUrl: '', fetchImpl: mockFetch });

    await expect(client.getItem('gone_id')).rejects.toMatchObject({
      name: 'ApiError',
      status: 410,
      message: 'Item has been taken down',
    });

    try {
      await client.reviewItem('chat_id', { action: 'approve' });
      expect.fail('Expected 422 ApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(422);
      expect((err as ApiError).message).toContain('rendered_images required');
    }
  });
});

describe('format and privacy utilities', () => {
  it('detects common video link platforms', () => {
    expect(detectVideoPlatform('https://www.bilibili.com/video/BV1abc')).toBe('bilibili');
    expect(detectVideoPlatform('https://youtu.be/dQw4w9WgXcQ')).toBe('youtube');
    expect(detectVideoPlatform('https://x.com/user/status/123')).toBe('x');
  });

  it('detects phone numbers, emails, and QQ/group numbers in text', () => {
    const hits = scanLocalPrivacyRisks('联系我 13812345678 或者 test@example.com，加群 123456789');
    const labels = hits.map((h) => h.label);
    expect(labels).toContain('疑似手机号');
    expect(labels).toContain('疑似邮箱');
    expect(labels).toContain('疑似 QQ/群号');
  });
});
