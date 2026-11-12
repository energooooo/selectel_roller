"use client"

import useSWR, {type SWRConfiguration} from "swr"

export class ApiError extends Error {
    constructor(public status: number, message: string, public data?: unknown) {
        super(message)
    }
}

function detailText(data: unknown, fallback: string): string {
    if (data && typeof data === "object" && "detail" in data) {
        const detail = (data as { detail: unknown }).detail
        if (typeof detail === "string") return detail
        // Ошибки валидации FastAPI: [{loc, msg}]
        if (Array.isArray(detail)) {
            return detail.map((d) => (typeof d?.msg === "string" ? d.msg : JSON.stringify(d))).join("; ")
        }
    }
    return fallback
}

/** Запрос к backend. Изменяющие запросы несут CSRF-заголовок; 401 → на экран входа. */
export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
    const {json, headers, ...rest} = init
    const res = await fetch(`/api${path}`, {
        credentials: "same-origin",
        cache: "no-store",
        ...rest,
        headers: {
            "X-Requested-With": "toffee-roller",
            ...(json !== undefined ? {"Content-Type": "application/json"} : {}),
            ...headers,
        },
        body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
    const text = await res.text()
    let data: unknown = undefined
    try {
        data = text ? JSON.parse(text) : undefined
    } catch {
        data = text
    }
    if (!res.ok) {
        if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
            window.location.href = "/login"
        }
        throw new ApiError(res.status, detailText(data, `Ошибка ${res.status}`), data)
    }
    return data as T
}

export const post = <T = unknown>(path: string, json?: unknown) => api<T>(path, {method: "POST", json: json ?? {}})
export const patch = <T = unknown>(path: string, json: unknown) => api<T>(path, {method: "PATCH", json})
export const put = <T = unknown>(path: string, json: unknown) => api<T>(path, {method: "PUT", json})
export const del = <T = unknown>(path: string) => api<T>(path, {method: "DELETE"})

export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
    return useSWR<T>(path, (p: string) => api<T>(p), {revalidateOnFocus: true, ...config})
}
