"use client"

import {useEffect, useRef, useState} from "react"
import {useRouter} from "next/navigation"
import {ArrowRight, Key, LockSimple, ShieldCheck, Sparkle} from "@phosphor-icons/react"
import {Logo} from "@/components/logo"
import {Button} from "@/components/ui/button"
import {Field, Input} from "@/components/ui/primitives"
import {ApiError, api, post} from "@/lib/api"

type Mode = "loading" | "setup" | "login" | "code"

export default function LoginPage() {
    const router = useRouter()
    const [mode, setMode] = useState<Mode>("loading")
    const [login, setLogin] = useState("")
    const [password, setPassword] = useState("")
    const [password2, setPassword2] = useState("")
    const [setupToken, setSetupToken] = useState("")
    const [code, setCode] = useState("")
    const [error, setError] = useState("")
    const [busy, setBusy] = useState(false)
    const codeRef = useRef<HTMLInputElement>(null)

    useEffect(() => {
        api<{ needs_setup: boolean; authenticated: boolean }>("/auth/state")
            .then((s) => {
                if (s.authenticated) router.replace("/")
                else setMode(s.needs_setup ? "setup" : "login")
            })
            .catch(() => {
                setMode("login")
                setError("Сервер панели не отвечает. Проверьте, что он запущен.")
            })
    }, [router])

    useEffect(() => {
        if (mode === "code") codeRef.current?.focus()
    }, [mode])

    const submit = async (e: React.FormEvent) => {
        e.preventDefault()
        setError("")
        if (mode === "setup" && password !== password2) {
            setError("Пароли не совпадают")
            return
        }
        setBusy(true)
        try {
            if (mode === "setup") {
                await post("/auth/setup", {setup_token: setupToken, login, password})
                router.replace("/")
                return
            }
            const res = await post<{ ok: boolean; need_code?: boolean }>("/auth/login", {login, password, code})
            if (res.need_code) {
                setMode("code")
            } else {
                router.replace("/")
            }
        } catch (err) {
            setError(err instanceof ApiError ? err.message : "Нет связи с сервером панели")
        } finally {
            setBusy(false)
        }
    }

    const title = mode === "setup" ? "Первый запуск" : mode === "code" ? "Код из приложения" : "Вход в панель"
    const subtitle = mode === "setup"
        ? "Создайте учётную запись администратора. Код создания лежит на сервере в файле backend/data/setup-token.txt и в его логе."
        : mode === "code"
            ? "Введите 6 цифр из приложения-аутентификатора или один из запасных кодов."
            : "Toffee Roller — нужный IP в Selectel без ручного перебора."

    return (
        <main className="relative grid min-h-dvh place-items-center overflow-hidden px-4 py-10">
            <div aria-hidden className="pointer-events-none absolute inset-0">
                <div className="animate-drift absolute left-1/2 top-[38%] h-[520px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--brand-amber)_22%,transparent),transparent)] blur-2xl"/>
                <div className="absolute inset-0 bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] bg-[size:56px_56px] opacity-[0.18] [mask-image:radial-gradient(ellipse_at_center,black_30%,transparent_75%)]"/>
            </div>

            <div className="animate-rise relative w-full max-w-[400px]">
                <div className="mb-8 flex flex-col items-center gap-5 text-center">
                    <Logo className="scale-110"/>
                    <div>
                        <h1 className="text-[28px] font-semibold tracking-[-0.03em]">{title}</h1>
                        <p className="mt-1.5 text-sm text-muted-foreground">{subtitle}</p>
                    </div>
                </div>

                <form onSubmit={submit} className="ring-brand rounded-2xl bg-card/90 p-6 shadow-[0_30px_80px_-30px_rgba(0,0,0,0.8)] backdrop-blur">
                    {mode === "loading" && (
                        <div className="grid h-40 place-items-center">
                            <div className="size-7 animate-spin rounded-full border-2 border-border-strong border-t-amber"/>
                        </div>
                    )}

                    {mode === "setup" && (
                        <Field label="Код создания" className="mb-4">
                            <Input value={setupToken} onChange={(e) => setSetupToken(e.target.value)} required
                                   autoComplete="off" className="font-mono" placeholder="Из файла setup-token.txt"/>
                        </Field>
                    )}

                    {(mode === "setup" || mode === "login") && (
                        <div className="space-y-4">
                            <Field label="Логин">
                                <Input value={login} onChange={(e) => setLogin(e.target.value)} required minLength={3}
                                       autoComplete="username" autoFocus={mode === "login"} placeholder="Ваш логин"/>
                            </Field>
                            <Field label="Пароль" hint={mode === "setup" ? "Не короче 12 символов и без логина внутри." : undefined}>
                                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                                       autoComplete={mode === "setup" ? "new-password" : "current-password"}
                                       placeholder="••••••••••••"/>
                            </Field>
                            {mode === "setup" && (
                                <Field label="Повторите пароль">
                                    <Input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)}
                                           required autoComplete="new-password" placeholder="••••••••••••"/>
                                </Field>
                            )}
                        </div>
                    )}

                    {mode === "code" && (
                        <Field label="Код">
                            <Input ref={codeRef} value={code} onChange={(e) => setCode(e.target.value)} required
                                   inputMode="numeric" autoComplete="one-time-code" placeholder="123 456"
                                   className="h-12 text-center font-mono text-xl tracking-[0.3em]"/>
                        </Field>
                    )}

                    {error && (
                        <p role="alert" className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                            {error}
                        </p>
                    )}

                    {mode !== "loading" && (
                        <Button type="submit" className="mt-6 w-full" disabled={busy}>
                            {busy ? (
                                <span className="size-4 animate-spin rounded-full border-2 border-[#140D08]/30 border-t-[#140D08]"/>
                            ) : mode === "setup" ? <Sparkle weight="bold"/> : mode === "code" ? <Key weight="bold"/> : <LockSimple weight="bold"/>}
                            {mode === "setup" ? "Создать и войти" : mode === "code" ? "Подтвердить вход" : "Войти"}
                            {!busy && <ArrowRight weight="bold" className="ml-auto"/>}
                        </Button>
                    )}

                    <p className="mt-4 flex items-start gap-2 text-[12.5px] leading-relaxed text-subtle">
                        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-amber" weight="duotone"/>
                        После 5 неверных паролей вход с этого адреса закрывается на 15 минут. О входе с нового адреса придёт сообщение в Telegram.
                    </p>
                </form>
            </div>
        </main>
    )
}
