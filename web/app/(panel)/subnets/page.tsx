"use client"

import {useEffect, useMemo, useState} from "react"
import {mutate} from "swr"
import {toast} from "sonner"
import {Fire, MagnifyingGlass, Plus, Snowflake, Trash, TreeStructure} from "@phosphor-icons/react"
import {Badge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, Checkbox, Empty, Field, Hint, Input, Mono, PageHeader, Segmented, Skeleton, Switch, Textarea, Tip} from "@/components/ui/primitives"
import {ApiError, del, patch, post, useApi} from "@/lib/api"
import {fmtNum} from "@/lib/format"
import type {SubnetItem} from "@/lib/types"

function HeatBadge({s}: { s: SubnetItem }) {
    const x = s.heat.toLocaleString("ru", {maximumFractionDigits: 1})
    if (s.tier === "hot") {
        return <Badge tone="brand" title={`Адреса находятся в ${x} раза чаще среднего — проверяется первой`}><Fire weight="fill" className="-ml-1 size-3.5"/>Горячая ×{x}</Badge>
    }
    if (s.tier === "cold") {
        return <Badge tone="info" title={`Много попыток без находок (×${x} от среднего) — проверяется не каждый проход`}><Snowflake className="-ml-1 size-3.5"/>Холодная</Badge>
    }
    if (s.tier === "new") return <Badge tone="muted" title="Попыток пока мало, чтобы судить">Мало данных</Badge>
    return <Badge tone="muted" title={`×${x} от среднего`}>Обычная</Badge>
}

function Th({children, help, right}: { children?: React.ReactNode; help?: string; right?: boolean }) {
    return (
        <th className={`px-4 py-3 text-[13px] font-medium text-muted-foreground first:pl-5 sm:first:pl-6 ${right ? "text-right" : "text-left"}`}>
            <span className="inline-flex items-center gap-1.5">{children}{help && <Hint>{help}</Hint>}</span>
        </th>
    )
}

export default function SubnetsPage() {
    const {data} = useApi<SubnetItem[]>("/subnets")
    const [filter, setFilter] = useState<"active" | "off" | "all">("active")
    const [sort, setSort] = useState<"default" | "heat">("default")
    const [q, setQ] = useState("")
    const [selected, setSelected] = useState<Set<number>>(new Set())
    const [text, setText] = useState("")
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        if (new URLSearchParams(window.location.search).get("add")) document.getElementById("subnets-input")?.focus()
    }, [])

    const rows = useMemo(() => {
        const list = (data ?? []).filter((s) => {
            if (filter === "active" && !s.enabled) return false
            if (filter === "off" && s.enabled) return false
            return !q || s.cidr.includes(q.trim()) || s.region.includes(q.trim())
        })
        return sort === "heat" ? [...list].sort((a, b) => b.heat - a.heat) : list
    }, [data, filter, q, sort])

    const toggle = async (s: SubnetItem, enabled: boolean) => {
        await patch(`/subnets/${s.id}`, {enabled})
        toast.success(enabled ? `${s.cidr}: поиск включён` : `${s.cidr}: поиск выключен`)
        mutate("/subnets")
    }
    const bulkToggle = async (enabled: boolean) => {
        await post("/subnets/toggle", {ids: [...selected], enabled})
        toast.success(enabled ? `Включено подсетей: ${selected.size}` : `Выключено подсетей: ${selected.size}`)
        setSelected(new Set())
        mutate("/subnets")
    }
    const add = async () => {
        setBusy(true)
        try {
            const res = await post<{ added: number; updated: number; errors: { line: number; error: string }[] }>("/subnets", {text})
            toast.success(`Добавлено: ${res.added}, обновлено: ${res.updated}`, {
                description: res.errors.length ? res.errors.map((e) => `строка ${e.line}: ${e.error}`).join("; ") : "Работающие ключи уже ищут в них",
            })
            if (!res.errors.length) setText("")
            mutate("/subnets")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        } finally {
            setBusy(false)
        }
    }

    const counts = {active: data?.filter((s) => s.enabled).length ?? 0, off: data?.filter((s) => !s.enabled).length ?? 0}
    const allVisibleSelected = rows.length > 0 && rows.every((r) => selected.has(r.id))

    return (
        <>
            <PageHeader title="Подсети"
                        description="Диапазоны адресов, из которых вам нужен IP. Панель просит у Selectel адрес именно из включённых подсетей. Изменения подхватываются сразу."/>
            <Card>
                <div className="flex flex-wrap items-center gap-3 px-5 pt-5 sm:px-6">
                    <Segmented value={filter} onChange={setFilter}
                               options={[["active", `Включены · ${counts.active}`], ["off", `Выключены · ${counts.off}`], ["all", "Все"]]}/>
                    <div className="relative w-full sm:w-64">
                        <MagnifyingGlass className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle"/>
                        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти: 81.163 или ru-2" className="pl-10" aria-label="Поиск подсети"/>
                    </div>
                    <div className="sm:ml-auto">
                        <Segmented size="sm" value={sort} onChange={setSort} options={[["default", "По порядку"], ["heat", "Сначала горячие"]]}/>
                    </div>
                </div>
                {selected.size > 0 && (
                    <div className="mx-5 mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-amber/30 bg-amber/[0.06] px-4 py-2.5 sm:mx-6">
                        <span className="text-[14px]">Выбрано: {selected.size}</span>
                        <div className="ml-auto flex gap-2">
                            <Button size="sm" variant="outline" onClick={() => bulkToggle(true)}>Включить поиск</Button>
                            <Button size="sm" variant="outline" onClick={() => bulkToggle(false)}>Выключить поиск</Button>
                            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Снять выбор</Button>
                        </div>
                    </div>
                )}
                <div className="mt-4 overflow-x-auto">
                    <table className="w-full min-w-[860px] text-[14.5px]">
                        <thead>
                        <tr className="border-y border-border">
                            <th className="w-12 py-3 pl-5 sm:pl-6">
                                <Checkbox checked={allVisibleSelected} label="Выбрать все" onChange={(v) => {
                                    const next = new Set(selected)
                                    rows.forEach((r) => (v ? next.add(r.id) : next.delete(r.id)))
                                    setSelected(next)
                                }}/>
                            </th>
                            <Th>Подсеть</Th>
                            <Th help="Облачный регион Selectel, где выдаются адреса из этой подсети. Определяется сам.">Регион</Th>
                            <Th help="Сколько адресов из подсети вам подходят. Заполняется вручную, для справки — на поиск не влияет. Нажмите на число, чтобы изменить.">Подходящих адресов</Th>
                            <Th right help="Сколько адресов панель нашла в подсети и за сколько ответов Selectel.">Найдено</Th>
                            <Th help="Насколько часто здесь находятся адреса по сравнению со средним. Горячие проверяются первыми, холодные — не каждый проход. Обновляется само по мере поиска.">Рейтинг</Th>
                            <Th help="Выдаёт ли Selectel адреса из этой подсети. Если нет — искать в ней бесполезно.">Selectel</Th>
                            <Th>Поиск</Th>
                            <Th/>
                        </tr>
                        </thead>
                        <tbody>
                        {!data ? (
                            <tr><td colSpan={9} className="p-5"><Skeleton className="h-48"/></td></tr>
                        ) : rows.length ? rows.map((s) => (
                            <tr key={s.id} className="border-b border-border/60 transition-colors last:border-0 hover:bg-card-raised/40">
                                <td className="py-3 pl-5 sm:pl-6">
                                    <Checkbox checked={selected.has(s.id)} label={`Выбрать ${s.cidr}`} onChange={(v) => {
                                        const next = new Set(selected)
                                        if (v) next.add(s.id)
                                        else next.delete(s.id)
                                        setSelected(next)
                                    }}/>
                                </td>
                                <td className="px-4"><Mono className="text-[14px] text-foreground">{s.cidr}</Mono></td>
                                <td className="px-4 font-mono text-[13.5px] text-muted-foreground">{s.region || "—"}</td>
                                <td className="px-4">
                                    <input defaultValue={s.white_count ?? ""} inputMode="numeric" placeholder="—" aria-label={`Подходящих адресов в ${s.cidr}`}
                                           className="h-9 w-20 rounded-lg border border-transparent bg-transparent px-2 font-mono tabular text-muted-foreground outline-none hover:border-border focus:border-amber/60 focus:text-foreground"
                                           onBlur={async (e) => {
                                               const v = e.target.value.trim()
                                               if (v === String(s.white_count ?? "")) return
                                               await patch(`/subnets/${s.id}`, {white_count: v ? Number(v) : 0})
                                               mutate("/subnets")
                                           }}/>
                                </td>
                                <td className="px-4 text-right font-mono tabular">
                                    {s.caught_total}
                                    <span className="block text-[12px] text-subtle">из {fmtNum(s.attempts_total)}</span>
                                </td>
                                <td className="px-4"><HeatBadge s={s}/></td>
                                <td className="px-4">
                                    {s.offered ? <Badge tone="ok">Выдаёт</Badge> : <Badge tone="muted" title={s.note}>Не выдаёт</Badge>}
                                </td>
                                <td className="px-4"><Switch checked={s.enabled} onChange={(v) => toggle(s, v)} label={`Искать в ${s.cidr}`}/></td>
                                <td className="px-4 text-right">
                                    <Tip label="Удалить подсеть">
                                        <button aria-label={`Удалить ${s.cidr}`} className="grid size-10 place-items-center rounded-xl text-subtle transition-colors hover:bg-danger/10 hover:text-danger" onClick={async () => {
                                            await del(`/subnets/${s.id}`)
                                            toast.success(`${s.cidr} удалена`)
                                            mutate("/subnets")
                                        }}><Trash className="size-[18px]"/></button>
                                    </Tip>
                                </td>
                            </tr>
                        )) : (
                            <tr><td colSpan={9}>
                                <Empty icon={<TreeStructure weight="duotone" className="size-6"/>}
                                       title={filter === "active" ? "Ни одна подсеть не включена" : "Ничего не найдено"}>
                                    {filter === "active" ? "Без включённых подсетей ключам негде искать. Включите нужные в списке «Выключены» или добавьте новые ниже." : "Попробуйте другой запрос."}
                                </Empty>
                            </td></tr>
                        )}
                        </tbody>
                    </table>
                </div>
            </Card>

            <Card className="mt-4 p-5 sm:p-6">
                <h2 className="text-[16px] font-semibold">Добавить подсети</h2>
                <p className="mt-1.5 text-[14px] leading-relaxed text-muted-foreground">
                    По одной в строке. Через пробел можно указать, сколько адресов в ней подходит, например <Mono>31.129.42.0/24 97</Mono>. Регион указывать не нужно. Если подсеть уже есть — она включится, а число обновится.
                </p>
                <Field label="Подсети" className="mt-4">
                    <Textarea id="subnets-input" value={text} onChange={(e) => setText(e.target.value)} placeholder={"31.129.42.0/24 97\n87.228.101.0/24 94"}/>
                </Field>
                <div className="mt-4 flex justify-end">
                    <Button onClick={add} disabled={busy || !text.trim()}><Plus weight="bold"/> Добавить подсети</Button>
                </div>
            </Card>
        </>
    )
}
