"use client"

import {useEffect, useRef, useState} from "react"
import {Button} from "@/components/ui/button"
import {Field, Input} from "@/components/ui/primitives"
import {api, ApiError} from "@/lib/api"

type MailReply = {cursor: string; code: string | null}

export function MailCode({email: initialEmail = ""}: {email?: string}) {
    const [email, setEmail] = useState(initialEmail)
    const [waiting, setWaiting] = useState(false)
    const [message, setMessage] = useState("")
    const [code, setCode] = useState("")
    const controller = useRef<AbortController | null>(null)
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

    function cancel() {
        controller.current?.abort()
        controller.current = null
        if (timer.current) clearTimeout(timer.current)
        timer.current = null
    }

    useEffect(() => cancel, [])

    async function start() {
        cancel()
        const request = new AbortController()
        controller.current = request
        setCode("")
        setWaiting(true)
        setMessage("Проверяем ящик… Пока не запрашивайте код в Selectel.")
        const address = email.trim()
        const deadline = Date.now() + 120_000
        async function poll(cursor?: string) {
            try {
                if (Date.now() >= deadline) throw new Error("Код не пришёл за 120 секунд. Начните ожидание заново и запросите новый код.")
                const result = await api<MailReply>("/keys/onboard/mail-code", {
                    method: "POST", json: {email: address, cursor}, signal: request.signal,
                })
                if (request.signal.aborted) return
                if (result.code) {
                    setCode(result.code)
                    setMessage("Введите этот код в панели Selectel, затем получите IAM-токен.")
                    setWaiting(false)
                    return
                }
                setMessage("Ожидание началось. Теперь запросите новый код в Selectel. Проверяем почту каждые 3 секунды.")
                timer.current = setTimeout(() => void poll(result.cursor), 3000)
            } catch (error) {
                if (request.signal.aborted) return
                setWaiting(false)
                setMessage(error instanceof ApiError || error instanceof Error ? error.message : "Не удалось проверить почту.")
            }
        }
        await poll()
    }

    return <div className="space-y-3 rounded-xl border border-border p-3">
        <Field label="Код входа из MailyGet" hint="Начните ожидание перед запросом кода в Selectel. Старые письма будут пропущены.">
            <Input aria-label="Email MailyGet" type="email" value={email} disabled={waiting}
                   placeholder="user@example.com" onChange={(event) => {setEmail(event.target.value); setCode(""); setMessage("")}}/>
        </Field>
        <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" disabled={waiting || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())}
                    onClick={() => void start()}>Ждать новый код</Button>
            {waiting && <Button type="button" size="sm" variant="ghost" onClick={() => {
                cancel(); setWaiting(false); setMessage("Ожидание отменено.")
            }}>Отменить</Button>}
            {code && <Input aria-label="Код Selectel" readOnly value={code} className="max-w-40 font-mono text-lg tracking-widest"
                            onFocus={(event) => event.target.select()}/>}
        </div>
        {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
    </div>
}
