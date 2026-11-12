"use client"

import {useEffect, useState} from "react"
import {mutate} from "swr"
import {toast} from "sonner"
import {ArrowsClockwise, Globe, Plus, Trash} from "@phosphor-icons/react"
import {ConfirmDialog} from "@/components/confirm"
import {Badge, PROXY_STATUS} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, CardHeader, Empty, Field, Hint, Mono, PageHeader, Select, Skeleton, Textarea, Tip} from "@/components/ui/primitives"
import {ApiError, del, post, useApi} from "@/lib/api"
import {fmtAgo, plural, proxyName} from "@/lib/format"
import type {ProxyItem} from "@/lib/types"
import {cn} from "@/lib/utils"

const REGIONS = ["ru-1", "ru-2", "ru-3", "ru-9"]

function RegionChips({p}: { p: ProxyItem }) {
    return (
        <div className="flex flex-wrap gap-1.5">
            {REGIONS.map((r) => {
                const ok = p.regions_ok.includes(r), bad = p.regions_failed.includes(r)
                return (
                    <span key={r} title={ok ? `${r}: Selectel доступен` : bad ? `${r}: Selectel недоступен через этот прокси` : `${r}: не проверялся`}
                          className={cn("inline-flex h-7 items-center gap-1.5 rounded-lg border px-2 font-mono text-[12px]",
                              ok ? "border-ok/30 text-ok" : bad ? "border-danger/30 text-danger" : "border-border text-subtle")}>
                        <span className={cn("size-1.5 rounded-full", ok ? "bg-ok" : bad ? "bg-danger" : "bg-subtle")}/>
                        {r}
                    </span>
                )
            })}
        </div>
    )
}

function ProxyRow({p}: { p: ProxyItem }) {
    const [checking, setChecking] = useState(false)
    const [confirm, setConfirm] = useState(false)
    const s = PROXY_STATUS[p.status]
    const check = async () => {
        setChecking(true)
        try {
            await post(`/proxies/${p.id}/check`)
            mutate("/proxies")
        } finally {
            setChecking(false)
        }
    }
    return (
        <div className="flex flex-col gap-3.5 border-b border-border/70 px-5 py-4 last:border-0 sm:px-6">
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2.5">
                    <span className="truncate text-[15.5px] font-medium">{proxyName(p)}</span>
                    <Badge tone={s.tone} title={s.about}>{checking ? "Проверяется" : s.label}</Badge>
                    {p.session_mode === "password_suffix" && <Badge tone="info" title="Каждый ключ получает свой внешний IP через этот прокси">IP на каждый ключ</Badge>}
                </div>
                <p className="mt-1.5 text-[14px] text-muted-foreground">
                    <Mono className="text-subtle">{p.display}</Mono>{" · "}
                    {p.exit_ip ? <>Внешний IP <Mono className="text-foreground">{p.exit_ip}</Mono>{p.exit_country && ` (${p.exit_country})`}</> : "Внешний IP пока неизвестен"}
                    {p.latency_ms != null && <> · отвечает за {p.latency_ms} мс</>}
                    {" · "}{p.keys ? `${p.keys} ${plural(p.keys, "ключ", "ключа", "ключей")}` : "не используется"}
                </p>
                {p.last_error && <p className="mt-1 truncate text-[13px] text-danger/85" title={p.last_error}>Ошибка: {p.last_error}</p>}
                {p.checked_at && <p className="mt-1 text-[12.5px] text-subtle">Проверен {fmtAgo(p.checked_at)}</p>}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <RegionChips p={p}/>
                <div className="flex gap-2">
                <Tip label="Проверить сейчас">
                    <Button variant="outline" size="icon" aria-label="Проверить сейчас" onClick={check} disabled={checking}>
                        <ArrowsClockwise className={checking ? "animate-spin" : ""}/>
                    </Button>
                </Tip>
                <Tip label="Удалить прокси">
                    <Button variant="outline" size="icon" aria-label="Удалить прокси" className="hover:border-danger/50 hover:text-danger" onClick={() => setConfirm(true)}>
                        <Trash/>
                    </Button>
                </Tip>
                </div>
            </div>
            <ConfirmDialog open={confirm} onOpenChange={setConfirm} danger confirmLabel="Удалить прокси"
                           title="Удалить прокси?"
                           description={p.keys ? `Через него работают ключи (${p.keys}). Они сразу перейдут на другой рабочий прокси.` : "Ни один ключ его сейчас не использует."}
                           onConfirm={async () => {
                               await del(`/proxies/${p.id}`)
                               toast.success("Прокси удалён")
                               mutate("/proxies")
                           }}/>
        </div>
    )
}

export default function ProxiesPage() {
    const {data} = useApi<ProxyItem[]>("/proxies", {refreshInterval: 30_000})
    const [text, setText] = useState("")
    const [kind, setKind] = useState("http")
    const [sessions, setSessions] = useState("none")
    const [busy, setBusy] = useState(false)
    const [errors, setErrors] = useState<{ line: number; error: string }[]>([])

    useEffect(() => {
        if (new URLSearchParams(window.location.search).get("add")) document.getElementById("proxy-input")?.focus()
    }, [])

    const add = async () => {
        setBusy(true)
        try {
            const res = await post<{ added: number; duplicates: number; errors: { line: number; error: string }[] }>(
                "/proxies", {text, default_kind: kind, session_mode: sessions})
            setErrors(res.errors)
            const extra = [res.duplicates && `уже были: ${res.duplicates}`, res.errors.length && `с ошибкой: ${res.errors.length}`].filter(Boolean).join(", ")
            toast.success(`Добавлено прокси: ${res.added}`, {description: extra || "Проверка уже идёт, результат появится в списке"})
            if (!res.errors.length) setText("")
            mutate("/proxies")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        } finally {
            setBusy(false)
        }
    }

    const counts = {ok: 0, partial: 0, dead: 0, unchecked: 0}
    data?.forEach((p) => counts[p.status]++)

    return (
        <>
            <PageHeader title="Прокси"
                        description="Через прокси ключи ходят в Selectel, и у каждого аккаунта свой внешний IP. Так Selectel не заблокирует все аккаунты разом."
                        action={<Button variant="outline" size="sm" onClick={async () => {
                            await post("/proxies/check-all")
                            toast("Проверяю все прокси — статусы обновятся сами")
                        }}><ArrowsClockwise/> Проверить все</Button>}/>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
                <Card>
                    <CardHeader title="Список прокси"
                                hint={data?.length ? `Работают: ${counts.ok} · частично: ${counts.partial} · не работают: ${counts.dead}${counts.unchecked ? ` · проверяются: ${counts.unchecked}` : ""}` : undefined}
                                action={data?.length ? (
                                    <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                                        Значки регионов
                                        <Hint>Зелёный — Selectel в этом регионе доступен через прокси. Красный — нет, ключ будет искать только в остальных регионах.</Hint>
                                    </span>
                                ) : undefined}/>
                    <div className="mt-4 border-t border-border">
                        {!data ? (
                            <div className="space-y-2 p-5">{Array.from({length: 4}, (_, i) => <Skeleton key={i} className="h-16"/>)}</div>
                        ) : data.length ? data.map((p) => <ProxyRow key={p.id} p={p}/>) : (
                            <Empty icon={<Globe weight="duotone" className="size-6"/>} title="Прокси пока нет">
                                Без прокси все ключи обращаются к Selectel с адреса сервера, и его быстро блокируют. Добавьте хотя бы по одному прокси на ключ.
                            </Empty>
                        )}
                    </div>
                </Card>
                <Card className="h-fit p-5 sm:p-6 xl:sticky xl:top-24">
                    <h2 className="text-[16px] font-semibold">Добавить прокси</h2>
                    <p className="mt-1.5 text-[14px] leading-relaxed text-muted-foreground">
                        По одному в строке. Подходят форматы <Mono>хост:порт</Mono>, <Mono>логин:пароль@хост:порт</Mono>, <Mono>хост:порт:логин:пароль</Mono> и ссылки <Mono>socks5://…</Mono>. Через пробел после адреса можно дописать название.
                    </p>
                    <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                        <Field label="Тип" help="Используется, если в строке не указано http:// или socks5://.">
                            <Select value={kind} onChange={(e) => setKind(e.target.value)}>
                                <option value="http">HTTP</option>
                                <option value="socks5">SOCKS5</option>
                            </Select>
                        </Field>
                        <Field label="Внешний IP" help="Для мобильных прокси, которые меняют IP по метке в пароле (например, proxxxymiron): панель выдаст каждому ключу свой постоянный внешний IP.">
                            <Select value={sessions} onChange={(e) => setSessions(e.target.value)}>
                                <option value="none">Как в прокси</option>
                                <option value="password_suffix">Свой на каждый ключ</option>
                            </Select>
                        </Field>
                    </div>
                    <Field label="Адреса прокси" className="mt-4">
                        <Textarea id="proxy-input" value={text} onChange={(e) => setText(e.target.value)} className="min-h-40"
                                  placeholder={"login:pass@1.2.3.4:8000 Мобильный-1\nsocks5://login:pass@host:1080"}/>
                    </Field>
                    {errors.length > 0 && (
                        <ul className="mt-2 space-y-0.5 text-[13.5px] text-danger">
                            {errors.map((e) => <li key={e.line}>Строка {e.line}: {e.error}</li>)}
                        </ul>
                    )}
                    <Button className="mt-4 w-full" onClick={add} disabled={busy || !text.trim()}><Plus weight="bold"/> Добавить и проверить</Button>
                    <p className="mt-3 text-[13px] leading-relaxed text-subtle">
                        Логины и пароли хранятся в зашифрованном виде. Каждый прокси сразу проверяется: внешний IP, страна, скорость ответа и доступность регионов Selectel.
                    </p>
                </Card>
            </div>
        </>
    )
}
