"use client"

import {useEffect, useState} from "react"
import {toast} from "sonner"
import {mutate} from "swr"
import {CalendarBlank, Copy, Desktop, Key as KeyIcon} from "@phosphor-icons/react"
import {Badge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Dialog, Field, Input, Mono, Note, Skeleton} from "@/components/ui/primitives"
import {ApiError, patch, post, useApi} from "@/lib/api"
import {fmtDate, fmtRub} from "@/lib/format"
import type {Caught, VdsQuote} from "@/lib/types"

const refreshAll = () => mutate((k) => typeof k === "string" && (k.startsWith("/caught") || k.startsWith("/finance") || k.startsWith("/vds")))

function errorText(e: unknown) {
    return e instanceof ApiError ? e.message : "Не получилось"
}

/** Сколько дней до срока: отрицательное — просрочен. */
export function daysUntil(iso: string | null): number | null {
    if (!iso) return null
    return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000)
}

/** Короткая сводка учёта под адресом: кому отдан, срок, доход, сервер. */
export function InventoryLine({c}: { c: Caught }) {
    const left = daysUntil(c.expires_at)
    const vds = c.vds
    if (!c.client && !c.tags.length && left == null && !c.income && !vds) return null
    return (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12.5px]">
            {c.client && <span className="text-muted-foreground">→ {c.client}</span>}
            {c.tags.map((t) => (
                <span key={t} className="rounded-md border border-border-strong bg-card-raised px-1.5 py-0.5 text-[11.5px] text-muted-foreground">{t}</span>
            ))}
            {left != null && (
                <Badge tone={left < 0 ? "danger" : left <= 3 ? "warn" : "muted"} className="h-6 px-2 text-[11.5px]"
                       title={`Срок: ${fmtDate(c.expires_at)}`}>
                    {left < 0 ? `срок прошёл ${fmtDate(c.expires_at)}` : left === 0 ? "срок сегодня" : `до ${fmtDate(c.expires_at)}`}
                </Badge>
            )}
            {c.income > 0 && <span className="font-mono text-ok">+{fmtRub(c.income)}</span>}
            {vds && (
                <Badge tone={vds.status === "active" ? "ok" : vds.status === "error" ? "danger" : "info"} live={vds.status === "creating" || vds.status === "deleting"}
                       className="h-6 px-2 text-[11.5px]" title={vds.error || vds.step}>
                    {vds.status === "active" ? `сервер ${vds.flavor}` : vds.status === "error" ? "сервер: ошибка" : vds.step || "сервер…"}
                </Badge>
            )}
        </div>
    )
}

export function InventoryDialog({c, onOpenChange}: { c: Caught; onOpenChange: (v: boolean) => void }) {
    const [client, setClient] = useState(c.client)
    const [tags, setTags] = useState(c.tags.join(", "))
    const [expires, setExpires] = useState(c.expires_at ? new Date(c.expires_at).toISOString().slice(0, 10) : "")
    const [note, setNote] = useState(c.note)
    const [busy, setBusy] = useState(false)
    const save = async () => {
        setBusy(true)
        try {
            await patch(`/caught/${c.id}`, {
                client, note,
                tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
                expires_at: expires ? new Date(`${expires}T23:59:00+03:00`).toISOString() : null,
            })
            toast.success(`${c.ip}: учёт сохранён`)
            refreshAll()
            onOpenChange(false)
        } catch (e) {
            toast.error(errorText(e))
        } finally {
            setBusy(false)
        }
    }
    return (
        <Dialog open onOpenChange={onOpenChange} title="Учёт адреса" description={<>Кому отдан <span className="font-mono text-foreground">{c.ip}</span> и до какого числа.</>}>
            <form className="space-y-4" onSubmit={(e) => {
                e.preventDefault()
                save()
            }}>
                <Field label="Кому отдан" hint="Покупатель или арендатор. Подставится в запись дохода.">
                    <Input value={client} onChange={(e) => setClient(e.target.value)} maxLength={120} placeholder="@buyer" autoFocus/>
                </Field>
                <Field label="Метки" hint="Через запятую: «аренда», «vpn», «резерв».">
                    <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="аренда, vpn"/>
                </Field>
                <Field label="Срок" help="За несколько дней до этой даты придёт напоминание в Telegram. Сколько дней — в настройках, раздел «Деньги».">
                    <div className="flex gap-2">
                        <Input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} className="flex-1"/>
                        {[30, 90].map((d) => (
                            <Button key={d} type="button" variant="outline" className="shrink-0" onClick={() => {
                                const base = expires ? new Date(`${expires}T12:00:00+03:00`) : new Date()
                                setExpires(new Date(base.getTime() + d * 86_400_000).toISOString().slice(0, 10))
                            }}>+{d} дн.</Button>
                        ))}
                    </div>
                </Field>
                <Field label="Заметка">
                    <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200}/>
                </Field>
                <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
                    {expires && <Button type="button" variant="ghost" className="sm:mr-auto" onClick={() => setExpires("")}><CalendarBlank/> Убрать срок</Button>}
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
                    <Button type="submit" disabled={busy}>{busy ? "…" : "Сохранить учёт"}</Button>
                </div>
            </form>
        </Dialog>
    )
}

export function VdsCreateDialog({c, onOpenChange}: { c: Caught; onOpenChange: (v: boolean) => void }) {
    const {data: quote, error} = useApi<VdsQuote>(`/vds/quote?caught_id=${c.id}`, {revalidateOnFocus: false})
    const [typed, setTyped] = useState("")
    const [busy, setBusy] = useState(false)
    const create = async () => {
        setBusy(true)
        try {
            await post("/vds", {caught_id: c.id, confirm_ip: typed.trim()})
            toast.success("Сервер создаётся", {description: "Обычно 2–4 минуты. Ход виден под адресом."})
            refreshAll()
            onOpenChange(false)
        } catch (e) {
            toast.error(errorText(e))
        } finally {
            setBusy(false)
        }
    }
    const rows: [string, number | null][] = quote ? [
        [`Сервер ${quote.flavor}${quote.zone ? `, ${quote.zone}` : ""}`, quote.server_month],
        [`Диск ${quote.disk_gb} ГБ`, quote.disk_month],
        ["Роутер — один на проект, если его ещё нет", quote.router_month],
    ] : []
    return (
        <Dialog open onOpenChange={onOpenChange} title="Поднять сервер под IP"
                description={<>Самый дешёвый сервер в {c.region} на том же аккаунте, адрес <span className="font-mono text-foreground">{c.ip}</span> привяжется к нему. Деньги спишутся с баланса аккаунта.</>}>
            {error ? <Note tone="danger">{error.message}</Note> : !quote ? <Skeleton className="h-28"/> : (
                <div className="rounded-xl border border-border bg-card-raised/50 px-4 py-3">
                    <ul className="space-y-1.5 text-[14px]">
                        {rows.map(([label, value]) => (
                            <li key={label} className="flex items-baseline justify-between gap-3">
                                <span className="text-muted-foreground">{label}</span>
                                <Mono className="shrink-0 text-foreground">{value == null ? "—" : `${fmtRub(value)}/мес`}</Mono>
                            </li>
                        ))}
                    </ul>
                    <div className="mt-3 flex items-baseline justify-between border-t border-border/60 pt-3 text-[15px]">
                        <span>Итого</span>
                        <span className="text-right">
                            <Mono className="text-foreground">{fmtRub(quote.total_month)}/мес</Mono>
                            <span className="block text-[12.5px] text-muted-foreground">с новым роутером {fmtRub(quote.total_with_router)}</span>
                        </span>
                    </div>
                </div>
            )}
            <ul className="mt-4 space-y-1 text-[13.5px] leading-relaxed text-muted-foreground">
                <li>· Ubuntu, вход root по паролю — пароль покажем здесь же, когда сервер запустится.</li>
                <li>· Если самый дешёвый вариант распродан, панель возьмёт следующий по цене.</li>
                <li>· Сам IP панель не удаляет: при ошибке или удалении сервера он остаётся на аккаунте.</li>
            </ul>
            <div className="mt-5">
                <p className="mb-2 text-[14px] text-muted-foreground">Чтобы подтвердить, введите <span className="font-mono text-foreground">{c.ip}</span></p>
                <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="font-mono" autoFocus/>
            </div>
            <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
                <Button disabled={typed.trim() !== c.ip || busy || (quote && !quote.available)} onClick={create}>
                    <Desktop/> {busy ? "…" : "Поднять сервер"}
                </Button>
            </div>
        </Dialog>
    )
}

export function VdsAccessDialog({c, onOpenChange}: { c: Caught; onOpenChange: (v: boolean) => void }) {
    const [creds, setCreds] = useState<{ user: string; password: string; ip: string } | null>(null)
    const [err, setErr] = useState("")
    useEffect(() => {
        if (!c.vds) return
        post<{ user: string; password: string; ip: string }>(`/vds/${c.vds.id}/password`).then(setCreds).catch((e) => setErr(errorText(e)))
    }, [c.vds])
    const copy = (v: string, what: string) => navigator.clipboard.writeText(v).then(() => toast.success(`${what} скопирован`))
    return (
        <Dialog open onOpenChange={onOpenChange} title="Доступ к серверу" description={`${c.vds?.flavor ?? ""} · ${c.vds?.zone ?? c.region}${c.vds?.month_cost ? ` · ${fmtRub(c.vds.month_cost)}/мес` : ""}`}>
            {err ? <Note tone="danger">{err}</Note> : !creds ? <Skeleton className="h-24"/> : (
                <div className="space-y-2">
                    {([["Команда", `ssh ${creds.user}@${creds.ip}`], ["Пароль", creds.password]] as const).map(([label, value]) => (
                        <div key={label} className="flex items-center gap-2 rounded-xl border border-border bg-card-raised/50 px-3.5 py-2.5">
                            <span className="w-20 shrink-0 text-[13px] text-muted-foreground">{label}</span>
                            <Mono className="min-w-0 flex-1 truncate text-[14px] text-foreground">{value}</Mono>
                            <button onClick={() => copy(value, label)} aria-label={`Скопировать: ${label}`}
                                    className="grid size-10 place-items-center rounded-lg text-subtle transition-colors duration-150 hover:bg-card hover:text-amber">
                                <Copy className="size-[18px]"/>
                            </button>
                        </div>
                    ))}
                    <p className="flex items-center gap-2 pt-1 text-[13px] text-muted-foreground"><KeyIcon className="size-4"/>Смените пароль после первого входа.</p>
                </div>
            )}
        </Dialog>
    )
}
