"use client"

import {useEffect, useMemo, useRef, useState} from "react"
import {
    ArrowsClockwise, CaretDown, CaretLeft, CaretRight, GearSix, MagnifyingGlass, Pause, Play, Trash, X,
} from "@phosphor-icons/react"
import {toast} from "sonner"
import {ConfirmDialog} from "@/components/confirm"
import {Badge, KEY_STATUS, KeyStatusBadge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {ActionMenu, Card, Dialog, Empty, Input, Mono, Select, Tip} from "@/components/ui/primitives"
import {ApiError, del, post} from "@/lib/api"
import {fmtAgo, fmtDuration, fmtNum, proxyName} from "@/lib/format"
import {useStream} from "@/lib/stream"
import type {Key, Runtime} from "@/lib/types"
import {cn} from "@/lib/utils"

const LIVE = new Set(["starting", "hunting", "sleeping"])
const TERMINAL = new Set(["done", "banned", "auth_failed"])
const PROBLEM_HINT: Record<string, string> = {
    auth_failed: "Проверьте пароль и доступ сервисного пользователя к проекту.",
    banned: "Selectel временно ограничил выдачу IP для этого аккаунта.",
    error: "Подробности — в журнале.",
}
const SHORT_STATUS: Partial<Record<Key["status"], string>> = {
    auth_failed: "Ошибка входа",
    banned: "Заблокирован",
    sleeping: "Ищет IP · ждёт",
}
const FILTERS = [
    ["all", "Все"],
    ["live", "В работе"],
    ["paused", "Остановлены"],
    ["problem", "С ошибками"],
    ["done", "Готово"],
] as const
type Filter = (typeof FILTERS)[number][0]
type PauseScope = "all" | "selected"
const collator = new Intl.Collator("ru", {numeric: true})
const SORTS = {
    account: (a: Key, b: Key) => collator.compare(a.account_id, b.account_id),
    newest: (a: Key, b: Key) => b.id - a.id,
    name: (a: Key, b: Key) => collator.compare(a.name, b.name),
    ips: (a: Key, b: Key) => b.held_ips - a.held_ips,
}

function filterFor(key: Key): Exclude<Filter, "all"> {
    if (LIVE.has(key.status)) return "live"
    if (key.status === "done") return "done"
    if (PROBLEM_HINT[key.status]) return "problem"
    return "paused"
}

function canPause(key: Key) {
    return key.enabled && !TERMINAL.has(key.status)
}

function errorText(error: unknown) {
    return error instanceof ApiError ? error.message : "Не получилось — проверьте связь с сервером"
}

function Checkbox({label, checked, mixed = false, onChange, disabled}: {
    label: string; checked: boolean; mixed?: boolean; onChange: () => void; disabled: boolean
}) {
    const input = useRef<HTMLInputElement>(null)
    useEffect(() => {
        if (input.current) input.current.indeterminate = mixed
    }, [mixed])
    return (
        <label className="grid min-h-11 w-10 cursor-pointer place-items-center has-disabled:cursor-default">
            <input ref={input} type="checkbox" aria-label={label} checked={checked} onChange={onChange} disabled={disabled}
                   className="size-4 cursor-inherit accent-amber focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber disabled:opacity-50"/>
        </label>
    )
}

function KeyDetails({k, rt}: {k: Key; rt: Runtime | null}) {
    const [region, subnet] = rt?.current.split(" ") ?? []
    const proxy = k.proxy_mode === "none" ? "Без прокси" : k.proxy
        ? <>{proxyName(k.proxy)}{k.proxy.exit_ip && <> · <Mono>{k.proxy.exit_ip}</Mono></>}{k.proxy_mode === "auto" && " · автовыбор"}</>
        : k.proxy_mode === "auto" ? "Будет выбран при запуске" : "Не выбран"
    const fields = [
        ["Пользователь", k.username || "—"],
        ["Проект", k.project_name || "—"],
        ["Прокси", proxy],
        ["Всего найдено IP", fmtNum(k.caught_total)],
        ["Запросов с запуска", rt ? fmtNum(rt.attempts_run) : "—"],
        ["Запросов в минуту", k.rate_per_min.toLocaleString("ru", {maximumFractionDigits: 1})],
        ["Последняя находка", fmtAgo(k.last_caught_at)],
    ] as const
    return (
        <div className="space-y-3 px-4 py-4 sm:px-6">
            {(PROBLEM_HINT[k.status] || k.status_reason) && (
                <p className={cn("text-sm leading-relaxed wrap-break-word", PROBLEM_HINT[k.status] ? "text-danger" : "text-muted-foreground")}>
                    {k.status_reason || KEY_STATUS[k.status]?.about} {PROBLEM_HINT[k.status]}
                </p>
            )}
            {rt && (
                <div className="space-y-1 text-sm text-muted-foreground">
                    <p className="text-foreground">
                        {rt.state === "sleeping" ? "Пауза между проходами" : subnet ? <>Подсеть <Mono>{subnet}</Mono> · {region}</> : "Подключение к Selectel"}
                    </p>
                    <p>Проход {rt.round} · подсети {rt.targets_active} из {rt.targets_total} · в работе {fmtDuration(rt.uptime_s)}
                        {rt.next_action_in != null && <> · следующий запрос через {fmtDuration(rt.next_action_in)}</>}
                    </p>
                    {Object.keys(rt.paused_regions).length > 0 && (
                        <p className="text-warn">
                            Пауза по регионам: {Object.entries(rt.paused_regions).map(([name, seconds]) => `${name} (${seconds} с)`).join(", ")}
                        </p>
                    )}
                </div>
            )}
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 lg:grid-cols-3">
                {fields.map(([label, value]) => (
                    <div key={label} className="min-w-0">
                        <dt className="text-xs text-muted-foreground">{label}</dt>
                        <dd className="mt-0.5 text-sm wrap-break-word">{value}</dd>
                    </div>
                ))}
            </dl>
        </div>
    )
}

function KeyRow({k, runtime, checked, onCheck, locked, onBusyChange, onEdit, refresh}: {
    k: Key; runtime?: Runtime; checked: boolean; onCheck: () => void; locked: boolean
    onBusyChange: (delta: number) => void; onEdit: () => void; refresh: () => Promise<void>
}) {
    const [expanded, setExpanded] = useState(false)
    const [busy, setBusy] = useState(false)
    const [confirmDelete, setConfirmDelete] = useState(false)
    const liveRuntime = runtime && LIVE.has(runtime.state) && (k.status !== "starting" || runtime.state === "starting")
    const rt = LIVE.has(k.status) ? (liveRuntime ? runtime : k.runtime) : null
    const disabled = busy || locked
    const active = canPause(k)
    const action = active ? "Остановить" : "Запустить"
    const status = KEY_STATUS[k.status]
    const run = async (fn: () => Promise<unknown>, message: string) => {
        setBusy(true)
        onBusyChange(1)
        try {
            await fn()
            toast.success(message)
        } catch (error) {
            toast.error(errorText(error))
        } finally {
            await refresh()
            onBusyChange(-1)
            setBusy(false)
        }
    }
    const toggle = () => run(() => post(`/keys/${k.id}/${active ? "pause" : "start"}`),
        `${k.name}: поиск ${active ? "остановлен" : "запущен"}`)

    return (
        <>
            <tr className={cn("border-t border-border/70 transition-colors hover:bg-card-raised/70",
                (checked || expanded) && "bg-amber/[0.045]")}>
                <td className="pl-0.5 sm:pl-2">
                    <Checkbox label={`Выбрать ключ ${k.name}`} checked={checked} onChange={onCheck} disabled={locked}/>
                </td>
                <th scope="row" className="py-1 pr-1 text-left font-normal sm:pr-3">
                    <button type="button" aria-expanded={expanded} aria-controls={expanded ? `key-details-${k.id}` : undefined}
                            className="group flex min-h-11 w-full min-w-0 items-center gap-1 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-amber/70 sm:gap-2"
                            onClick={() => setExpanded(!expanded)}>
                        <CaretDown aria-hidden className={cn("size-3 shrink-0 -rotate-90 text-subtle transition-transform group-hover:text-amber motion-reduce:transition-none", expanded && "rotate-0")}/>
                        <span className="min-w-0">
                            <span className="block truncate text-sm font-semibold group-hover:text-amber" title={k.name}>{k.name}</span>
                            <Mono className="block truncate text-xs text-muted-foreground">№ {k.account_id}</Mono>
                        </span>
                    </button>
                    <div className="mb-1 ml-4 flex flex-wrap items-center gap-x-2 gap-y-1 md:hidden">
                        <Badge tone={status?.tone} title={status?.about} className="h-6 gap-1.5 px-2 text-[11px]">
                            {SHORT_STATUS[k.status] ?? status?.label ?? k.status}
                        </Badge>
                        <Mono className="text-xs text-muted-foreground">{k.held_ips}/{k.ip_goal_effective} IP</Mono>
                    </div>
                </th>
                <td className="hidden px-3 md:table-cell"><KeyStatusBadge status={k.status}/></td>
                <td className="hidden whitespace-nowrap px-3 text-right md:table-cell">
                    <Mono className={cn("text-sm", k.held_ips > 0 && "text-ok")}>{k.held_ips}</Mono>
                    <Mono className="text-xs text-muted-foreground"> / {k.ip_goal_effective}</Mono>
                </td>
                <td className="hidden px-3 text-sm text-muted-foreground xl:table-cell">
                    {PROBLEM_HINT[k.status] ? (
                        <span className="block truncate" title={k.status_reason}>{k.status_reason || "Откройте детали"}</span>
                    ) : rt ? (
                        <span className="block truncate" title={rt.current}>{rt.current ? <Mono>{rt.current}</Mono> : rt.state === "sleeping" ? "Между проходами" : "Подключение"}</span>
                    ) : <span className="text-subtle">—</span>}
                </td>
                <td className="pr-1.5 sm:pr-3">
                    <div className="flex justify-end gap-1 sm:gap-1.5">
                        <Tip label={action}>
                            <Button variant="ghost" size="icon" className={cn("hidden size-9 min-[360px]:inline-flex", !active && "text-ok")}
                                    aria-label={`${action} поиск: ${k.name}`} disabled={disabled} onClick={toggle}>
                                {active ? <Pause weight="fill" className="size-4"/> : <Play weight="fill" className="size-4"/>}
                            </Button>
                        </Tip>
                        <ActionMenu label={`Действия с ключом ${k.name}`} items={[
                            {label: `${action} поиск`, icon: active ? <Pause/> : <Play/>, disabled, onSelect: toggle},
                            {label: "Настройки ключа", icon: <GearSix/>, disabled, onSelect: onEdit},
                            {
                                label: "Сменить прокси", icon: <ArrowsClockwise/>, disabled: disabled || k.proxy_mode === "none",
                                onSelect: () => run(() => post(`/keys/${k.id}/rotate-proxy`), "Прокси ключа обновлён"),
                            },
                            "sep",
                            {label: "Удалить ключ", icon: <Trash/>, danger: true, disabled, onSelect: () => setConfirmDelete(true)},
                        ]}/>
                    </div>
                    {confirmDelete && (
                        <ConfirmDialog open onOpenChange={setConfirmDelete} danger confirmLabel="Удалить ключ"
                                       title={`Удалить ключ «${k.name}»?`}
                                       description="Поиск на этом аккаунте остановится, ключ исчезнет из панели. Адреса на аккаунте Selectel останутся, история находок сохранится."
                                       onConfirm={() => run(() => del(`/keys/${k.id}`), "Ключ удалён")}/>
                    )}
                </td>
            </tr>
            {expanded && (
                <tr id={`key-details-${k.id}`} className="border-t border-border/70 bg-card-raised/60">
                    <td colSpan={6}><KeyDetails k={k} rt={rt}/></td>
                </tr>
            )}
        </>
    )
}

export function KeysTable({keys, onEdit, refresh}: {
    keys: Key[]; onEdit: (key: Key) => void; refresh: () => Promise<void>
}) {
    const {runtimes} = useStream()
    const top = useRef<HTMLDivElement>(null)
    const [query, setQuery] = useState("")
    const [filter, setFilter] = useState<Filter>("all")
    const [sort, setSort] = useState<keyof typeof SORTS>("account")
    const [pageSize, setPageSize] = useState(25)
    const [page, setPage] = useState(1)
    const [selection, setSelection] = useState<Set<number>>(() => new Set())
    const [pending, setPending] = useState<PauseScope | "start" | null>(null)
    const [confirmPause, setConfirmPause] = useState<PauseScope | null>(null)
    const [busyRows, setBusyRows] = useState(0)
    const rowBusyChange = (delta: number) => setBusyRows((count) => count + delta)

    const {counts, pausable, startable, filtered, selected} = useMemo(() => {
        const counts: Record<Filter, number> = {all: keys.length, live: 0, paused: 0, problem: 0, done: 0}
        const terms = query.trim().toLocaleLowerCase("ru").split(/\s+/).filter(Boolean)
        const pausable: Key[] = [], startable: Key[] = [], filtered: Key[] = [], selected: Key[] = []
        for (const key of keys) {
            const group = filterFor(key)
            counts[group]++
            if (canPause(key)) pausable.push(key)
            if (!key.enabled && key.status !== "banned" && key.status !== "auth_failed") startable.push(key)
            if (selection.has(key.id)) selected.push(key)
            if (filter !== "all" && group !== filter) continue
            const text = [key.name, key.account_id, key.username, key.project_name].join(" ").toLocaleLowerCase("ru")
            if (terms.every((term) => text.includes(term))) filtered.push(key)
        }
        filtered.sort((a, b) => SORTS[sort](a, b) || a.id - b.id)
        return {counts, pausable, startable, filtered, selected}
    }, [keys, filter, query, selection, sort])

    const pages = Math.max(1, Math.ceil(filtered.length / pageSize))
    const currentPage = Math.min(page, pages)
    const offset = (currentPage - 1) * pageSize
    const visible = filtered.slice(offset, offset + pageSize)
    const checkedOnPage = visible.filter((key) => selection.has(key.id)).length
    const allOnPage = visible.length > 0 && checkedOnPage === visible.length
    const checkedPausable = selected.filter(canPause)
    const blockBulk = pending !== null || busyRows > 0

    const updateSelection = (ids: number[], checked: boolean) => setSelection((old) => {
        const next = new Set(old)
        for (const id of ids) checked ? next.add(id) : next.delete(id)
        return next
    })
    const changeFilter = (value: Filter, text: string) => {
        setFilter(value)
        setQuery(text)
        setPage(1)
        setSelection(new Set())
    }
    const goTo = (value: number) => {
        setPage(value)
        top.current?.scrollIntoView({block: "start"})
    }

    const pause = async (scope: PauseScope) => {
        setPending(scope)
        try {
            const {paused} = await post<{paused: number}>("/keys/pause", {
                key_ids: scope === "all" ? null : checkedPausable.map((key) => key.id),
            })
            if (paused) toast.success(`Поиск остановлен на ключах: ${fmtNum(paused)}`)
            else toast.info("Эти ключи уже остановлены")
            setSelection(new Set())
            setConfirmPause(null)
        } catch (error) {
            toast.error(errorText(error))
        } finally {
            await refresh()
            setPending(null)
        }
    }

    const startAll = async () => {
        setPending("start")
        let started = 0
        try {
            for (const k of startable) {
                try {
                    await post(`/keys/${k.id}/start`)
                    started++
                } catch {
                    // Продолжаем с другими ключами, а в итоге показываем число сбоев.
                }
            }
            const failed = startable.length - started
            if (failed) toast.error(`Запущено: ${started}. Не удалось запустить: ${failed}.`)
            else toast.success(`Запущено ключей: ${fmtNum(started)}`)
        } finally {
            await refresh()
            setPending(null)
        }
    }

    return (
        <div ref={top} className="scroll-mt-[76px]">
            <Card className="overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-3 sm:px-4">
                    <div role="group" aria-label="Статус ключей" className="hidden flex-wrap gap-1 sm:flex">
                        {FILTERS.map(([value, label]) => (
                            <button key={value} type="button" aria-pressed={filter === value}
                                    onClick={() => { if (filter !== value) changeFilter(value, query) }}
                                    className={cn("flex min-h-9 items-center gap-2 rounded-lg px-2.5 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-amber/60",
                                        filter === value ? "bg-amber/12 text-amber" : "text-muted-foreground hover:bg-card-raised hover:text-foreground")}>
                                {label} <span className="text-xs tabular">{fmtNum(counts[value])}</span>
                            </button>
                        ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 sm:gap-2">
                        {startable.length > 1 && (
                            <Button size="sm" variant="ghost" className="h-9 px-2 text-xs sm:px-3 sm:text-[13px]" onClick={startAll} disabled={blockBulk}>
                                <Play className="size-3.5"/> {pending === "start" ? "Запускаем…" : "Запустить все"}
                            </Button>
                        )}
                        <Button size="sm" variant="danger" className="h-9 px-2 text-xs sm:px-3 sm:text-[13px]" disabled={blockBulk || !pausable.length}
                                onClick={() => setConfirmPause("all")}>
                            <Pause weight="fill" className="size-3.5"/> Остановить все
                        </Button>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2 border-t border-border/70 px-3 py-3 sm:px-4">
                    <div className="relative min-w-0 flex-1 basis-full sm:basis-64">
                        <MagnifyingGlass aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"/>
                        <Input className="h-10 pl-9 pr-10 text-sm" aria-label="Поиск по ключам"
                               placeholder="Имя, аккаунт, пользователь или проект" value={query}
                               onChange={(event) => changeFilter(filter, event.target.value)}/>
                        {query && (
                            <button type="button" aria-label="Очистить поиск" onClick={() => changeFilter(filter, "")}
                                    className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-xl text-muted-foreground hover:text-foreground focus-visible:outline-amber">
                                <X className="size-4"/>
                            </button>
                        )}
                    </div>
                    <Select value={filter} aria-label="Фильтр по статусу" className="h-10 min-w-0 flex-1 px-2 text-xs sm:hidden"
                            onChange={(event) => changeFilter(event.target.value as Filter, query)}>
                        {FILTERS.map(([value, label]) => <option key={value} value={value}>{label} ({fmtNum(counts[value])})</option>)}
                    </Select>
                    <Select value={sort} aria-label="Сортировка ключей" className="h-10 min-w-0 flex-1 px-2 text-xs sm:w-auto sm:flex-none sm:px-3.5 sm:text-sm"
                            onChange={(event) => { setSort(event.target.value as keyof typeof SORTS); setPage(1) }}>
                        <option value="account">По аккаунту</option>
                        <option value="newest">Сначала новые</option>
                        <option value="name">По названию</option>
                        <option value="ips">По найденным IP</option>
                    </Select>
                </div>
                {selected.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-amber/20 bg-amber/[0.05] px-3 py-2 sm:px-4">
                        <span role="status" className="text-sm font-medium text-amber">Выбрано: {fmtNum(selected.length)}</span>
                        <Button size="sm" variant="outline" className="h-8 px-3 text-xs" disabled={blockBulk || !checkedPausable.length}
                                onClick={() => setConfirmPause("selected")}>
                            <Pause weight="fill" className="size-3.5"/> Остановить выбранные ({fmtNum(checkedPausable.length)})
                        </Button>
                        <Button size="sm" variant="ghost" className="h-8 px-2 text-xs" disabled={pending !== null}
                                onClick={() => setSelection(new Set())}>Снять выбор</Button>
                    </div>
                )}
                {visible.length ? (
                    <table className="w-full table-fixed border-collapse text-sm">
                        <thead className="border-t border-border bg-card-raised/55 text-left text-xs text-muted-foreground">
                            <tr>
                                <th scope="col" className="w-[43px] pl-0.5 sm:w-12 sm:pl-2">
                                    <Checkbox label="Выбрать все ключи на странице" checked={allOnPage} mixed={checkedOnPage > 0 && !allOnPage}
                                              onChange={() => updateSelection(visible.map((key) => key.id), !allOnPage)} disabled={pending !== null}/>
                                </th>
                                <th scope="col" className="pl-4 font-medium sm:pl-5">Ключ / аккаунт</th>
                                <th scope="col" className="hidden w-[188px] px-3 font-medium md:table-cell">Статус</th>
                                <th scope="col" className="hidden w-24 px-3 text-right font-medium md:table-cell">IP / цель</th>
                                <th scope="col" className="hidden w-[24%] px-3 font-medium xl:table-cell">Что происходит</th>
                                <th scope="col" className="w-[52px] min-[360px]:w-24 sm:w-28"><span className="sr-only">Действия</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((key) => (
                                <KeyRow key={key.id} k={key} runtime={runtimes[key.id]} checked={selection.has(key.id)}
                                        onCheck={() => updateSelection([key.id], !selection.has(key.id))}
                                        locked={pending !== null} onBusyChange={rowBusyChange} onEdit={() => onEdit(key)} refresh={refresh}/>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <div className="border-t border-border">
                        <Empty icon={<MagnifyingGlass className="size-5"/>} title="Ключи не найдены"
                               action={<Button size="sm" variant="outline" onClick={() => changeFilter("all", "")}>Сбросить фильтры</Button>}>
                            Попробуйте другой запрос или статус.
                        </Empty>
                    </div>
                )}
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-border px-3 py-2 text-sm text-muted-foreground sm:px-4">
                    <span role="status" className="tabular">
                        {visible.length ? `${fmtNum(offset + 1)}–${fmtNum(offset + visible.length)} из ${fmtNum(filtered.length)}` : "0 ключей"}
                    </span>
                    <div className="flex flex-wrap items-center gap-2">
                        <label className="flex items-center gap-2">
                            <span className="hidden sm:inline">На странице</span>
                            <Select aria-label="Ключей на странице" value={pageSize} className="h-9 w-auto px-2 text-sm"
                                    onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1) }}>
                                {[10, 25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
                            </Select>
                        </label>
                        <div className="flex items-center gap-1">
                            <Button variant="outline" size="icon" className="size-9" aria-label="Предыдущая страница"
                                    disabled={currentPage === 1} onClick={() => goTo(currentPage - 1)}><CaretLeft className="size-4"/></Button>
                            <span aria-label={`Страница ${currentPage} из ${pages}`} className="min-w-12 px-1 text-center text-xs tabular">{currentPage} / {pages}</span>
                            <Button variant="outline" size="icon" className="size-9" aria-label="Следующая страница"
                                    disabled={currentPage >= pages} onClick={() => goTo(currentPage + 1)}><CaretRight className="size-4"/></Button>
                        </div>
                    </div>
                </div>
            </Card>
            {confirmPause && (
                <Dialog open onOpenChange={(open) => { if (!open && !pending) setConfirmPause(null) }}
                        title={confirmPause === "all" ? "Остановить поиск на всех ключах?" : "Остановить поиск на выбранных ключах?"}
                        description={confirmPause === "all"
                            ? `Ключей к остановке: ${fmtNum(pausable.length)}, включая скрытые фильтрами. Найденные IP останутся на аккаунтах.`
                            : `Выбранных ключей к остановке: ${fmtNum(checkedPausable.length)}. Найденные IP останутся на аккаунтах.`}>
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <Button variant="ghost" disabled={!!pending} onClick={() => setConfirmPause(null)}>Отмена</Button>
                        <Button variant="danger" disabled={!!pending || !(confirmPause === "all" ? pausable.length : checkedPausable.length)}
                                onClick={() => pause(confirmPause)}>
                            {pending ? "Останавливаем…" : confirmPause === "all" ? "Остановить все" : "Остановить выбранные"}
                        </Button>
                    </div>
                </Dialog>
            )}
        </div>
    )
}
