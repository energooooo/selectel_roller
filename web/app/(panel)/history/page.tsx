"use client"

import {useState} from "react"
import {mutate} from "swr"
import {toast} from "sonner"
import {ArrowsClockwise, Copy, DownloadSimple, Target} from "@phosphor-icons/react"
import {Badge, CHECK_STATUS} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, Empty, Input, Mono, PageHeader, Select, Skeleton} from "@/components/ui/primitives"
import {post, useApi} from "@/lib/api"
import {fmtDateTime, fmtNum} from "@/lib/format"
import type {Caught, Key} from "@/lib/types"

export default function HistoryPage() {
    const [keyId, setKeyId] = useState("")
    const [region, setRegion] = useState("")
    const [days, setDays] = useState("")
    const [q, setQ] = useState("")
    const params = new URLSearchParams()
    if (keyId) params.set("key_id", keyId)
    if (region) params.set("region", region)
    if (days) params.set("days", days)
    if (q.trim()) params.set("q", q.trim())
    const path = `/caught${params.size ? `?${params}` : ""}`
    const {data} = useApi<Caught[]>(path)
    const {data: keys} = useApi<Key[]>("/keys")

    const recheck = async (c: Caught) => {
        const res = await post<{ check_status: string }>(`/caught/${c.id}/check`)
        toast(CHECK_STATUS[res.check_status]?.label ?? res.check_status)
        mutate(path)
    }

    return (
        <>
            <PageHeader title="Найденные IP" description="Все адреса, которые нашла панель, за всё время — даже если ключ уже удалён."
                        action={<Button variant="outline" size="sm" asChild><a href="/api/caught.csv"><DownloadSimple/> Скачать CSV</a></Button>}/>
            <Card>
                <div className="grid grid-cols-1 gap-3 px-5 pt-5 sm:flex sm:flex-wrap sm:px-6">
                    <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти IP или подсеть" aria-label="Поиск" className="sm:max-w-56"/>
                    <Select value={keyId} onChange={(e) => setKeyId(e.target.value)} className="sm:max-w-56" aria-label="Ключ">
                        <option value="">Все ключи</option>
                        {keys?.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
                    </Select>
                    <Select value={region} onChange={(e) => setRegion(e.target.value)} className="sm:max-w-40" aria-label="Регион">
                        <option value="">Все регионы</option>
                        {["ru-1", "ru-2", "ru-3", "ru-9"].map((r) => <option key={r}>{r}</option>)}
                    </Select>
                    <Select value={days} onChange={(e) => setDays(e.target.value)} className="sm:max-w-44" aria-label="Период">
                        <option value="">За всё время</option>
                        <option value="1">За сутки</option>
                        <option value="7">За неделю</option>
                        <option value="30">За месяц</option>
                    </Select>
                    {data && <span className="self-center text-[14px] text-muted-foreground sm:ml-auto">Показано: {data.length}</span>}
                </div>
                <div className="mt-4 overflow-x-auto">
                    <table className="w-full min-w-[820px] text-[13.5px]">
                        <thead>
                        <tr className="border-y border-border text-left">
                            {["IP", "Когда найден", "Ключ", "Регион и подсеть", "Запросов до находки", "Сейчас", "Проверка", ""].map((h) => (
                                <th key={h} className="px-4 py-3 text-left text-[13px] font-medium text-muted-foreground first:pl-5 sm:first:pl-6">{h}</th>
                            ))}
                        </tr>
                        </thead>
                        <tbody>
                        {!data ? (
                            <tr><td colSpan={8} className="p-5"><Skeleton className="h-32"/></td></tr>
                        ) : data.length ? data.map((c) => (
                            <tr key={c.id} className="border-b border-border/60 last:border-0 hover:bg-card-raised/40">
                                <td className="py-3 pl-5 pr-4 sm:pl-6">
                                    <span className="inline-flex items-center gap-2">
                                        <Mono className="text-[13px] text-foreground">{c.ip}</Mono>
                                        <button aria-label={`Скопировать ${c.ip}`} title="Скопировать" className="grid size-8 place-items-center rounded-lg text-subtle hover:bg-card-raised hover:text-amber"
                                                onClick={() => navigator.clipboard.writeText(c.ip).then(() => toast.success("Скопировано"))}><Copy/></button>
                                    </span>
                                    {c.note && <p className="text-[11.5px] text-subtle">{c.note}</p>}
                                </td>
                                <td className="px-4 font-mono text-[12.5px] text-muted-foreground">{fmtDateTime(c.caught_at)}</td>
                                <td className="px-4">{c.key_name ?? <span className="text-subtle">{c.account_id}</span>}</td>
                                <td className="px-4 font-mono text-[12.5px] text-muted-foreground">{c.region} · {c.pool_cidr || "—"}</td>
                                <td className="px-4 font-mono tabular text-muted-foreground">{fmtNum(c.attempts_spent)}</td>
                                <td className="px-4">{c.on_account ? <Badge tone="ok">На аккаунте</Badge> : <Badge tone="muted">Удалён</Badge>}</td>
                                <td className="px-4"><Badge tone={CHECK_STATUS[c.check_status]?.tone}>{CHECK_STATUS[c.check_status]?.label}</Badge></td>
                                <td className="px-4 text-right">
                                    <Button variant="ghost" size="sm" onClick={() => recheck(c)} aria-label={`Проверить ${c.ip} ещё раз`}><ArrowsClockwise/> Проверить</Button>
                                </td>
                            </tr>
                        )) : (
                            <tr><td colSpan={8}><Empty icon={<Target weight="duotone" className="size-6"/>} title="Пока ничего не найдено">Как только ключ найдёт подходящий адрес, он появится здесь и придёт в Telegram.</Empty></td></tr>
                        )}
                        </tbody>
                    </table>
                </div>
            </Card>
        </>
    )
}
