const TZ = "Europe/Moscow"

const timeFmt = new Intl.DateTimeFormat("ru-RU", {timeZone: TZ, hour: "2-digit", minute: "2-digit"})
const dateTimeFmt = new Intl.DateTimeFormat("ru-RU", {
    timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
})
const fullFmt = new Intl.DateTimeFormat("ru-RU", {
    timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
})
const numFmt = new Intl.NumberFormat("ru-RU")

export const fmtNum = (n: number | null | undefined) => (n == null ? "—" : numFmt.format(n))
const rubFmt = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 0})
const rubFmt2 = new Intl.NumberFormat("ru-RU", {minimumFractionDigits: 0, maximumFractionDigits: 2})
/** «12 340 ₽»; exact — с копейками, если они есть. */
export const fmtRub = (n: number | null | undefined, exact = false) =>
    n == null ? "—" : `${(exact ? rubFmt2 : rubFmt).format(n)} ₽`
const dateFmt = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", day: "numeric", month: "short", year: "numeric"})
export const fmtDate = (iso: string | null | undefined) => (iso ? dateFmt.format(new Date(iso)) : "—")
export const fmtTime = (iso: string | null | undefined) => (iso ? timeFmt.format(new Date(iso)) : "—")
export const fmtDateTime = (iso: string | null | undefined) => (iso ? dateTimeFmt.format(new Date(iso)) : "—")
export const fmtFull = (iso: string | null | undefined) => (iso ? fullFmt.format(new Date(iso)) : "—")

/** «5 мин назад», «вчера 14:20». */
export function fmtAgo(iso: string | null | undefined): string {
    if (!iso) return "никогда"
    const diff = (Date.now() - new Date(iso).getTime()) / 1000
    if (diff < 45) return "только что"
    if (diff < 3600) return `${Math.round(diff / 60)} мин назад`
    if (diff < 86400) return `${Math.round(diff / 3600)} ч назад`
    if (diff < 172800) return `вчера ${fmtTime(iso)}`
    return fmtDateTime(iso)
}

export function fmtDuration(seconds: number | null | undefined): string {
    if (seconds == null) return "—"
    const s = Math.max(0, Math.round(seconds))
    if (s < 60) return `${s} с`
    if (s < 3600) return `${Math.floor(s / 60)} мин${s % 60 ? ` ${s % 60} с` : ""}`
    const h = Math.floor(s / 3600)
    if (h < 48) return `${h} ч ${Math.floor((s % 3600) / 60)} мин`
    return `${Math.floor(h / 24)} д ${h % 24} ч`
}

export function plural(n: number, one: string, few: string, many: string): string {
    const m10 = n % 10, m100 = n % 100
    if (m10 === 1 && m100 !== 11) return one
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
    return many
}

/** Имя прокси для людей: своё название или «Прокси №N» — адреса у прокси одного провайдера одинаковые. */
export function proxyName(p: { id: number; label: string }): string {
    return p.label || `Прокси №${p.id}`
}
