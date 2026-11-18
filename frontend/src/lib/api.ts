const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    /** 后端给的错误码，界面据此查自己的文案。没有就只能显示 message */
    public code?: string,
    public params?: Record<string, unknown>,
  ) {
    super(message);
  }
}

/** 后端的 detail 有两种形态：`{code, params}`（新的，界面自己翻）
 *  和一串现成的话（老的 / FastAPI 自己抛的校验错误）。 */
export function toApiError(body: unknown, status: number, fallback: string): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string") {
    const { code, params } = detail as { code: string; params?: Record<string, unknown> };
    return new ApiError(code, status, code, params);
  }
  return new ApiError(typeof detail === "string" ? detail : fallback, status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw toApiError(body, res.status, res.statusText);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

type QsValue = string | number | boolean | undefined | null | (string | number)[];

/** 数组会展开成重复参数（`board=a&board=b`）—— FastAPI 的 `list[str]` 就是这么收的。
 *  空数组等于没传，不会挂一个空的 `board=` 上去。 */
export function qs(params: Record<string, QsValue>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item));
    } else {
      search.append(key, String(value));
    }
  }
  const out = search.toString();
  return out ? `?${out}` : "";
}
