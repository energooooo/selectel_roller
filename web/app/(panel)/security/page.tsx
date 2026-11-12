"use client"

import {useState} from "react"
import QRCode from "qrcode"
import {toast} from "sonner"
import {Desktop, ShieldCheck, ShieldWarning, X} from "@phosphor-icons/react"
import {Badge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, CardHeader, Dialog, Field, Input, Mono, PageHeader, Skeleton} from "@/components/ui/primitives"
import {ApiError, del, post, useApi} from "@/lib/api"
import {fmtAgo, fmtDateTime} from "@/lib/format"

interface Me { login: string; role: "owner" | "user"; totp_enabled: boolean; backup_codes_left: number; password_changed_at: string }
interface SessionRow { id: number; ip: string; user_agent: string; created_at: string; last_seen_at: string; current: boolean }
interface Attempt { id: number; ts: string; ip: string; login: string; ok: boolean; reason: string }
interface Audit { id: number; ts: string; ip: string; action: string; target: string }

const REASONS: Record<string, string> = {
    ok: "Вход", setup: "Первый вход", bad_credentials: "Неверный логин или пароль", bad_code: "Неверный код", bad_setup_token: "Неверный код создания",
    registered: "Заявка на доступ", status_pending: "Вход до одобрения заявки", status_rejected: "Вход по отклонённой заявке",
    status_blocked: "Вход заблокированного",
}

function browser(ua: string) {
    const b = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : ua.split(" ")[0] || "?"
    const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : ""
    return [b, os].filter(Boolean).join(" · ")
}

function TotpSetup({onDone}: { onDone: () => void }) {
    const [secret, setSecret] = useState<{ secret: string; uri: string } | null>(null)
    const [qr, setQr] = useState("")
    const [code, setCode] = useState("")
    const [codes, setCodes] = useState<string[] | null>(null)
    const start = async () => {
        const res = await post<{ secret: string; uri: string }>("/auth/totp/setup")
        setSecret(res)
        setQr(await QRCode.toDataURL(res.uri, {margin: 1, width: 220, color: {dark: "#0B0908", light: "#F6F1EC"}}))
    }
    const enable = async () => {
        try {
            const res = await post<{ backup_codes: string[] }>("/auth/totp/enable", {code})
            setCodes(res.backup_codes)
            toast.success("Вход по коду включён")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        }
    }
    if (codes) {
        return (
            <div>
                <p className="text-[13px] text-muted-foreground">Запасные коды нужны, если телефона не будет под рукой. Каждый срабатывает один раз. Сохраните их в менеджер паролей — больше они не покажутся.</p>
                <div className="mt-3 grid grid-cols-2 gap-2 rounded-xl border border-border bg-input p-3">
                    {codes.map((c) => <Mono key={c} className="text-[14px] text-foreground">{c}</Mono>)}
                </div>
                <div className="mt-4 flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => navigator.clipboard.writeText(codes.join("\n")).then(() => toast.success("Скопировано"))}>Копировать</Button>
                    <Button size="sm" onClick={onDone}>Готово</Button>
                </div>
            </div>
        )
    }
    if (!secret) return <Button size="sm" onClick={start}>Показать QR-код</Button>
    return (
        <div className="grid gap-5 sm:grid-cols-[220px_1fr]">
            {qr && <img src={qr} alt="QR-код для приложения-аутентификатора" className="rounded-xl" width={220} height={220}/>}
            <div>
                <p className="text-[13px] text-muted-foreground">Отсканируйте QR-код в Google Authenticator, Aegis или 2FAS — или введите ключ вручную:</p>
                <Mono className="mt-2 block break-all rounded-lg border border-border bg-input px-3 py-2 text-foreground">{secret.secret}</Mono>
                <Field label="Код из приложения" className="mt-4">
                    <Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" className="font-mono tracking-[0.2em]" placeholder="123456"/>
                </Field>
                <Button size="sm" className="mt-3" onClick={enable} disabled={code.length < 6}>Подтвердить и включить</Button>
            </div>
        </div>
    )
}

export default function SecurityPage() {
    const {data: me, mutate: reloadMe} = useApi<Me>("/auth/me")
    const {data: sessions, mutate: reloadSessions} = useApi<SessionRow[]>("/auth/sessions")
    // Журналы входов и изменений — у владельца панели (там все пользователи).
    const isOwner = me?.role === "owner"
    const {data: attempts} = useApi<Attempt[]>(isOwner ? "/auth/attempts?limit=50" : null)
    const {data: audit} = useApi<Audit[]>(isOwner ? "/auth/audit?limit=50" : null)
    const [totpOpen, setTotpOpen] = useState(false)
    const [disableOpen, setDisableOpen] = useState(false)
    const [pw, setPw] = useState({current: "", next: "", next2: ""})
    const [off, setOff] = useState({password: "", code: ""})


    const changePassword = async (e: React.FormEvent) => {
        e.preventDefault()
        if (pw.next !== pw.next2) return toast.error("Новые пароли не совпадают")
        try {
            await post("/auth/password", {current_password: pw.current, new_password: pw.next})
            setPw({current: "", next: "", next2: ""})
            toast.success("Пароль изменён, остальные сессии завершены")
            reloadSessions()
        } catch (err) {
            toast.error(err instanceof ApiError ? err.message : "Не получилось")
        }
    }

    return (
        <>
            <PageHeader title="Безопасность" description="Пароль, вход по коду из приложения, устройства с открытой панелью и история входов."/>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Card className="p-5">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <h2 className="text-[17px] font-semibold">Вход по коду из приложения</h2>
                            <p className="mt-1.5 text-[14px] leading-relaxed text-muted-foreground">Кроме пароля панель будет спрашивать шестизначный код из Google Authenticator, Aegis или 2FAS.</p>
                        </div>
                        {me ? (me.totp_enabled ? <Badge tone="ok">Включён</Badge> : <Badge tone="warn">Выключен</Badge>) : null}
                    </div>
                    {me && (
                        <div className="mt-4 flex items-center gap-3">
                            {me.totp_enabled ? (
                                <>
                                    <ShieldCheck weight="duotone" className="size-8 text-ok"/>
                                    <span className="flex-1 text-[14px] text-muted-foreground">Запасных кодов осталось: {me.backup_codes_left}</span>
                                    <Button variant="outline" size="sm" onClick={() => setDisableOpen(true)}>Выключить</Button>
                                </>
                            ) : (
                                <>
                                    <ShieldWarning weight="duotone" className="size-8 text-warn"/>
                                    <span className="flex-1 text-[14px] text-muted-foreground">Сейчас панель защищена только паролем.</span>
                                    <Button size="sm" onClick={() => setTotpOpen(true)}>Включить</Button>
                                </>
                            )}
                        </div>
                    )}
                </Card>

                <Card className="p-5">
                    <h2 className="text-[17px] font-semibold">Пароль</h2>
                    <p className="mt-1.5 text-[14px] text-muted-foreground">Логин <Mono className="text-foreground">{me?.login}</Mono> · пароль менялся {fmtAgo(me?.password_changed_at)}. После смены все остальные устройства выйдут из панели.</p>
                    <form onSubmit={changePassword} className="mt-5 grid grid-cols-1 gap-3">
                        <Input type="password" placeholder="Текущий пароль" aria-label="Текущий пароль" value={pw.current} onChange={(e) => setPw({...pw, current: e.target.value})} autoComplete="current-password" required/>
                        <Input type="password" placeholder="Новый пароль, от 12 символов" aria-label="Новый пароль" value={pw.next} onChange={(e) => setPw({...pw, next: e.target.value})} autoComplete="new-password" required/>
                        <Input type="password" placeholder="Новый пароль ещё раз" aria-label="Новый пароль ещё раз" value={pw.next2} onChange={(e) => setPw({...pw, next2: e.target.value})} autoComplete="new-password" required/>
                        <div><Button size="sm" type="submit">Сменить пароль</Button></div>
                    </form>
                </Card>
            </div>

            <Card className="mt-4">
                <CardHeader title="Где открыта панель" hint="Если видите незнакомое устройство — завершите его сеанс и смените пароль."/>
                <div className="p-5 pt-3">
                    {!sessions ? <Skeleton className="h-16"/> : (
                        <ul className="divide-y divide-border/60">
                            {sessions.map((s) => (
                                <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
                                    <Desktop className="size-5 text-subtle"/>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2 text-[14px]">{browser(s.user_agent)} {s.current && <Badge tone="brand">Это устройство</Badge>}</div>
                                        <p className="text-[13px] text-muted-foreground">IP <Mono>{s.ip}</Mono> · вход {fmtDateTime(s.created_at)} · был активен {fmtAgo(s.last_seen_at)}</p>
                                    </div>
                                    {!s.current && (
                                        <Button variant="ghost" size="sm" onClick={async () => {
                                            await del(`/auth/sessions/${s.id}`)
                                            reloadSessions()
                                        }}><X/> Завершить сеанс</Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </Card>

            {isOwner && <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Card>
                    <CardHeader title="История входов" hint="Удачные и неудачные попытки войти."/>
                    <div className="max-h-[420px] overflow-y-auto p-5 pt-3">
                        {attempts?.map((a) => (
                            <div key={a.id} className="flex items-center gap-3 border-b border-border/60 py-2 text-[13px] last:border-0">
                                <span aria-label={a.ok ? "Удачно" : "Неудачно"} className={a.ok ? "size-2 shrink-0 rounded-full bg-ok" : "size-2 shrink-0 rounded-full bg-danger"}/>
                                <span className="w-24 shrink-0 font-mono text-[12px] text-subtle">{fmtDateTime(a.ts)}</span>
                                <Mono className="w-32 shrink-0 truncate text-muted-foreground">{a.ip}</Mono>
                                <span className="truncate">{REASONS[a.reason] ?? a.reason} <span className="text-subtle">{a.login}</span></span>
                            </div>
                        ))}
                    </div>
                </Card>
                <Card>
                    <CardHeader title="История изменений" hint="Что и когда меняли в панели."/>
                    <div className="max-h-[420px] overflow-y-auto p-5 pt-3">
                        {audit?.length ? audit.map((a) => (
                            <div key={a.id} className="flex items-center gap-3 border-b border-border/60 py-2 text-[13px] last:border-0">
                                <span className="w-24 shrink-0 font-mono text-[12px] text-subtle">{fmtDateTime(a.ts)}</span>
                                <Mono className="shrink-0 text-amber">{a.action}</Mono>
                                <span className="truncate text-muted-foreground">{a.target}</span>
                            </div>
                        )) : <p className="py-4 text-center text-[13px] text-subtle">Пока пусто</p>}
                    </div>
                </Card>
            </div>}

            <Dialog open={totpOpen} onOpenChange={setTotpOpen} title="Включить вход по коду" className="sm:max-w-2xl">
                <TotpSetup onDone={() => {
                    setTotpOpen(false)
                    reloadMe()
                }}/>
            </Dialog>
            <Dialog open={disableOpen} onOpenChange={setDisableOpen} title="Выключить вход по коду?" description="Введите пароль и текущий код из приложения.">
                <div className="space-y-3">
                    <Input type="password" placeholder="Пароль" aria-label="Пароль" value={off.password} onChange={(e) => setOff({...off, password: e.target.value})}/>
                    <Input placeholder="Код из приложения" aria-label="Код из приложения" value={off.code} onChange={(e) => setOff({...off, code: e.target.value})} className="font-mono"/>
                    <div className="flex justify-end">
                        <Button variant="danger" size="sm" onClick={async () => {
                            try {
                                await post("/auth/totp/disable", off)
                                setDisableOpen(false)
                                setOff({password: "", code: ""})
                                reloadMe()
                                toast.success("Вход по коду выключен")
                            } catch (e) {
                                toast.error(e instanceof ApiError ? e.message : "Не получилось")
                            }
                        }}>Выключить</Button>
                    </div>
                </div>
            </Dialog>
        </>
    )
}
