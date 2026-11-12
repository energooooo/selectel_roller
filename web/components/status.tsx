import {cn} from "@/lib/utils"

type Tone = "ok" | "warn" | "danger" | "info" | "muted" | "brand"

const TONES: Record<Tone, string> = {
    ok: "border-ok/30 bg-ok/10 text-ok",
    warn: "border-warn/30 bg-warn/10 text-warn",
    danger: "border-danger/35 bg-danger/10 text-danger",
    info: "border-info/30 bg-info/10 text-info",
    muted: "border-border-strong bg-card-raised text-muted-foreground",
    brand: "border-amber/40 bg-amber/10 text-amber",
}
const DOTS: Record<Tone, string> = {
    ok: "bg-ok", warn: "bg-warn", danger: "bg-danger", info: "bg-info", muted: "bg-subtle", brand: "bg-amber",
}

export function Badge({tone = "muted", live, children, className, title}: {
    tone?: Tone
    live?: boolean
    children: React.ReactNode
    className?: string
    title?: string
}) {
    return (
        <span title={title} className={cn(
            "inline-flex h-7 shrink-0 items-center gap-2 whitespace-nowrap rounded-full border px-3 text-[13px] font-medium",
            TONES[tone], className,
        )}>
            <span className="relative flex size-2">
                {live && <span className={cn("absolute inset-0 rounded-full animate-ping-soft", DOTS[tone])}/>}
                <span className={cn("relative size-2 rounded-full", DOTS[tone])}/>
            </span>
            {children}
        </span>
    )
}

/** Статус ключа: цвет + слово + пояснение (никогда не только цвет). */
export const KEY_STATUS: Record<string, { tone: Tone; label: string; live?: boolean; about: string }> = {
    starting: {tone: "info", label: "Запускается", live: true, about: "Входит в Selectel и готовит список подсетей."},
    hunting: {tone: "ok", label: "Ищет IP", live: true, about: "Прямо сейчас запрашивает адреса в ваших подсетях."},
    sleeping: {tone: "ok", label: "Ищет IP · пауза", live: true, about: "Обошёл все подсети и ждёт перед следующим проходом, чтобы Selectel не заблокировал аккаунт."},
    paused: {tone: "muted", label: "Остановлен", about: "Ключ не ищет IP. Нажмите «Запустить»."},
    error: {tone: "danger", label: "Ошибка", about: "Поиск остановлен из-за ошибки. Причина указана в карточке ключа."},
    banned: {tone: "danger", label: "Заблокирован Selectel", about: "Selectel временно запретил аккаунту получать IP за частые запросы. Обычно проходит за сутки."},
    auth_failed: {tone: "danger", label: "Не удаётся войти", about: "Selectel не принял номер аккаунта, имя пользователя или пароль."},
    done: {tone: "brand", label: "Готово", about: "Нужное количество IP найдено, поиск остановлен."},
}

export function KeyStatusBadge({status}: { status: string }) {
    const s = KEY_STATUS[status] ?? {tone: "muted" as Tone, label: status, about: ""}
    return <Badge tone={s.tone} live={s.live} title={s.about}>{s.label}</Badge>
}

export const PROXY_STATUS: Record<string, { tone: Tone; label: string; about: string }> = {
    ok: {tone: "ok", label: "Работает", about: "Прокси отвечает, все выбранные регионы Selectel доступны."},
    partial: {tone: "warn", label: "Частично", about: "Прокси отвечает, но часть регионов Selectel через него недоступна."},
    dead: {tone: "danger", label: "Не работает", about: "Прокси не отвечает. Ключи на нём автоматически пересядут на другой."},
    unchecked: {tone: "muted", label: "Проверяется", about: "Проверка идёт, результат появится через несколько секунд."},
}

export const CHECK_STATUS: Record<string, { tone: Tone; label: string }> = {
    ok: {tone: "ok", label: "Проверен"},
    bad: {tone: "danger", label: "Не прошёл проверку"},
    error: {tone: "warn", label: "Проверка не удалась"},
    pending: {tone: "muted", label: "Ждёт проверки"},
    skipped: {tone: "muted", label: "Не проверялся"},
}
