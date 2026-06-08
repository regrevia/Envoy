export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8140';
export const API_TIMEOUT_MS = 15000;

export function apiUrl(path: string) {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

type RequestOptions = RequestInit & {
  timeoutMs?: number;
  expectJson?: boolean;
};

export class ApiError extends Error {
  status?: number;
  details?: string;

  constructor(message: string, status?: number, details?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

async function readBody(response: Response, expectJson: boolean) {
  if (response.status === 204) return null;
  if (expectJson) return response.json();
  return response.text();
}

export async function apiRequest<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = API_TIMEOUT_MS, expectJson = true, ...init } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      signal: controller.signal,
      credentials: 'include',
      headers: {
        ...(init.headers || {}),
      },
    });

    const body = await readBody(response, expectJson);

    if (!response.ok) {
      const details = typeof body === 'string' ? body : body ? JSON.stringify(body) : '';
      throw new ApiError(details || `HTTP ${response.status}`, response.status, details);
    }

    return body as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`请求超时：${timeoutMs}ms`);
    }
    if (error instanceof ApiError) throw error;
    const message = error instanceof Error ? error.message : '请求失败';
    throw new ApiError(message);
  } finally {
    window.clearTimeout(timer);
  }
}

export async function apiJson<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  return apiRequest<T>(path, { ...options, expectJson: true });
}

export async function apiText(path: string, options: RequestOptions = {}): Promise<string> {
  return apiRequest<string>(path, { ...options, expectJson: false });
}

export async function apiBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await fetch(apiUrl(path), options);
  if (!response.ok) {
    throw new ApiError(`HTTP ${response.status}`, response.status);
  }
  return response.blob();
}
