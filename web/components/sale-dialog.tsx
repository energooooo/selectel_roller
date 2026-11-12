"use client"

import {useEffect, useState} from "react"
import {toast} from "sonner"
import {mutate} from "swr"
import {Button} from "@/components/ui/button"
import {Dialog, Field, Input, Select} from "@/components/ui/primitives"
import {ApiError, patch, post, useApi} from "@/lib/api"
import type {Caught, Sale, SaleKind} from "@/lib/types"

export const SALE_KINDS: [SaleKind, string][] = [
    ["ip", "Продажа IP"],
    ["rent", "Аренда IP"],
    ["vds", "Сервер"],
    ["service", "Услуга"],
    ["other", "Другое"],
]
export const SALE_LABEL = Object.fromEntries(SALE_KINDS) as Record<SaleKind, string>

/** Дата-время в поле datetime-local по Москве. */
function toLocalInput(iso?: string | null): string {
    const d = iso ? new Date(iso) : new Date()
    const msk = new Date(d.getTime() + 3 * 3600_000)
    return msk.toISOString().slice(0, 16)
}

function fromLocalInput(v: string): string {
    return new Date(`${v}:00+03:00`).toISOString()
}

/** Запись в журнал доходов: новая (sale = null) или правка. caught — привязать к адресу сразу. */
export function SaleDialog({open, onOpenChange, sale, caught}: {
    open: boolean
    onOpenChange: (v: boolean) => void
    sale?: Sale | null
    caught?: Caught | null
}) {
    const {data: ips} = useApi<Caught[]>(open && !caught ? "/caught" : null)
    const [kind, setKind] = useState<SaleKind>("ip")
    const [amount, setAmount] = useState("")
    const [buyer, setBuyer] = useState("")
    const [caughtId, setCaughtId] = useState<string>("")
    const [ts, setTs] = useState(toLocalInput())
    const [note, setNote] = useState("")
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        if (!open) return
        setKind(sale?.kind ?? (caught?.client ? "rent" : "ip"))
        setAmount(sale ? String(sale.amount) : "")
        setBuyer(sale?.buyer ?? caught?.client ?? "")
        setCaughtId(String(sale?.caught_id ?? caught?.id ?? ""))
        setTs(toLocalInput(sale?.ts))
        setNote(sale?.note ?? "")
    }, [open, sale, caught])

    const value = Number(amount.replace(",", ".").replace(/\s/g, ""))
    const valid = value > 0 && Boolean(ts)

    const save = async () => {
        setBusy(true)
        try {
            const body = {
                kind, amount: value, buyer, note, ts: fromLocalInput(ts),
                caught_id: caughtId ? Number(caughtId) : null,
            }
            if (sale) await patch(`/sales/${sale.id}`, body)
            else await post("/sales", body)
            toast.success(sale ? "Запись обновлена" : `Доход ${value.toLocaleString("ru")} ₽ записан`)
            mutate((k) => typeof k === "string" && (k.startsWith("/sales") || k.startsWith("/finance") || k.startsWith("/caught")))
            onOpenChange(false)
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        } finally {
            setBusy(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange} title={sale ? "Изменить запись" : "Записать доход"}
                description={caught ? <>Адрес <span className="font-mono text-foreground">{caught.ip}</span></> : "Продажа, аренда адреса или услуга — всё, за что пришли деньги."}>
            <form className="space-y-4" onSubmit={(e) => {
                e.preventDefault()
                if (valid) save()
            }}>
                <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Что продано">
                        <Select value={kind} onChange={(e) => setKind(e.target.value as SaleKind)}>
                            {SALE_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </Select>
                    </Field>
                    <Field label="Сумма, ₽">
                        <Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="1500" className="font-mono" autoFocus/>
                    </Field>
                </div>
                <Field label="Покупатель" hint="Имя, ник в Telegram — как вам удобнее искать.">
                    <Input value={buyer} onChange={(e) => setBuyer(e.target.value)} maxLength={120} placeholder="@buyer"/>
                </Field>
                {!caught && (
                    <Field label="Адрес" hint="Необязательно. С привязкой доход виден в карточке адреса.">
                        <Select value={caughtId} onChange={(e) => setCaughtId(e.target.value)}>
                            <option value="">Без адреса</option>
                            {(ips ?? []).map((c) => <option key={c.id} value={c.id}>{c.ip} · {c.region}{c.on_account ? "" : " · удалён"}</option>)}
                        </Select>
                    </Field>
                )}
                <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Когда получены деньги">
                        <Input type="datetime-local" value={ts} onChange={(e) => setTs(e.target.value)}/>
                    </Field>
                    <Field label="Заметка">
                        <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Оплата за декабрь"/>
                    </Field>
                </div>
                <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
                    <Button type="submit" disabled={!valid || busy}>{busy ? "…" : sale ? "Сохранить запись" : "Записать доход"}</Button>
                </div>
            </form>
        </Dialog>
    )
}
