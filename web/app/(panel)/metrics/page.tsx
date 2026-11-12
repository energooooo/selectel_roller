"use client"

import {useState} from "react"
import {ChartLineUp} from "@phosphor-icons/react"
import {BarList, Heatmap, Legend, LineChart, SERIES} from "@/components/charts"
import {KeyStatusBadge} from "@/components/status"
import {Card, CardHeader, Empty, Mono, PageHeader, Segmented, Skeleton} from "@/components/ui/primitives"
import {useApi} from "@/lib/api"
import {fmtNum} from "@/lib/format"
import {cn} from "@/lib/utils"

type Fields = { attempts: number; busy: number; misses: number; errors: number; timeouts: number; proxy_errors: number; caught: number }

interface MetricsData {
    period: string
    step_s: number
    series: (Fields & { t: number; latency_ms: number | null; rate_per_min: number })[]
    by_key_series: { key_id: number; name: string; values: number[] }[]
    by_key: (Fields & { key_id: number; name: string; account_id: string; rate_per_min: number; latency_ms: number | null; attempts_per_ip: number | null; status: string })[]
    by_region: (Fields & { region: string })[]
    totals: Fields
    rate_per_min: number
    caught_by_subnet: { subnet: string; caught: number }[]
    caught_by_account: { name: string; caught: number }[]
    heatmap: { attempts: number[][]; caught: number[][] }
}

const PERIODS = [["1h", "Час"], ["24h", "Сутки"], ["7d", "Неделя"], ["30d", "Месяц"]] as const

function Tile({label, value, sub}: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
    return (
        <Card className="p-5">
            <div className="text-[14px] font-medium text-muted-foreground">{label}</div>
            <div className="mt-2.5 text-[28px] font-semibold leading-none tracking-[-0.03em] tabular">{value}</div>
            {sub && <div className="mt-2 text-[13.5px] text-muted-foreground">{sub}</div>}
        </Card>
    )
}

export default function MetricsPage() {
    const [period, setPeriod] = useState<(typeof PERIODS)[number][0]>("24h")
    const {data} = useApi<MetricsData>(`/metrics?period=${period}`, {refreshInterval: 60_000})
    const [heat, setHeat] = useState<"caught" | "attempts">("attempts")

    const formatX = (t: number) => {
        const d = new Date(t)
        const opts: Intl.DateTimeFormatOptions = period === "1h" || period === "24h"
            ? {hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow"}
            : {day: "2-digit", month: "2-digit", hour: "2-digit", timeZone: "Europe/Moscow"}
        return d.toLocaleString("ru-RU", opts)
    }

    const t = data?.totals
    const errorsTotal = t ? t.errors + t.timeouts + t.proxy_errors : 0
    // Больше 8 ключей на одном графике не различить — остальные сворачиваются в «Прочие».
    // «Прочие» — среднее на один аккаунт, а не сумма: иначе 16 обычных аккаунтов рисуются
    // одной линией в 16 раз выше остальных и выглядят как самый быстрый ключ.
    const keyLines = (() => {
        if (!data) return []
        const sorted = [...data.by_key_series].sort((a, b) => b.values.reduce((s, v) => s + v, 0) - a.values.reduce((s, v) => s + v, 0))
        const head = sorted.slice(0, 7)
        const tail = sorted.slice(7)
        const lines = head.map((k, i) => ({name: k.name, values: k.values.map((v) => +(v * 60 / data.step_s).toFixed(2)), color: SERIES[i]}))
        if (tail.length) {
            lines.push({
                name: `Прочие ${tail.length}, в среднем на аккаунт`,
                values: tail[0].values.map((_, i) => +(tail.reduce((s, k) => s + k.values[i], 0) / tail.length * 60 / data.step_s).toFixed(2)),
                color: "var(--subtle)",
            })
        }
        return lines
    })()

    return (
        <>
            <PageHeader title="Статистика" description="Как быстро идёт поиск, сколько адресов найдено на каждом аккаунте и в каких подсетях, сколько было ошибок."
                        action={
                            <Segmented value={period} onChange={setPeriod} options={PERIODS}/>
                        }/>

            {!data || !t ? (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Array.from({length: 4}, (_, i) => <Skeleton key={i} className="h-24 rounded-2xl"/>)}</div>
            ) : (
                <>
                    <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
                        <Tile label="Запросов к Selectel" value={fmtNum(t.attempts)} sub={`${data.rate_per_min} в минуту в среднем`}/>
                        <Tile label="Найдено IP" value={t.caught} sub={t.caught ? `≈ ${fmtNum(Math.round(t.attempts / t.caught))} запросов на один адрес` : "за период — ни одного"}/>
                        <Tile label="Нет свободных адресов" value={fmtNum(t.busy)} sub={t.attempts ? `${Math.round((t.busy / t.attempts) * 100)}% запросов — обычный ответ` : "—"}/>
                        <Tile label="Ошибки" value={fmtNum(errorsTotal)}
                              sub={`Selectel не ответил: ${fmtNum(t.timeouts)} · сбой прокси: ${fmtNum(t.proxy_errors)}`}/>
                        <Tile label="Неподходящие адреса" value={fmtNum(t.misses)} sub="выданы и сразу возвращены"/>
                    </div>

                    <Card className="mt-4">
                        <CardHeader title="Скорость по аккаунтам" hint="Запросов в минуту. Наведите на график, чтобы увидеть точные значения."/>
                        <div className="p-5 pt-4">
                            {keyLines.length ? (
                                <>
                                    <Legend items={keyLines}/>
                                    <div className="mt-3"><LineChart times={data.series.map((s) => s.t)} series={keyLines} formatX={formatX}/></div>
                                </>
                            ) : (
                                <Empty icon={<ChartLineUp weight="duotone" className="size-6"/>} title="За этот период данных нет">Статистика собирается, пока ключи ищут IP.</Empty>
                            )}
                        </div>
                    </Card>

                    <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <Card>
                            <CardHeader title="Ошибки по времени" hint="Отказы Selectel, запросы без ответа и сбои прокси."/>
                            <div className="p-5 pt-4">
                                <Legend items={[{name: "Отказ Selectel", color: SERIES[1]}, {name: "Нет ответа", color: SERIES[3]}, {name: "Сбой прокси", color: SERIES[4]}]}/>
                                <div className="mt-3">
                                    <LineChart height={200} times={data.series.map((s) => s.t)} formatX={formatX} series={[
                                        {name: "Отказ Selectel", values: data.series.map((s) => s.errors), color: SERIES[1]},
                                        {name: "Нет ответа", values: data.series.map((s) => s.timeouts), color: SERIES[3]},
                                        {name: "Сбой прокси", values: data.series.map((s) => s.proxy_errors), color: SERIES[4]},
                                    ]}/>
                                </div>
                            </div>
                        </Card>
                        <Card>
                            <CardHeader title="Скорость ответа Selectel" hint="Сколько в среднем Selectel отвечает на запрос адреса, в миллисекундах."/>
                            <div className="p-5 pt-4">
                                <LineChart height={226} times={data.series.map((s) => s.t)} formatX={formatX} unit=" мс"
                                           series={[{name: "Время ответа", values: data.series.map((s) => s.latency_ms ?? 0), color: SERIES[0]}]}/>
                            </div>
                        </Card>
                    </div>

                    <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <Card>
                            <CardHeader title="Найдено по аккаунтам"/>
                            <div className="p-5 pt-4"><BarList rows={data.caught_by_account.map((r) => ({label: r.name, value: r.caught}))} color={SERIES[0]} empty="За период ничего не найдено"/></div>
                        </Card>
                        <Card>
                            <CardHeader title="Найдено по подсетям" hint="Из каких подсетей чаще удаётся получить адрес."/>
                            <div className="p-5 pt-4"><BarList rows={data.caught_by_subnet.map((r) => ({label: r.subnet, value: r.caught}))} color={SERIES[2]} empty="За период ничего не найдено"/></div>
                        </Card>
                    </div>

                    <Card className="mt-4">
                        <CardHeader title="Сводка по аккаунтам" hint="За выбранный период."/>
                        <div className="mt-3 overflow-x-auto">
                            <table className="w-full min-w-[760px] text-[13.5px]">
                                <thead>
                                <tr className="border-y border-border text-left">
                                    {["Ключ", "Состояние", "Запросов", "В минуту", "Нет свободных", "Ошибок", "Найдено", "Запросов на IP", "Ответ Selectel"].map((h) => (
                                        <th key={h} className="px-4 py-3 text-left text-[13px] font-medium text-muted-foreground first:pl-5 sm:first:pl-6">{h}</th>
                                    ))}
                                </tr>
                                </thead>
                                <tbody>
                                {data.by_key.length ? data.by_key.map((r) => (
                                    <tr key={r.key_id} className="border-b border-border/60 last:border-0">
                                        <td className="py-3 pl-5 pr-4 sm:pl-6">{r.name} <Mono className="text-subtle">{r.account_id}</Mono></td>
                                        <td className="px-4"><KeyStatusBadge status={r.status}/></td>
                                        <td className="px-4 font-mono tabular">{fmtNum(r.attempts)}</td>
                                        <td className="px-4 font-mono tabular">{r.rate_per_min}</td>
                                        <td className="px-4 font-mono tabular text-muted-foreground">{fmtNum(r.busy)}</td>
                                        <td className="px-4 font-mono tabular text-muted-foreground">{fmtNum(r.errors + r.timeouts + r.proxy_errors)}</td>
                                        <td className="px-4 font-mono tabular font-semibold">{r.caught}</td>
                                        <td className="px-4 font-mono tabular text-muted-foreground">{fmtNum(r.attempts_per_ip)}</td>
                                        <td className="px-4 font-mono tabular text-muted-foreground">{r.latency_ms != null ? `${r.latency_ms} мс` : "—"}</td>
                                    </tr>
                                )) : (
                                    <tr><td colSpan={9} className="px-5 py-8 text-center text-[14px] text-subtle">За этот период данных нет</td></tr>
                                )}
                                </tbody>
                            </table>
                        </div>
                    </Card>

                    <Card className="mt-4">
                        <CardHeader title="Когда чаще находится" hint="По дням недели и часам по Москве, за последние 30 дней."
                                    action={
                                        <Segmented size="sm" value={heat} onChange={setHeat} options={[["attempts", "Запросы"], ["caught", "Находки"]] as const}/>
                                    }/>
                        <div className="p-5 pt-4">
                            <Heatmap values={data.heatmap[heat]} label={heat === "caught" ? "находок" : "запросов"}/>
                        </div>
                    </Card>
                </>
            )}
        </>
    )
}
