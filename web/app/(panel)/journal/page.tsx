"use client"

import {useEffect, useState} from "react"
import {ListBullets} from "@phosphor-icons/react"
import {EventRow} from "@/components/journal"
import {Button} from "@/components/ui/button"
import {Card, Empty, PageHeader, Segmented, Select, Skeleton} from "@/components/ui/primitives"
import {api, useApi} from "@/lib/api"
import {useStream} from "@/lib/stream"
import type {EventItem, Key} from "@/lib/types"
import {cn} from "@/lib/utils"

const LEVELS = [
    ["", "Все"],
    ["success,warn,error", "Только важное"],
    ["error", "Ошибки"],
] as const

export default function JournalPage() {
    const [level, setLevel] = useState("")
    const [keyId, setKeyId] = useState("")
    const [caughtOnly, setCaughtOnly] = useState(false)
    const [older, setOlder] = useState<EventItem[]>([])
    const [end, setEnd] = useState(false)
    const params = new URLSearchParams({limit: "100"})
    if (level) params.set("level", level)
    if (keyId) params.set("key_id", keyId)
    if (caughtOnly) params.set("kind", "caught")
    const {data} = useApi<EventItem[]>(`/events?${params}`)
    const {data: keys} = useApi<Key[]>("/keys")
    const {events: live} = useStream()

    useEffect(() => {
        setOlder([])
        setEnd(false)
    }, [level, keyId, caughtOnly])

    const matches = (e: EventItem) =>
        (!level || level.split(",").includes(e.level)) && (!keyId || String(e.key_id) === keyId) && (!caughtOnly || e.kind === "caught")
    const seen = new Set<number>()
    const all = [...live.filter(matches), ...(data ?? []), ...older].filter((e) => {
        if (e.id == null) return true
        if (seen.has(e.id)) return false
        seen.add(e.id)
        return true
    })

    const loadMore = async () => {
        const last = all.filter((e) => e.id != null).at(-1)
        if (!last?.id) return
        const p = new URLSearchParams(params)
        p.set("before_id", String(last.id))
        const more = await api<EventItem[]>(`/events?${p}`)
        setOlder((o) => [...o, ...more])
        if (more.length < 100) setEnd(true)
    }

    return (
        <>
            <PageHeader title="Журнал" description="Что делала панель и когда: запуски, ошибки, смены прокси, найденные адреса. Новые записи появляются сверху сами."/>
            <Card>
                <div className="flex flex-wrap items-center gap-3 px-5 pt-5 sm:px-6">
                    <Segmented value={caughtOnly ? "caught" : level || "all"} onChange={(v: string) => {
                        setCaughtOnly(v === "caught")
                        setLevel(v === "all" || v === "caught" ? "" : v)
                    }} options={[["all", "Все"], ["success,warn,error", "Только важное"], ["error", "Ошибки"], ["caught", "Находки"]] as const}/>
                    <Select value={keyId} onChange={(e) => setKeyId(e.target.value)} className="sm:max-w-56" aria-label="Ключ">
                        <option value="">Все ключи</option>
                        {keys?.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
                    </Select>
                </div>
                <div className="px-5 py-4 sm:px-6">
                    {!data ? <Skeleton className="h-60"/> : all.length ? (
                        <>
                            {all.map((e, i) => <EventRow key={e.id ?? `l${i}`} e={e}/>)}
                            {!end && all.length >= 100 && (
                                <div className="mt-4 flex justify-center"><Button variant="outline" size="sm" onClick={loadMore}>Показать более ранние</Button></div>
                            )}
                        </>
                    ) : <Empty icon={<ListBullets weight="duotone" className="size-6"/>} title="Записей нет">Записи появятся, когда ключи начнут поиск.</Empty>}
                </div>
            </Card>
        </>
    )
}
