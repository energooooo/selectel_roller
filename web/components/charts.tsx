"use client"

/**
 * Графики на чистом SVG. Правила (dataviz): линии 2px, площадь — заливка ~10%,
 * сетка — волосяная и тихая, подписи — цветом текста, не цветом серии,
 * у каждого графика есть подсказка при наведении.
 * Категориальная палитра — тёмная версия эталонной, проверена валидатором
 * на фоне карточки #141110 (все проверки пройдены).
 */
import {useEffect, useRef, useState} from "react"
import {cn} from "@/lib/utils"

export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
const GRID = "var(--border)"

/**
 * Круглая шкала: шаг 1, 2 или 5 × 10ⁿ, 3–5 делений, верхняя отметка выше максимума
 * (линия не упирается в край). Для счётчиков шаг не дробный.
 */
export function niceScale(maxValue: number, target = 4, integer = false): { max: number; ticks: number[] } {
    const v = Math.max(0, maxValue)
    if (v <= 0) return {max: 1, ticks: [0, 1]}
    const raw = v / target
    const exp = Math.pow(10, Math.floor(Math.log10(raw)))
    let step = [1, 2, 5, 10].map((m) => m * exp).find((s) => s >= raw) ?? 10 * exp
    if (integer) step = Math.max(1, Math.ceil(step))
    let top = Math.ceil(v / step) * step
    if (top <= v) top += step
    const ticks: number[] = []
    for (let t = 0; t <= top + step / 2; t += step) ticks.push(+t.toFixed(10))
    return {max: top, ticks}
}

const compact = new Intl.NumberFormat("ru-RU", {notation: "compact", maximumFractionDigits: 1})
const fmtTick = (v: number) => (v >= 10_000 ? compact.format(v) : new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1}).format(v))

function useWidth() {
    const ref = useRef<HTMLDivElement>(null)
    const [width, setWidth] = useState(600)
    useEffect(() => {
        if (!ref.current) return
        const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
        ro.observe(ref.current)
        return () => ro.disconnect()
    }, [])
    return {ref, width}
}

export function Sparkline({values, color = "var(--brand-amber)", className}: {
    values: number[]
    color?: string
    className?: string
}) {
    const w = 120, h = 32
    const max = Math.max(1, ...values)
    const pts = values.map((v, i) => [values.length > 1 ? (i / (values.length - 1)) * w : w, h - 2 - (v / max) * (h - 4)])
    const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("")
    return (
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-8 w-full", className)} aria-hidden>
            {values.length > 1 && (
                <>
                    <path d={`${line}L${w},${h}L0,${h}Z`} fill={color} opacity={0.12}/>
                    <path d={line} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke"
                          strokeLinejoin="round" strokeLinecap="round"/>
                </>
            )}
        </svg>
    )
}

export interface LineSeries {
    name: string
    values: number[]
    color: string
}

/** Линии во времени с перекрестием и подсказкой. Одна ось Y. */
export function LineChart({times, series, height = 240, formatX, unit = ""}: {
    times: number[]
    series: LineSeries[]
    height?: number
    formatX: (t: number) => string
    unit?: string
}) {
    const {ref, width} = useWidth()
    const [hover, setHover] = useState<number | null>(null)
    const pad = {l: 44, r: 12, t: 12, b: 26}
    const w = Math.max(200, width), h = height
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b
    const {max, ticks} = niceScale(Math.max(0, ...series.flatMap((s) => s.values)))
    const n = times.length
    const x = (i: number) => pad.l + (n > 1 ? (i / (n - 1)) * iw : iw / 2)
    const y = (v: number) => pad.t + ih - (v / max) * ih
    const xLabels = n > 1 ? [0, Math.floor((n - 1) / 3), Math.floor((2 * (n - 1)) / 3), n - 1] : [0]

    const onMove = (e: React.PointerEvent<SVGRectElement>) => {
        const rect = e.currentTarget.getBoundingClientRect()
        const px = ((e.clientX - rect.left) / rect.width) * iw
        setHover(Math.min(n - 1, Math.max(0, Math.round((px / iw) * (n - 1)))))
    }

    return (
        <div ref={ref} className="relative w-full">
            <svg width={w} height={h} className="block overflow-visible" role="img" aria-label={series.map((s) => s.name).join(", ")}>
                {ticks.map((t) => (
                    <g key={t}>
                        <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1}/>
                        <text x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-subtle font-mono text-[11.5px]">
                            {fmtTick(t)}
                        </text>
                    </g>
                ))}
                {xLabels.map((i) => (
                    <text key={i} x={x(i)} y={h - 6} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
                          className="fill-subtle font-mono text-[11.5px]">{n ? formatX(times[i]) : ""}</text>
                ))}
                {series.map((s) => {
                    const d = s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")
                    return (
                        <g key={s.name}>
                            {series.length === 1 && n > 1 && (
                                <path d={`${d}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z`} fill={s.color} opacity={0.1}/>
                            )}
                            <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"/>
                        </g>
                    )
                })}
                {hover != null && (
                    <g>
                        <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--border-strong)" strokeWidth={1}/>
                        {series.map((s) => (
                            <circle key={s.name} cx={x(hover)} cy={y(s.values[hover] ?? 0)} r={4} fill={s.color}
                                    stroke="var(--card)" strokeWidth={2}/>
                        ))}
                    </g>
                )}
                <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="transparent"
                      onPointerMove={onMove} onPointerLeave={() => setHover(null)}/>
            </svg>
            {hover != null && (
                <div className="pointer-events-none absolute top-2 z-10 min-w-36 rounded-lg border border-border-strong bg-card-raised/95 px-3 py-2 text-[12px] shadow-xl backdrop-blur"
                     style={{left: Math.min(x(hover) + 12, w - 170)}}>
                    <div className="mb-1 font-mono text-[11px] text-subtle">{formatX(times[hover])}</div>
                    {series.map((s) => (
                        <div key={s.name} className="flex items-center gap-2">
                            <span className="size-2 rounded-full" style={{background: s.color}}/>
                            <span className="flex-1 text-muted-foreground">{s.name}</span>
                            <span className="font-mono tabular text-foreground">{fmtTick(s.values[hover] ?? 0)}{unit}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

export function Legend({items}: { items: { name: string; color: string }[] }) {
    return (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {items.map((i) => (
                <span key={i.name} className="inline-flex items-center gap-2 text-[13.5px] text-muted-foreground">
                    <span className="h-[3px] w-3 rounded-full" style={{background: i.color}}/>
                    {i.name}
                </span>
            ))}
        </div>
    )
}

/** Горизонтальные полосы: значение на кончике, подсказка при наведении. */
export function BarList({rows, color = SERIES[0], format = fmtTick, empty = "Нет данных"}: {
    rows: { label: string; value: number; sub?: string }[]
    color?: string
    format?: (v: number) => string
    empty?: string
}) {
    const max = Math.max(1, ...rows.map((r) => r.value))
    if (!rows.length) return <p className="py-6 text-center text-[14px] text-subtle">{empty}</p>
    return (
        <div className="space-y-2.5">
            {rows.map((r) => (
                <div key={r.label} className="group" title={`${r.label}: ${format(r.value)}${r.sub ? ` · ${r.sub}` : ""}`}>
                    <div className="mb-1 flex items-baseline justify-between gap-3 text-[12.5px]">
                        <span className="truncate text-[14px] text-muted-foreground group-hover:text-foreground">{r.label}</span>
                        <span className="shrink-0 font-mono text-[14px] tabular text-foreground">{format(r.value)}</span>
                    </div>
                    <div className="h-2 rounded-full bg-muted/50">
                        <div className="h-2 rounded-full transition-[width] duration-500"
                             style={{width: `${Math.max(2, (r.value / max) * 100)}%`, background: color}}/>
                    </div>
                </div>
            ))}
        </div>
    )
}

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

/** Тепловая карта «день недели × час»: один оттенок (янтарь), от фона к насыщенному. */
export function Heatmap({values, label}: { values: number[][]; label: string }) {
    const max = Math.max(1, ...values.flat())
    const [hover, setHover] = useState<{ d: number; h: number } | null>(null)
    return (
        <div className="relative">
            <div className="overflow-x-auto">
                <div className="min-w-[560px]">
                    <div className="grid grid-cols-[28px_repeat(24,minmax(0,1fr))] gap-[2px]">
                        <span/>
                        {Array.from({length: 24}, (_, h) => (
                            <span key={h} className="text-center font-mono text-[9.5px] text-subtle">{h % 3 === 0 ? h : ""}</span>
                        ))}
                        {values.map((row, d) => (
                            <div key={d} className="contents">
                                <span className="pr-1 text-right font-mono text-[10.5px] leading-[18px] text-subtle">{DAYS[d]}</span>
                                {row.map((v, h) => (
                                    <span key={h}
                                          onPointerEnter={() => setHover({d, h})} onPointerLeave={() => setHover(null)}
                                          className="h-[18px] rounded-[3px] ring-amber/70 hover:ring-1"
                                          style={{background: v ? `color-mix(in oklch, var(--brand-amber) ${Math.round(12 + (v / max) * 88)}%, var(--card))` : "var(--muted)", opacity: v ? 1 : 0.35}}/>
                                ))}
                            </div>
                        ))}
                    </div>
                </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted-foreground">
                <span>{hover ? `${DAYS[hover.d]}, ${hover.h}:00–${hover.h + 1}:00 МСК — ${values[hover.d][hover.h]} ${label}` : "Наведите на клетку, чтобы увидеть число"}</span>
                <span className="flex items-center gap-1.5">реже
                    <span className="h-2 w-16 rounded-full" style={{background: "linear-gradient(90deg, color-mix(in oklch, var(--brand-amber) 12%, var(--card)), var(--brand-amber))"}}/>
                    чаще</span>
            </div>
        </div>
    )
}
