"use client"

import {createContext, useContext, useEffect, useRef, useState} from "react"
import {mutate} from "swr"
import {toast} from "sonner"
import type {EventItem, Runtime} from "@/lib/types"

type Message = { type: string; data: Record<string, unknown> }
type Listener = (m: Message) => void

interface StreamState {
    connected: boolean
    runtimes: Record<number, Runtime>
    events: EventItem[]
    subscribe: (fn: Listener) => () => void
}

const StreamContext = createContext<StreamState | null>(null)

/** Какие данные перезапросить по событию из движка. */
const INVALIDATE: Record<string, string[]> = {
    key: ["/keys", "/overview"],
    keys_paused: ["/keys", "/overview"],
    key_deleted: ["/keys", "/overview"],
    account: ["/accounts"],
    extension: ["/extension/tokens"],
    proxy: ["/proxies", "/overview"],
    proxy_deleted: ["/proxies"],
    subnets: ["/subnets", "/overview"],
    caught: ["/overview", "/keys"],
    caught_update: [],
    caught_check: [],
    settings: ["/settings"],
}

/**
 * Один EventSource на всё приложение: журнал, состояние воркеров, поимки.
 * Обновления данных идут через SWR mutate — страницы обновляются сами.
 */
export function StreamProvider({children}: { children: React.ReactNode }) {
    const [connected, setConnected] = useState(false)
    const [runtimes, setRuntimes] = useState<Record<number, Runtime>>({})
    const [events, setEvents] = useState<EventItem[]>([])
    const listeners = useRef(new Set<Listener>())

    useEffect(() => {
        let source: EventSource | null = null
        let retry: ReturnType<typeof setTimeout> | undefined
        let closed = false
        const throttled = new Map<string, ReturnType<typeof setTimeout>>()
        // Итог входа аккаунта через расширение — одно уведомление на исход, без повторов.
        const lastOutcome = new Map<string, string>()

        const revalidate = (path: string) => {
            if (throttled.has(path)) return
            throttled.set(path, setTimeout(() => {
                throttled.delete(path)
                mutate((k) => typeof k === "string" && (k === path || k.startsWith(path + "?") || k.startsWith(path + "/")))
            }, 400))
        }

        const open = () => {
            source = new EventSource("/api/stream")
            source.onopen = () => setConnected(true)
            source.onerror = () => {
                setConnected(false)
                source?.close()
                if (!closed) retry = setTimeout(open, 3000)
            }
            source.onmessage = (e) => {
                let msg: Message
                try {
                    msg = JSON.parse(e.data)
                } catch {
                    return
                }
                if (msg.type === "runtime") {
                    const {key_id, ...rest} = msg.data as unknown as Runtime & { key_id: number }
                    setRuntimes((prev) => ({...prev, [key_id]: rest as Runtime}))
                } else if (msg.type === "event") {
                    setEvents((prev) => [msg.data as unknown as EventItem, ...prev].slice(0, 200))
                    revalidate("/events")
                } else if (msg.type === "caught") {
                    const d = msg.data as { ip: string; key_name: string; region: string }
                    toast.success(`Пойман ${d.ip}`, {description: `${d.key_name} · ${d.region}`, duration: 15000})
                    revalidate("/caught")
                } else if (msg.type === "account") {
                    const d = msg.data as { account_id: string; email: string; login_stage: string; login_message: string }
                    const outcome = d.login_stage === "done" || d.login_stage === "error" ? d.login_stage + d.login_message : ""
                    if (outcome && lastOutcome.get(d.account_id) !== outcome) {
                        const who = d.email || `Аккаунт ${d.account_id}`
                        if (d.login_stage === "done") {
                            toast.success(`${who} подключён`, {description: d.login_message || `№ ${d.account_id} — ключ в панели.`})
                        } else {
                            toast.error(`${who}: вход не удался`, {description: d.login_message, duration: 10000})
                        }
                    }
                    lastOutcome.set(d.account_id, outcome)
                } else if (msg.type === "extension") {
                    toast.success("Расширение подключено", {description: "Можно входить в аккаунты Selectel."})
                }
                for (const path of INVALIDATE[msg.type] ?? []) revalidate(path)
                if (msg.type.startsWith("caught")) revalidate("/caught")
                listeners.current.forEach((fn) => fn(msg))
            }
        }
        open()
        return () => {
            closed = true
            clearTimeout(retry)
            source?.close()
        }
    }, [])

    const subscribe = (fn: Listener) => {
        listeners.current.add(fn)
        return () => listeners.current.delete(fn)
    }

    return (
        <StreamContext.Provider value={{connected, runtimes, events, subscribe}}>
            {children}
        </StreamContext.Provider>
    )
}

export function useStream() {
    const ctx = useContext(StreamContext)
    if (!ctx) throw new Error("useStream вне StreamProvider")
    return ctx
}
