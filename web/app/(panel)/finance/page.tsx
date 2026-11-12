"use client"

import {useMemo, useState} from "react"
import {toast} from "sonner"
import {mutate} from "swr"
import {ArrowsClockwise, Coins, DownloadSimple, PencilSimple, Plus, Trash, Wallet} from "@phosphor-icons/react"
import {ConfirmDialog} from "@/components/confirm"
import {SALE_KINDS, SALE_LABEL, SaleDialog} from "@/components/sale-dialog"
import {Badge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, CardHeader, Empty, Hint, Mono, PageHeader, Segmented, Skeleton, Tip} from "@/components/ui/primitives"
import {ApiError, del, post, useApi} from "@/lib/api"
import {fmtAgo, fmtDateTime, fmtRub} from "@/lib/format"
import type {AccountMoney, FinanceSummary, Sale, SaleKind, Settings} from "@/lib/types"
import {cn} from "@/lib/utils"
import {niceScale} from "@/components/charts"

const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

/** Одна ячейка полосы показателей: подпись над числом, без плитки и иконки. lead — ответ экрана, крупнее остальных. */
function Stat({label, help, value, sub, tone, lead, wide}: {
    wide?: boolean
    label: string
    help: string
    value: React.ReactNode
    sub: React.ReactNode
    tone?: "ok" | "danger"
    lead?: boolean
}) {
    return (
        <div className={cn("min-w-0 px-4 py-4 sm:px-6 sm:py-5", wide && "col-span-2 sm:col-span-1")}>
            <span className="flex items-center gap-1.5 text-[14px] text-muted-foreground">{label}<Hint>{help}</Hint></span>
            <div className={cn("mt-2 font-semibold leading-none tracking-[-0.03em] tabular",
                lead ? "text-[34px]" : "text-[24px]",
                tone === "ok" && "text-ok", tone === "danger" && "text-danger")}>{value}</div>
            <div className="mt-2 text-[13px] text-muted-foreground">{sub}</div>
        </div>
    )
}

const tick = new Intl.NumberFormat("ru-RU", {notation: "compact", maximumFractionDigits: 1})

function MonthBars({months}: { months: FinanceSummary["months"] }) {
    // Пока доходов нет — шкала до 1 000 ₽, чтобы не было «0 / 0,5 / 1 ₽».
    const {max, ticks} = niceScale(Math.max(...months.map((m) => m.income)) || 999, 3)
    const [hover, setHover] = useState<number | null>(null)
    const shown = hover ?? months.length - 1
    const m = months[shown]
    return (
        <div>
            <p className="text-[14px] text-muted-foreground" aria-live="polite">
                {m ? <>{MONTHS[Number(m.month.slice(5)) - 1]} {m.month.slice(0, 4)}: <span className="font-mono text-foreground">{fmtRub(m.income)}</span></> : "—"}
            </p>
            <div className="mt-4 flex gap-2">
                <div className="relative h-40 flex-1">
                    {ticks.map((t) => (
                        <div key={t} className="pointer-events-none absolute inset-x-0 border-t border-border/70" style={{bottom: `${(t / max) * 100}%`}}/>
                    ))}
                    <div className="relative flex h-full items-end gap-1.5 sm:gap-2" onMouseLeave={() => setHover(null)}>
                        {months.map((row, i) => (
                            <button key={row.month} type="button" onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                                    aria-label={`${row.month}: ${fmtRub(row.income)}`}
                                    className="group flex h-full flex-1 flex-col justify-end outline-none focus-visible:ring-2 focus-visible:ring-amber/60">
                                <span className={cn("block w-full rounded-t-[3px] transition-colors duration-150 ease-[cubic-bezier(.2,.8,.2,1)]",
                                    i === shown ? "bg-amber" : "bg-amber/40 group-hover:bg-amber/70")}
                                      style={{height: row.income ? `${(row.income / max) * 100}%` : 0}}/>
                            </button>
                        ))}
                    </div>
                </div>
                <div className="relative h-40 w-10 shrink-0 font-mono text-[11.5px] text-subtle tabular">
                    {ticks.map((t) => (
                        <span key={t} className="absolute right-0 translate-y-1/2 leading-none" style={{bottom: `${(t / max) * 100}%`}}>{tick.format(t)}</span>
                    ))}
                </div>
            </div>
            <div className="mt-2 flex gap-1.5 pr-12 sm:gap-2">
                {months.map((row) => (
                    <span key={row.month} className="flex-1 text-center text-[11.5px] text-subtle">{MONTHS[Number(row.month.slice(5)) - 1]}</span>
                ))}
            </div>
        </div>
    )
}

function AccountsTable({rows: all, warnDays}: { rows: AccountMoney[]; warnDays: number }) {
    const [showAll, setShowAll] = useState(false)
    // Аккаунты без баланса и без адресов ничего не говорят о деньгах — свёрнуты в одну строку.
    const useful = all.filter((r) => r.balance != null || r.ips > 0 || r.vds > 0)
    const hidden = all.length - useful.length
    const rows = showAll ? all : useful
    if (!all.length) {
        return <Empty icon={<Wallet weight="duotone" className="size-6"/>} title="Нет аккаунтов">Добавьте ключ Selectel — здесь появится его баланс и расходы.</Empty>
    }
    return (
        <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-[14px]">
                <thead>
                <tr className="border-y border-border text-left text-[13px] text-muted-foreground">
                    <th className="py-3 pl-5 pr-4 font-medium sm:pl-6">Аккаунт</th>
                    <th className="px-4 text-right font-medium">Баланс</th>
                    <th className="px-4 font-medium"><span className="inline-flex items-center gap-1.5">Хватит на<Hint>По прогнозу Selectel, а если он недоступен — баланс, делённый на расход панели в сутки.</Hint></span></th>
                    <th className="px-4 pr-5 text-right font-medium sm:pr-6"><span className="inline-flex items-center gap-1.5">Держим<Hint>Найденные адреса и серверы под ними, которые сейчас на аккаунте, и их цена по прайсу Selectel.</Hint></span></th>
                </tr>
                </thead>
                <tbody>
                {rows.map((r) => {
                    const low = r.days_left != null && warnDays > 0 && r.days_left < warnDays
                    return (
                        <tr key={r.account_id} className="border-b border-border/60 last:border-0">
                            <td className="py-3 pl-5 pr-4 sm:pl-6">
                                <span className="block font-medium">{r.keys.join(", ") || "Без ключа"}</span>
                                <Mono className="text-[12.5px] text-subtle">№ {r.account_id}</Mono>
                            </td>
                            <td className="px-4 text-right">
                                {r.error && r.balance == null ? (
                                    <span className="inline-flex items-center gap-1.5 text-[13px] text-warn">
                                        {r.error.includes("биллинг") ? "Нет доступа" : "Не получено"}<Hint>{r.error}</Hint>
                                    </span>
                                ) :<Mono className="text-[15px] text-foreground">{fmtRub(r.balance, true)}</Mono>}
                            </td>
                            <td className="px-4">
                                {r.days_left == null ? <span className="text-subtle">—</span> : (
                                    <Badge tone={low ? "danger" : r.days_left < 14 ? "warn" : "ok"}>
                                        {r.days_left >= 365 ? "больше года" : `${r.days_left.toLocaleString("ru")} дн.`}
                                    </Badge>
                                )}
                            </td>
                            <td className="px-4 pr-5 text-right tabular sm:pr-6">
                                <span className="block">{r.ips} IP{r.vds ? ` · ${r.vds} сервер` : ""}</span>
                                <span className="text-[12.5px] text-muted-foreground">{fmtRub(r.month)}/мес</span>
                            </td>
                        </tr>
                    )
                })}
                {hidden > 0 && (
                    <tr>
                        <td colSpan={4} className="px-5 py-2.5 sm:px-6">
                            <button type="button" onClick={() => setShowAll(!showAll)}
                                    className="min-h-11 text-[14px] text-muted-foreground underline-offset-4 transition-colors duration-150 hover:text-foreground hover:underline">
                                {showAll ? "Скрыть аккаунты без данных" : `Ещё ${hidden} без доступа к балансу и без адресов — показать`}
                            </button>
                        </td>
                    </tr>
                )}
                </tbody>
            </table>
        </div>
    )
}

function SalesTable({sales, onEdit, onDelete}: { sales: Sale[]; onEdit: (s: Sale) => void; onDelete: (s: Sale) => void }) {
    return (
        <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-[14px]">
                <thead>
                <tr className="border-y border-border text-left text-[13px] text-muted-foreground">
                    <th className="py-3 pl-5 pr-4 font-medium sm:pl-6">Когда</th>
                    <th className="px-4 font-medium">Что</th>
                    <th className="px-4 font-medium">Покупатель</th>
                    <th className="px-4 font-medium">Адрес</th>
                    <th className="px-4 text-right font-medium">Сумма</th>
                    <th className="w-24 pr-5 sm:pr-6"/>
                </tr>
                </thead>
                <tbody>
                {sales.map((s) => (
                    <tr key={s.id} className="border-b border-border/60 transition-colors duration-150 last:border-0 hover:bg-card-raised/40">
                        <td className="whitespace-nowrap py-3 pl-5 pr-4 text-muted-foreground sm:pl-6">{fmtDateTime(s.ts)}</td>
                        <td className="px-4">
                            <span className="block">{SALE_LABEL[s.kind] ?? s.kind}</span>
                            {s.note && <span className="block max-w-[240px] truncate text-[12.5px] text-subtle" title={s.note}>{s.note}</span>}
                        </td>
                        <td className="px-4">{s.buyer || <span className="text-subtle">—</span>}</td>
                        <td className="px-4">{s.ip ? <Mono className="text-foreground">{s.ip}</Mono> : <span className="text-subtle">—</span>}</td>
                        <td className="px-4 text-right"><Mono className="text-[15px] text-ok">+{fmtRub(s.amount, true)}</Mono></td>
                        <td className="pr-5 sm:pr-6">
                            <div className="flex justify-end gap-1">
                                <Tip label="Изменить запись">
                                    <button onClick={() => onEdit(s)} aria-label={`Изменить запись от ${fmtDateTime(s.ts)}`}
                                            className="grid size-10 place-items-center rounded-xl text-subtle transition-colors duration-150 hover:bg-card-raised hover:text-amber">
                                        <PencilSimple className="size-[18px]"/>
                                    </button>
                                </Tip>
                                <Tip label="Удалить запись">
                                    <button onClick={() => onDelete(s)} aria-label={`Удалить запись от ${fmtDateTime(s.ts)}`}
                                            className="grid size-10 place-items-center rounded-xl text-subtle transition-colors duration-150 hover:bg-danger/10 hover:text-danger">
                                        <Trash className="size-[18px]"/>
                                    </button>
                                </Tip>
                            </div>
                        </td>
                    </tr>
                ))}
                </tbody>
            </table>
        </div>
    )
}

export default function FinancePage() {
    const {data} = useApi<FinanceSummary>("/finance/summary")
    const {data: sales} = useApi<Sale[]>("/sales")
    const {data: settings} = useApi<Settings>("/settings")
    const [kind, setKind] = useState<SaleKind | "all">("all")
    const [editing, setEditing] = useState<Sale | null>(null)
    const [adding, setAdding] = useState(false)
    const [deleting, setDeleting] = useState<Sale | null>(null)
    const [refreshing, setRefreshing] = useState(false)

    const shown = useMemo(() => (sales ?? []).filter((s) => kind === "all" || s.kind === kind), [sales, kind])
    const warnDays = Number(settings?.balance_warn_days ?? 3)

    const refresh = async () => {
        setRefreshing(true)
        try {
            await post("/finance/balances/refresh")
            await mutate("/finance/summary")
            toast.success("Балансы обновлены")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        } finally {
            setRefreshing(false)
        }
    }

    const delta = data ? data.income_month - data.income_prev_month : 0
    const low = data?.balances.filter((b) => b.days_left != null && warnDays > 0 && b.days_left < warnDays) ?? []

    return (
        <>
            <PageHeader title="Финансы"
                        description="Сколько приносят найденные адреса и во что обходится их удержание. Балансы аккаунтов обновляются сами раз в час."
                        action={<>
                            <Button variant="outline" onClick={refresh} disabled={refreshing}>
                                <ArrowsClockwise className={refreshing ? "animate-spin" : ""}/> Обновить балансы
                            </Button>
                            <Button onClick={() => setAdding(true)}><Plus weight="bold"/> Записать доход</Button>
                        </>}/>

            {!data ? <Skeleton className="h-36 rounded-2xl"/> : (
                <>
                    {low.length > 0 && (
                        <div className="mb-4 rounded-2xl border border-danger/30 bg-danger/[0.07] px-5 py-4 text-[14.5px] leading-relaxed">
                            <span className="font-medium text-foreground">Скоро кончатся деньги: </span>
                            <span className="text-muted-foreground">
                                {low.map((b) => `${b.keys[0] ?? b.account_id} (${b.days_left?.toLocaleString("ru")} дн.)`).join(", ")}.
                                Пополните баланс, иначе Selectel заберёт адреса.
                            </span>
                        </div>
                    )}
                    <Card className="grid grid-cols-2 gap-px overflow-hidden bg-border xl:grid-cols-4 [&>*]:bg-card">
                        <Stat lead wide label="Прибыль за месяц" help="Доход за месяц минус расход в месяц на удержание адресов и серверов."
                              value={fmtRub(data.profit_month)} tone={data.profit_month > 0 ? "ok" : data.profit_month < 0 ? "danger" : undefined}
                              sub={`За всё время получено ${fmtRub(data.income_total)}`}/>
                        <Stat label="Доход за месяц" help="Сумма записей журнала с первого числа текущего месяца, по Москве."
                              value={fmtRub(data.income_month)}
                              sub={!data.income_prev_month && !data.income_month ? "Записей пока нет"
                                  : !data.income_prev_month ? `+${fmtRub(data.income_month)} (в прошлом месяце не было)`
                                      : <>{delta >= 0 ? "+" : "−"}{fmtRub(Math.abs(delta))} к прошлому месяцу</>}/>
                        <Stat label="Расход в месяц" help="Цена по прайсу Selectel за адреса и серверы, которые сейчас держит панель."
                              value={fmtRub(data.cost_month)} sub={`${data.ips_held} IP · ${fmtRub(data.cost_day, true)} в сутки`}/>
                        <Stat wide label="На балансах" help="Сумма балансов аккаунтов Selectel, где у пользователя ключа есть доступ к биллингу."
                              value={fmtRub(data.balance_total)}
                              sub={`Известен у ${data.balances.filter((b) => b.balance != null).length} из ${data.balances.length} аккаунтов`}/>
                    </Card>

                    <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
                        <Card>
                            <CardHeader title="Аккаунты Selectel" hint="Баланс, на сколько хватит и что панель на нём держит."
                                        action={<span className="text-[13px] text-muted-foreground">
                                            Обновлено {fmtAgo(data.balances.map((b) => b.checked_at).filter(Boolean).sort().at(-1) ?? null)}
                                        </span>}/>
                            <div className="mt-4"><AccountsTable rows={data.balances} warnDays={warnDays}/></div>
                        </Card>
                        <Card className="p-5 sm:p-6">
                            <h2 className="text-[16px] font-semibold tracking-[-0.01em]">Доход по месяцам</h2>
                            <div className="mt-3"><MonthBars months={data.months}/></div>
                            {data.by_kind.length > 0 && (
                                <ul className="mt-6 space-y-2 border-t border-border/60 pt-4">
                                    {data.by_kind.map((k) => (
                                        <li key={k.kind} className="flex items-center justify-between text-[14px]">
                                            <span className="text-muted-foreground">{k.label}</span>
                                            <Mono className="text-foreground">{fmtRub(k.amount)}</Mono>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                    </div>
                </>
            )}

            <Card className="mt-4">
                <CardHeader title="Журнал доходов" hint="Каждая продажа или оплата аренды. Привязка к адресу показывает доход в его карточке."
                            action={
                                <Button variant="outline" size="sm" asChild>
                                    <a href="/api/sales.csv"><DownloadSimple/> Скачать CSV</a>
                                </Button>
                            }/>
                <div className="px-5 pt-4 sm:px-6">
                    <Segmented size="sm" value={kind} onChange={setKind}
                               options={[["all", "Все"], ...SALE_KINDS] as [SaleKind | "all", string][]}/>
                </div>
                <div className="mt-4">
                    {!sales ? <div className="p-5"><Skeleton className="h-32"/></div> : shown.length ? (
                        <SalesTable sales={shown} onEdit={setEditing} onDelete={setDeleting}/>
                    ) : (
                        <Empty icon={<Coins weight="duotone" className="size-6"/>} title={sales.length ? "Таких записей нет" : "Доходов пока нет"}
                               action={!sales.length ? <Button onClick={() => setAdding(true)}><Plus weight="bold"/> Записать первый доход</Button> : undefined}>
                            {sales.length ? "Выберите другой тип." : "Продали или сдали адрес — запишите здесь, и панель посчитает прибыль за вычетом удержания."}
                        </Empty>
                    )}
                </div>
            </Card>

            <SaleDialog open={adding || editing != null} sale={editing}
                        onOpenChange={(v) => {
                            if (!v) {
                                setAdding(false)
                                setEditing(null)
                            }
                        }}/>
            {deleting && (
                <ConfirmDialog open onOpenChange={(v) => !v && setDeleting(null)} danger confirmLabel="Удалить запись"
                               title={`Удалить запись на ${fmtRub(deleting.amount, true)}?`}
                               description="Сумма пропадёт из дохода и прибыли. Отменить нельзя."
                               onConfirm={async () => {
                                   try {
                                       await del(`/sales/${deleting.id}`)
                                       mutate((k) => typeof k === "string" && (k.startsWith("/sales") || k.startsWith("/finance")))
                                       toast.success("Запись удалена")
                                   } catch (e) {
                                       toast.error(e instanceof ApiError ? e.message : "Не получилось")
                                   }
                               }}/>
            )}
        </>
    )
}
