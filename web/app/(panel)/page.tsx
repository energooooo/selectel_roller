"use client"

import Link from "next/link"
import {useEffect, useState} from "react"
import {ArrowRight, CheckCircle, Circle, Crosshair, Key, Lightning, Target} from "@phosphor-icons/react"
import {Sparkline} from "@/components/charts"
import {EventRow} from "@/components/journal"
import {Badge, CHECK_STATUS, KeyStatusBadge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, CardHeader, Empty, Hint, Mono, PageHeader, Skeleton} from "@/components/ui/primitives"
import {useApi} from "@/lib/api"
import {fmtAgo, fmtDuration, fmtNum, plural} from "@/lib/format"
import {useStream} from "@/lib/stream"
import type {EventItem, Key as KeyT, Overview, ProxyItem, Settings} from "@/lib/types"
import {cn} from "@/lib/utils"

function Stat({label, help, value, sub, icon, accent, spark}: {
    label: string
    help: string
    value: React.ReactNode
    sub: React.ReactNode
    icon: React.ReactNode
    accent?: boolean
    spark?: number[]
}) {
    return (
        <Card className={cn("relative overflow-hidden p-5", accent && "ring-brand")}>
            <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[14px] font-medium text-muted-foreground">{label}<Hint>{help}</Hint></span>
                <span className="text-amber [&>svg]:size-5">{icon}</span>
            </div>
            <div className="mt-3 text-[36px] font-semibold leading-none tracking-[-0.04em] tabular">{value}</div>
            <div className="mt-2.5 text-[14px] text-muted-foreground">{sub}</div>
            {spark && spark.some(Boolean) && <Sparkline values={spark} className="mt-3"/>}
        </Card>
    )
}

function Countdown({seconds}: { seconds: number | null }) {
    const [left, setLeft] = useState(seconds)
    useEffect(() => {
        setLeft(seconds)
        if (seconds == null) return
        const id = setInterval(() => setLeft((s) => (s == null ? s : Math.max(0, s - 1))), 1000)
        return () => clearInterval(id)
    }, [seconds])
    if (left == null) return null
    return <span className="tabular">следующий запрос через {fmtDuration(left)}</span>
}

function HuntRow({k}: { k: KeyT }) {
    const {runtimes} = useStream()
    const rt = runtimes[k.id] ?? k.runtime
    const live = rt && ["hunting", "sleeping", "starting"].includes(k.status)
    return (
        <Link href="/keys" className="group flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-border bg-card-raised/40 px-4 py-3.5 transition-[border-color,transform] hover:-translate-y-0.5 hover:border-border-strong">
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2.5">
                    <span className="truncate text-[15.5px] font-medium">{k.name}</span>
                    <KeyStatusBadge status={k.status}/>
                </div>
                <p className="mt-1.5 truncate text-[14px] text-muted-foreground">
                    {live && rt ? (
                        rt.state === "sleeping"
                            ? <>Обошёл все подсети, отдыхает · <Countdown seconds={rt.next_action_in}/></>
                            : rt.current
                                ? <>Проверяет подсеть <Mono className="text-foreground">{rt.current.split(" ")[1]}</Mono> в {rt.current.split(" ")[0]}</>
                                : "Готовится к поиску"
                    ) : (
                        k.status_reason || "Не ищет"
                    )}
                </p>
            </div>
            <div className="flex items-center gap-6 text-right">
                <div>
                    <div className="font-mono text-[15px] tabular">{fmtNum(rt?.attempts_run ?? 0)}</div>
                    <div className="text-[12.5px] text-subtle">запросов с запуска</div>
                </div>
                <div>
                    <div className="font-mono text-[15px] tabular">{k.held_ips} из {k.ip_goal_effective}</div>
                    <div className="text-[12.5px] text-subtle">IP найдено</div>
                </div>
                <ArrowRight className="hidden size-5 text-subtle transition-transform group-hover:translate-x-0.5 sm:block"/>
            </div>
        </Link>
    )
}

/** Первые шаги: показываются, пока панель не настроена до конца. */
function GettingStarted({keys, proxies, ov, settings}: { keys: KeyT[]; proxies: ProxyItem[]; ov: Overview; settings?: Settings }) {
    const steps = [
        {
            done: proxies.some((p) => p.status === "ok" || p.status === "partial"),
            title: "Добавьте прокси",
            text: "Selectel блокирует сервер, который часто запрашивает IP. Прокси даёт каждому аккаунту свой внешний адрес.",
            href: "/proxies?add=1", action: "Добавить прокси",
        },
        {
            done: keys.length > 0,
            title: "Подключите аккаунт Selectel",
            text: "Нужны номер аккаунта и сервисный пользователь с доступом к проекту облака.",
            href: "/keys?add=1", action: "Добавить ключ",
        },
        {
            done: ov.subnets.enabled > 0,
            title: "Выберите подсети",
            text: "Подсети, адреса из которых вам нужны. Поиск идёт только в включённых.",
            href: "/subnets", action: "Открыть подсети",
        },
        {
            done: ov.keys.enabled > 0,
            title: "Запустите поиск",
            text: "Нажмите «Запустить» у ключа. Найденные адреса появятся во вкладке «IP на аккаунтах».",
            href: "/keys", action: "Открыть ключи",
        },
        {
            done: Boolean(settings?.tg_bot_token_set && settings?.tg_chat_id),
            title: "Подключите уведомления в Telegram",
            text: "Бот пришлёт найденный IP и предупредит, если что-то сломалось.",
            href: "/settings", action: "Настроить",
        },
    ]
    const left = steps.filter((s) => !s.done).length
    if (!left) return null
    const next = steps.find((s) => !s.done)!
    return (
        <Card className="mb-4 p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-[18px] font-semibold tracking-[-0.02em]">Настройка панели</h2>
                    <p className="mt-1 text-[14px] text-muted-foreground">
                        Осталось {left} {plural(left, "шаг", "шага", "шагов")} из {steps.length}. Следующий — «{next.title.toLowerCase()}».
                    </p>
                </div>
                <div className="h-2 w-40 overflow-hidden rounded-full bg-muted/60" aria-hidden>
                    <div className="h-2 rounded-full bg-brand transition-[width] duration-500" style={{width: `${((steps.length - left) / steps.length) * 100}%`}}/>
                </div>
            </div>
            <ol className="mt-5 grid gap-2 lg:grid-cols-5">
                {steps.map((s, i) => (
                    <li key={s.title} className={cn("flex flex-col rounded-xl border p-4",
                        s === next ? "border-amber/50 bg-amber/[0.06]" : "border-border bg-card-raised/30")}>
                        <div className="flex items-center gap-2">
                            {s.done ? <CheckCircle weight="fill" className="size-5 text-ok"/> : <Circle className="size-5 text-subtle"/>}
                            <span className="text-[12.5px] text-subtle">Шаг {i + 1}</span>
                        </div>
                        <p className={cn("mt-2 text-[15px] font-medium", s.done && "text-muted-foreground line-through decoration-subtle")}>{s.title}</p>
                        <p className="mt-1 flex-1 text-[13.5px] leading-relaxed text-muted-foreground">{s.text}</p>
                        {!s.done && (
                            <Button asChild size="sm" variant={s === next ? "brand" : "outline"} className="mt-3 w-full">
                                <Link href={s.href}>{s.action}</Link>
                            </Button>
                        )}
                    </li>
                ))}
            </ol>
        </Card>
    )
}

export default function OverviewPage() {
    const {data: ov} = useApi<Overview>("/overview", {refreshInterval: 15_000})
    const {data: keys} = useApi<KeyT[]>("/keys", {refreshInterval: 20_000})
    const {data: proxies} = useApi<ProxyItem[]>("/proxies")
    const {data: settings} = useApi<Settings>("/settings")
    const {data: metrics} = useApi<{ series: { rate_per_min: number }[] }>("/metrics?period=1h", {refreshInterval: 60_000})
    const {data: initialEvents} = useApi<EventItem[]>("/events?limit=40&level=info,success,warn,error")
    const {events: liveEvents} = useStream()

    const seen = new Set<number>()
    const events = [...liveEvents, ...(initialEvents ?? [])].filter((e) => {
        if (e.id == null) return true
        if (seen.has(e.id)) return false
        seen.add(e.id)
        return true
    }).slice(0, 30)

    const active = (keys ?? []).filter((k) => k.enabled || ["error", "banned", "auth_failed", "done"].includes(k.status))

    return (
        <>
            <PageHeader title="Обзор" description="Сколько ключей ищут IP, что уже найдено и что происходит прямо сейчас."/>

            {ov && keys && proxies && <GettingStarted keys={keys} proxies={proxies} ov={ov} settings={settings}/>}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {ov ? (
                    <>
                        <Stat label="Ключи" icon={<Key weight="duotone"/>}
                              help="Ключ — это аккаунт Selectel, на котором панель ищет IP. Включённые ищут, остальные стоят."
                              value={<>{ov.keys.enabled}<span className="text-subtle"> из {ov.keys.total}</span></>}
                              sub={ov.keys.problems ? <span className="text-danger">Требуют внимания: {ov.keys.problems}</span> : "включены"}/>
                        <Stat label="Ищут сейчас" icon={<Crosshair weight="duotone"/>} accent={ov.keys.hunting > 0}
                              help="Ключи, которые прямо сейчас запрашивают адреса в ваших подсетях."
                              value={ov.keys.hunting} sub={`в ${ov.subnets.enabled} ${plural(ov.subnets.enabled, "подсети", "подсетях", "подсетях")}`}/>
                        <Stat label="Скорость" icon={<Lightning weight="duotone"/>}
                              help="Сколько запросов в минуту все ключи вместе отправляют в Selectel. Меняется паузами в настройках."
                              value={ov.rate_per_min.toFixed(1)} sub={`запросов в минуту · ${fmtNum(ov.attempts_24h)} за сутки`}
                              spark={metrics?.series.map((s) => s.rate_per_min)}/>
                        <Stat label="Найдено IP" icon={<Target weight="duotone"/>}
                              help="Все найденные адреса за всё время. «На аккаунтах» — те, что сейчас у вас."
                              value={ov.caught.total}
                              sub={<>сегодня {ov.caught.today} · на аккаунтах {ov.caught.held}</>}/>
                    </>
                ) : (
                    Array.from({length: 4}, (_, i) => <Skeleton key={i} className="h-[146px] rounded-2xl"/>)
                )}
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
                <Card>
                    <CardHeader title="Ключи в работе" hint="Найденный IP сразу придёт в Telegram и появится во вкладке «IP на аккаунтах»."
                                action={<Button asChild variant="outline" size="sm"><Link href="/keys">Все ключи</Link></Button>}/>
                    <div className="space-y-2 p-5 sm:p-6">
                        {!keys ? (
                            Array.from({length: 3}, (_, i) => <Skeleton key={i} className="h-20"/>)
                        ) : active.length ? (
                            active.map((k) => <HuntRow key={k.id} k={k}/>)
                        ) : (
                            <Empty icon={<Key weight="duotone" className="size-6"/>} title="Сейчас ни один ключ не ищет IP"
                                   action={<Button asChild size="sm"><Link href="/keys">Открыть ключи</Link></Button>}>
                                Запустите ключ — поиск начнётся сразу, перезапускать ничего не нужно.
                            </Empty>
                        )}
                    </div>
                </Card>

                <div className="flex flex-col gap-4">
                    <Card>
                        <CardHeader title="Последние находки"
                                    action={<Link href="/history" className="text-[14px] text-amber hover:underline">Все</Link>}/>
                        <div className="px-5 pb-4 pt-2 sm:px-6">
                            {ov?.last_caught.length ? (
                                <ul className="divide-y divide-border/70">
                                    {ov.last_caught.map((c) => (
                                        <li key={c.id} className="flex items-center gap-3 py-3">
                                            <Target weight="fill" className="size-5 shrink-0 text-amber"/>
                                            <div className="min-w-0 flex-1">
                                                <Mono className="text-[14.5px] text-foreground">{c.ip}</Mono>
                                                <p className="text-[13px] text-subtle">{c.key_name ?? `аккаунт ${c.account_id}`} · {c.region} · {fmtAgo(c.caught_at)}</p>
                                            </div>
                                            <Badge tone={CHECK_STATUS[c.check_status]?.tone}>{CHECK_STATUS[c.check_status]?.label}</Badge>
                                        </li>
                                    ))}
                                </ul>
                            ) : (
                                <p className="py-6 text-center text-[14px] text-subtle">Пока ничего не найдено</p>
                            )}
                        </div>
                    </Card>

                    <Card className="flex min-h-0 flex-1 flex-col">
                        <CardHeader title="Что происходит" action={<Link href="/journal" className="text-[14px] text-amber hover:underline">Журнал</Link>}/>
                        <div className="max-h-[420px] overflow-y-auto px-5 pb-4 pt-2 sm:px-6">
                            {events.length ? events.map((e, i) => <EventRow key={e.id ?? `l${i}`} e={e} dense/>) : (
                                <p className="py-6 text-center text-[14px] text-subtle">Событий пока нет — они появятся, когда ключ начнёт поиск.</p>
                            )}
                        </div>
                    </Card>
                </div>
            </div>
        </>
    )
}
