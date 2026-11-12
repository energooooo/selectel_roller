"use client"

import {useCallback, useEffect, useState} from "react"
import {ArrowClockwise, ArrowLeft, Check, Copy} from "@phosphor-icons/react"
import {Button} from "@/components/ui/button"
import {ApiError, post} from "@/lib/api"
import {useStream} from "@/lib/stream"
import {cn} from "@/lib/utils"

type Pair = { code: string; expiresAt: number; ttl: number }

/** Кнопка «копировать»: значок сменяется галочкой на 1,6 с — без всплывающих тостов. */
export function CopyButton({value, label, className}: { value: string; label: string; className?: string }) {
    const [done, setDone] = useState(false)
    useEffect(() => {
        if (!done) return
        const t = setTimeout(() => setDone(false), 1600)
        return () => clearTimeout(t)
    }, [done])
    return (
        <button type="button" aria-label={done ? "Скопировано" : label} title={label}
                onClick={async () => {
                    try {
                        await navigator.clipboard.writeText(value)
                        setDone(true)
                    } catch {
                        /* буфер обмена недоступен — значение видно и выделяется вручную */
                    }
                }}
                className={cn(
                    "relative grid size-10 shrink-0 place-items-center rounded-xl border border-border-strong text-muted-foreground transition-[color,background-color,border-color,transform] duration-150 hover:-translate-y-px hover:border-subtle hover:bg-card-raised hover:text-foreground active:translate-y-px active:scale-[0.985]",
                    done && "border-ok/40 text-ok hover:text-ok",
                    className,
                )}>
            <Copy className={cn("absolute size-[18px] transition-[opacity,transform] duration-150", done && "scale-75 opacity-0")}/>
            <Check weight="bold" className={cn("absolute size-[18px] transition-[opacity,transform] duration-150", !done && "scale-75 opacity-0")}/>
        </button>
    )
}

function Countdown({left, ttl}: { left: number; ttl: number }) {
    const r = 9
    const length = 2 * Math.PI * r
    const share = ttl ? left / ttl : 0
    const seconds = Math.ceil(left / 1000)
    const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
    return (
        <span className={cn("inline-flex items-center gap-2 text-[13px] tabular", seconds <= 30 ? "text-warn" : "text-muted-foreground")}>
            <svg viewBox="0 0 24 24" className="size-5 -rotate-90" aria-hidden>
                <circle cx="12" cy="12" r={r} fill="none" stroke="currentColor" strokeOpacity=".2" strokeWidth="2.5"/>
                <circle cx="12" cy="12" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                        strokeDasharray={length} strokeDashoffset={length * (1 - share)}
                        className="transition-[stroke-dashoffset] duration-1000 ease-linear"/>
            </svg>
            <span>действует ещё {text}</span>
        </span>
    )
}

/** Код из 10 символов: две группы по пять, как его удобно читать вслух и вводить. */
function CodeCells({code, expired}: { code: string; expired: boolean }) {
    const chars = code.split("")
    const cell = "grid h-12 w-[28px] place-items-center rounded-lg border border-border-strong bg-input font-mono text-[19px] font-semibold text-foreground sm:h-14 sm:w-11 sm:rounded-xl sm:text-[24px] animate-rise"
    return (
        <div key={code} aria-label={`Код ${code.slice(0, 5)} ${code.slice(5)}`} role="img"
             className={cn("flex items-center gap-1 transition-opacity sm:gap-1.5", expired && "opacity-35")}>
            {chars.slice(0, 5).map((c, i) => (
                <span key={i} className={cell} style={{animationDelay: `${i * 35}ms`}}>{c}</span>
            ))}
            <span aria-hidden className="mx-0.5 h-0.5 w-2.5 rounded-full bg-subtle sm:mx-1.5 sm:w-3"/>
            {chars.slice(5).map((c, i) => (
                <span key={i + 5} className={cell} style={{animationDelay: `${(i + 5) * 35}ms`}}>{c}</span>
            ))}
        </div>
    )
}

const STEPS = [
    {
        title: "Установите расширение",
        text: <>В <span className="font-mono text-foreground/85">chrome://extensions</span> включите «Режим разработчика» и загрузите папку <span className="font-mono text-foreground/85">chrome-extension</span>.</>,
    },
    {
        title: "Откройте его",
        text: <>Значок Toffee Roller на панели браузера → «Подключить к панели».</>,
    },
    {
        title: "Введите адрес и код",
        text: <>Расширение проверит код и подключится. Код одноразовый.</>,
    },
]

export function ExtensionPairing({onBack}: { onBack: () => void }) {
    const {subscribe} = useStream()
    const [pair, setPair] = useState<Pair | null>(null)
    const [error, setError] = useState("")
    const [busy, setBusy] = useState(false)
    const [now, setNow] = useState(() => Date.now())
    const [origin, setOrigin] = useState("")
    const [connected, setConnected] = useState(false)

    const create = useCallback(async () => {
        setBusy(true)
        setError("")
        try {
            const {code, expires_in} = await post<{ code: string; expires_in: number }>("/extension/pair")
            setPair({code, expiresAt: Date.now() + expires_in * 1000, ttl: expires_in * 1000})
            setNow(Date.now())
        } catch (e) {
            setError(e instanceof ApiError ? e.message : "Не удалось создать код. Проверьте связь с сервером.")
        } finally {
            setBusy(false)
        }
    }, [])

    useEffect(() => {
        setOrigin(window.location.origin)
        void create()
    }, [create])

    useEffect(() => {
        const t = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(t)
    }, [])

    // Расширение обменяло код на токен — backend шлёт событие в живой поток.
    useEffect(() => subscribe((m) => {
        if (m.type === "extension") setConnected(true)
    }), [subscribe])

    const left = pair ? Math.max(0, pair.expiresAt - now) : 0
    const expired = !!pair && left === 0

    if (connected) {
        return (
            <div className="flex flex-col items-center px-2 py-8 text-center">
                <span className="grid size-16 place-items-center rounded-full border border-ok/35 bg-ok/10 text-ok animate-in zoom-in-75 fade-in-0 duration-300">
                    <Check weight="bold" className="size-7"/>
                </span>
                <p className="mt-5 text-[18px] font-semibold tracking-[-0.01em]">Расширение подключено</p>
                <p className="mt-1.5 max-w-sm text-[14px] leading-relaxed text-muted-foreground">
                    Теперь «Войти» у аккаунта подставит пароль в Selectel, а код из почты введётся сам.
                </p>
                <Button className="mt-6" onClick={onBack}>Перейти к аккаунтам</Button>
            </div>
        )
    }

    return (
        <div className="space-y-5">
            <button type="button" onClick={onBack}
                    className="group -ml-1 inline-flex h-10 items-center gap-1.5 rounded-lg px-1 text-[14px] text-muted-foreground transition-colors hover:text-foreground">
                <ArrowLeft className="size-4 transition-transform duration-150 group-hover:-translate-x-0.5"/> К аккаунтам
            </button>

            <div className="rounded-2xl border border-border-strong bg-card-raised/50 p-4 sm:p-5">
                <div className="flex min-h-10 items-center justify-between gap-2">
                    <span className="text-[13px] font-medium text-muted-foreground">Код сопряжения</span>
                    {pair && !expired && <CopyButton value={pair.code} label="Скопировать код"/>}
                    {expired && <span className="text-[13px] font-medium text-warn">Код истёк</span>}
                </div>

                <div className="mt-2 flex min-h-14 items-center">
                    {pair ? <CodeCells code={pair.code} expired={expired}/> : (
                        <div className="flex gap-1 sm:gap-1.5" aria-hidden>
                            {Array.from({length: 10}, (_, i) => (
                                <span key={i} className={cn("h-12 w-[28px] animate-pulse rounded-lg bg-muted/50 sm:h-14 sm:w-11 sm:rounded-xl", i === 5 && "ml-3.5 sm:ml-6")}/>
                            ))}
                        </div>
                    )}
                </div>
                {error && <p role="alert" className="mt-3 text-[14px] text-danger">{error}</p>}

                <div className="mt-4 flex items-center gap-3 border-t border-border pt-4">
                    <div className="min-w-0 flex-1">
                        <span className="text-[13px] font-medium text-muted-foreground">Адрес панели</span>
                        <p className="truncate font-mono text-[14px] text-foreground">{origin || "…"}</p>
                    </div>
                    {origin && <CopyButton value={origin} label="Скопировать адрес"/>}
                </div>

                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                    {expired ? (
                        <p className="text-[14px] text-muted-foreground">Код живёт 5 минут. Создайте новый — старый уже не сработает.</p>
                    ) : (
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                            <p role="status" className="flex items-center gap-2.5 text-[14px] text-muted-foreground">
                                <span className="relative flex size-2">
                                    <span className="absolute inset-0 rounded-full bg-amber animate-ping-soft"/>
                                    <span className="relative size-2 rounded-full bg-amber"/>
                                </span>
                                Ждём расширение
                            </p>
                            {pair && <Countdown left={left} ttl={pair.ttl}/>}
                        </div>
                    )}
                    <Button size="sm" variant={expired ? "brand" : "ghost"} disabled={busy} onClick={create}>
                        <ArrowClockwise className={cn("size-4", busy && "animate-spin")}/>
                        {expired ? "Создать новый код" : "Новый код"}
                    </Button>
                </div>
            </div>

            <ol className="grid gap-2 sm:grid-cols-3">
                {STEPS.map((step, i) => (
                    <li key={step.title} className="rounded-xl border border-border px-3.5 py-3">
                        <span className="font-mono text-[12px] text-amber">0{i + 1}</span>
                        <p className="mt-1 text-[14px] font-medium">{step.title}</p>
                        <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{step.text}</p>
                    </li>
                ))}
            </ol>
        </div>
    )
}
