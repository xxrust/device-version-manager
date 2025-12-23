export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, { ...init, credentials: "include" });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  const data = await res.json();
  if (!res.ok) {
    const err = (data && typeof data === "object" && "error" in data ? (data as any).error : null) || res.statusText;
    throw new Error(String(err));
  }
  return data as T;
}

export function jsonBody(body: unknown): RequestInit {
  return {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

