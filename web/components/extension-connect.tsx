"use client"

import {type DragEvent, useEffect, useMemo, useRef, useState} from "react"
import {toast} from "sonner"
import {
    ArrowUpRight, Check, CircleNotch, FileText, MagnifyingGlass, Plus, PuzzlePiece, Trash, WarningCircle, X,
} from "@phosphor-icons/react"
import {ExtensionPairing} from "@/components/extension-pairing"
import {Button} from "@/components/ui/button"
import {Dialog, Segmented} from "@/components/ui/primitives"
import {parseAccountImport} from "@/lib/account-import"
import {api, ApiError, del, useApi} from "@/lib/api"
import {fmtAgo, plural} from "@/lib/format"
import {cn} from "@/lib/utils"

type Account = {
    id: number; account_id: string; email: string; note: string
    onboarded: boolean; has_password: boolean; last_login_at: string | null
    login_stage: string; login_message: string; login_at: string | null
}
type ExtToken = { id: number; label: string; created_at: string; last_seen_at: string; expired: boolean }
type Filter = "all" | "wait" | "done"

// /login#blank — чистая форма входа нового аккаунта (не выбор уже добавленных,
// как /login-sessions#new). Добавляет аккаунт новой сессией, из прошлой выходить
// не нужно; номер кладём в query, расширение его подхватит.
const SELECTEL_LOGIN = "https://my.selectel.ru/login"
const loginUrl = (accountId: string) => SELECTEL_LOGIN + "?toffee-account=" + accountId + "#blank"

const STALE_MS = 10 * 60 * 1000

// ── Состояние входа одной строки ────────────────────────────────────────────
// Шаги, которые расширение присылает из вкладки Selectel, сведены к четырём.
const STEPS = ["Данные", "Код из почты", "В панель", "Готово"]
const STEP_OF: Record<string, number> = {
    filling: 0, login: 0, filled: 1, otp_sent: 1, otp_wait: 1, code: 2, verifying: 2, connecting: 2, done: 4,
}

type State = { kind: "done" | "error" | "active" | "idle"; text: string; step: number }

function stateOf(a: Account): State {
    const stage = a.login_stage
    if (stage === "done" || (a.onboarded && !stage)) {
        return {kind: "done", step: 4, text: a.login_message || "Подключён"}
    }
    if (stage === "error") return {kind: "error", step: -1, text: a.login_message || "Вход не удался"}
    if (stage in STEP_OF) {
        const fresh = a.login_at && Date.now() - new Date(a.login_at).getTime() < STALE_MS
        if (fresh) return {kind: "active", step: STEP_OF[stage], text: a.login_message || "Идёт вход"}
        return {kind: "idle", step: -1, text: "Вход не завершён " + fmtAgo(a.login_at)}
    }
    return {kind: "idle", step: -1, text: a.has_password ? "Ждёт входа" : "Нет пароля — добавьте его в TXT заново"}
}

function StateIcon({state}: { state: State }) {
    const base = "grid size-9 shrink-0 place-items-center rounded-full border"
    if (state.kind === "done") return <span className={cn(base, "border-ok/35 bg-ok/10 text-ok")}><Check weight="bold" className="size-4"/></span>
    if (state.kind === "error") return <span className={cn(base, "border-danger/35 bg-danger/10 text-danger")}><WarningCircle weight="bold" className="size-[18px]"/></span>
    if (state.kind === "active") return <span className={cn(base, "border-amber/40 bg-amber/10 text-amber")}><CircleNotch weight="bold" className="size-4 animate-spin"/></span>
    return <span className={cn(base, "border-border-strong bg-card-raised text-subtle")}><span className="size-1.5 rounded-full bg-current"/></span>
}

function StepBar({step}: { step: number }) {
    return (
        <ol className="mt-3 grid grid-cols-4 gap-1.5 pl-12" aria-label="Шаги входа">
            {STEPS.map((label, i) => (
                <li key={label} className="min-w-0" aria-current={i === step ? "step" : undefined}>
                    <span className={cn(
                        "block h-1 overflow-hidden rounded-full",
                        i < step ? "bg-amber" : "bg-border-strong",
                    )}>
                        {i === step && <span className="block h-full w-1/2 rounded-full bg-amber/80 animate-[stepflow_1.4s_ease-in-out_infinite]"/>}
                    </span>
                    <span className={cn("mt-1.5 block truncate text-[12px]", i <= step ? "text-foreground/85" : "text-subtle")}>{label}</span>
                </li>
            ))}
        </ol>
    )
}

/** Удаление в два нажатия прямо в строке — без второго окна поверх этого. */
function DeleteButton({onDelete}: { onDelete: () => Promise<void> }) {
    const [armed, setArmed] = useState(false)
    const [busy, setBusy] = useState(false)
    useEffect(() => {
        if (!armed) return
        const t = setTimeout(() => setArmed(false), 3500)
        return () => clearTimeout(t)
    }, [armed])
    if (armed) {
        return (
            <Button type="button" size="sm" variant="danger" disabled={busy} className="px-3" onClick={async () => {
                setBusy(true)
                try {
                    await onDelete()
                } finally {
                    setBusy(false)
                    setArmed(false)
                }
            }}>{busy ? "Удаляем…" : "Удалить?"}</Button>
        )
    }
    return (
        <button type="button" aria-label="Удалить аккаунт" title="Удалить аккаунт" onClick={() => setArmed(true)}
                className="grid size-10 shrink-0 place-items-center rounded-xl text-subtle transition-colors duration-150 hover:bg-danger/10 hover:text-danger">
            <Trash className="size-[18px]"/>
        </button>
    )
}

function AccountRow({account, onDelete}: { account: Account; onDelete: () => Promise<void> }) {
    const state = stateOf(account)
    const tone = {done: "text-ok", error: "text-danger", active: "text-amber", idle: "text-muted-foreground"}[state.kind]
    return (
        <li className={cn(
            "rounded-xl border bg-card px-3 py-3 transition-colors duration-150 sm:px-3.5",
            state.kind === "active" ? "border-amber/35" : "border-border hover:border-border-strong",
        )}>
            <div className="flex items-center gap-3">
                <StateIcon state={state}/>
                <div className="min-w-0 flex-1">
                    <p className="truncate text-[14.5px] font-medium">
                        {account.email || <span className="text-muted-foreground">Без почты</span>}
                    </p>
                    {/* На телефоне статус — отдельной строкой, чтобы не резался до пары слов. */}
                    <p className="mt-0.5 flex min-w-0 flex-col text-[13px] sm:flex-row sm:items-center sm:gap-1.5">
                        <span className="shrink-0 font-mono tabular text-muted-foreground">№ {account.account_id}</span>
                        <span aria-hidden className="hidden text-subtle sm:inline">·</span>
                        <span className={cn("truncate", tone)} title={state.text}>{state.text}</span>
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <Button type="button" size="sm" variant={state.kind === "done" ? "ghost" : "outline"}
                            disabled={!account.has_password} className="w-10 px-0 sm:w-auto sm:px-4"
                            aria-label={(state.kind === "error" ? "Войти ещё раз: " : "Войти: ") + (account.email || account.account_id)}
                            title={account.has_password ? "Откроет вход Selectel в новой вкладке" : "Нет пароля"}
                            onClick={() => window.open(loginUrl(account.account_id), "_blank", "noopener")}>
                        <span className="hidden sm:inline">{state.kind === "error" ? "Ещё раз" : "Войти"}</span>
                        <ArrowUpRight className="size-4"/>
                    </Button>
                    <DeleteButton onDelete={onDelete}/>
                </div>
            </div>
            {state.kind === "active" && <StepBar step={state.step}/>}
        </li>
    )
}

// ── Импорт ──────────────────────────────────────────────────────────────────
function ImportBox({onImported, autoFocus}: { onImported: () => void; autoFocus?: boolean }) {
    const [text, setText] = useState("")
    const [drag, setDrag] = useState(false)
    const [busy, setBusy] = useState(false)
    const [result, setResult] = useState<{ tone: "ok" | "danger"; text: string } | null>(null)
    const fileInput = useRef<HTMLInputElement>(null)
    const parsed = useMemo(() => parseAccountImport(text), [text])

    async function importText(source: string, fromFile: boolean) {
        const {rows, invalid} = parseAccountImport(source)
        if (!rows.length) {
            setResult({tone: "danger", text: "Аккаунты не распознаны. Проверьте номера и формат списка."})
            return
        }
        setBusy(true)
        try {
            const {added, updated} = await api<{ added: number; updated: number }>("/accounts/import", {
                method: "POST", json: {accounts: rows},
            })
            const parts = [`Добавлено ${added}`]
            if (updated) parts.push(`обновлено ${updated}`)
            if (invalid) parts.push(`не распознано ${invalid}`)
            setResult({tone: "ok", text: parts.join(" · ")})
            if (!fromFile) setText("")
            onImported()
        } catch (error) {
            setResult({tone: "danger", text: error instanceof ApiError ? error.message : "Не удалось импортировать аккаунты."})
        } finally {
            setBusy(false)
        }
    }

    async function readFiles(files: File[]) {
        const txt = files.filter((f) => f.type === "text/plain" || /\.txt$/i.test(f.name))
        if (!txt.length) {
            setResult({tone: "danger", text: "Нужен файл .txt."})
            return
        }
        try {
            await importText((await Promise.all(txt.map((f) => f.text()))).join("\n\n"), true)
        } catch {
            setResult({tone: "danger", text: "Не удалось прочитать файл."})
        }
    }

    function onDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault()
        setDrag(false)
        void readFiles(Array.from(event.dataTransfer.files))
    }

    const summary = !text.trim()
        ? "Вставьте список или перетащите TXT сюда"
        : parsed.rows.length
            ? `Готово к импорту: ${parsed.rows.length}` + (parsed.invalid ? ` · с ошибкой: ${parsed.invalid}` : "")
            : "Аккаунты не распознаны"

    return (
        <div>
            <div onDragOver={(e) => {
                e.preventDefault()
                setDrag(true)
            }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
                 className={cn(
                     "relative rounded-xl border border-dashed bg-input/60 transition-[border-color,background-color,box-shadow] duration-150",
                     "focus-within:border-solid focus-within:border-amber/70 focus-within:ring-[3px] focus-within:ring-amber/15",
                     drag ? "border-amber bg-amber/[0.06]" : "border-border-strong hover:border-subtle",
                 )}>
                <label htmlFor="accounts-import" className="sr-only">Строки аккаунтов</label>
                <textarea id="accounts-import" value={text} onChange={(e) => {
                    setText(e.target.value)
                    setResult(null)
                }} disabled={busy} autoFocus={autoFocus} autoComplete="off" spellCheck={false} rows={3}
                          placeholder={"user@mail.ru | пароль | 123456\nsecond@mail.ru | пароль | 654321"}
                          className="block max-h-56 min-h-[92px] w-full resize-y bg-transparent px-4 pt-3.5 pb-2 font-mono text-[13.5px] leading-relaxed outline-none placeholder:text-subtle"/>
                <div className="flex flex-wrap items-center gap-2 border-t border-dashed border-border-strong px-3 py-2.5">
                    <p className={cn("mr-auto min-w-0 text-[13px]", text.trim() && !parsed.rows.length ? "text-warn" : "text-muted-foreground")}>{summary}</p>
                    <input ref={fileInput} type="file" accept=".txt,text/plain" multiple hidden onChange={(e) => {
                        const files = Array.from(e.currentTarget.files ?? [])
                        e.currentTarget.value = ""
                        if (files.length) void readFiles(files)
                    }}/>
                    <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => fileInput.current?.click()}>
                        <FileText className="size-4"/> Выбрать TXT
                    </Button>
                    <Button type="button" size="sm" disabled={busy || !parsed.rows.length} onClick={() => importText(text, false)}>
                        {busy ? <><CircleNotch className="size-4 animate-spin"/> Импортируем…</>
                            : parsed.rows.length ? `Импортировать ${parsed.rows.length}` : "Импортировать"}
                    </Button>
                </div>
                {drag && (
                    <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl bg-background/70 text-[14px] font-medium text-amber">
                        Отпустите — импортируем файл
                    </div>
                )}
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
                Поддерживаются строки <span className="font-mono text-foreground/85">email | пароль | номер аккаунта</span> и блоки с полями «Почта», «Логин», «Пароль». Пароли шифруются и уходят только в расширение.
            </p>
            {result && (
                <p role="status" className={cn(
                    "mt-2 inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[13.5px] animate-in fade-in-0 slide-in-from-top-1 duration-200",
                    result.tone === "ok" ? "border-ok/30 bg-ok/[0.07] text-ok" : "border-danger/30 bg-danger/[0.07] text-danger",
                )}>
                    {result.tone === "ok" ? <Check weight="bold" className="size-4"/> : <WarningCircle weight="bold" className="size-4"/>}
                    {result.text}
                </p>
            )}
        </div>
    )
}

// ── Статус расширения ───────────────────────────────────────────────────────
function ExtensionStrip({tokens, onPair}: { tokens: ExtToken[] | undefined; onPair: () => void }) {
    if (!tokens) return <div className="h-[66px] animate-pulse rounded-xl bg-muted/40"/>
    const live = tokens.filter((t) => !t.expired)
    const latest = live.reduce<ExtToken | null>((a, t) => (!a || t.last_seen_at > a.last_seen_at ? t : a), null)
    if (latest) {
        return (
            <div className="flex items-center gap-3 rounded-xl border border-border bg-card-raised/40 px-3.5 py-3">
                <span className="relative grid size-9 shrink-0 place-items-center rounded-lg border border-ok/30 bg-ok/10 text-ok">
                    <PuzzlePiece weight="fill" className="size-[18px]"/>
                </span>
                <div className="min-w-0 flex-1">
                    <p className="text-[14.5px] font-medium">Расширение подключено</p>
                    <p className="truncate text-[13px] text-muted-foreground">
                        {live.length > 1 ? `${live.length} ${plural(live.length, "браузер", "браузера", "браузеров")}` : latest.label}
                        {" · на связи "}{fmtAgo(latest.last_seen_at)}
                    </p>
                </div>
                <Button type="button" size="sm" variant="ghost" onClick={onPair} className="px-3" aria-label="Подключить ещё браузер">
                    <Plus className="size-4"/><span className="hidden sm:inline">Ещё браузер</span>
                </Button>
            </div>
        )
    }
    return (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber/30 bg-amber/[0.06] px-3.5 py-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-amber/35 bg-amber/10 text-amber">
                <PuzzlePiece weight="fill" className="size-[18px]"/>
            </span>
            <div className="min-w-0 flex-1">
                <p className="text-[14.5px] font-medium">Сначала подключите расширение</p>
                <p className="text-[13px] text-muted-foreground">Без него «Войти» не подставит пароль и код из почты.</p>
            </div>
            <Button type="button" size="sm" onClick={onPair} className="w-full sm:w-auto">Подключить расширение</Button>
        </div>
    )
}

const HOW = [
    {title: "Нажмите «Войти»", text: "Selectel откроется новой сессией — из прошлого аккаунта выходить не нужно."},
    {title: "Пройдите капчу", text: "И нажмите «Войти» в Selectel. Код из почты MailyGet расширение введёт само."},
    {title: "Ключ в панели", text: "Аккаунт подключится сам. Шаги видны в его строке здесь."},
]

export function ExtensionConnect({open, onOpenChange}: { open: boolean; onOpenChange: (open: boolean) => void }) {
    const {data: accounts, mutate} = useApi<Account[]>(open ? "/accounts" : null)
    const {data: tokens} = useApi<ExtToken[]>(open ? "/extension/tokens" : null)
    const [view, setView] = useState<"accounts" | "pair">("accounts")
    const [adding, setAdding] = useState(false)
    const [filter, setFilter] = useState<Filter>("all")
    const [query, setQuery] = useState("")

    // Каждое открытие — с экрана аккаунтов.
    useEffect(() => {
        if (open) setView("accounts")
    }, [open])

    const all = accounts ?? []
    const doneCount = all.filter((a) => stateOf(a).kind === "done").length
    const shown = all.filter((a) => {
        const kind = stateOf(a).kind
        if (filter === "done" && kind !== "done") return false
        if (filter === "wait" && kind === "done") return false
        const q = query.trim().toLowerCase()
        return !q || a.account_id.includes(q) || a.email.toLowerCase().includes(q)
    })
    const showImport = accounts !== undefined && (all.length === 0 || adding)
    // Подсказка «как войти» — пока ни один аккаунт не подключён; дальше она только занимает место.
    const showHow = accounts !== undefined && doneCount === 0

    async function removeAccount(accountId: string) {
        try {
            await del(`/accounts/${accountId}`)
            await mutate()
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : "Не удалось удалить аккаунт.")
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange} className="sm:max-w-[760px]"
                title={view === "pair" ? "Подключение расширения" : "Аккаунты Selectel"}
                description={view === "pair"
                    ? "Один раз на браузер. Введите в расширении адрес панели и код — дальше оно работает само."
                    : "Загрузите список, и вход станет в одно нажатие: расширение подставит пароль и код из почты, а ключ появится в панели."}>
            {view === "pair" ? <ExtensionPairing onBack={() => setView("accounts")}/> : (
                <div className="space-y-5">
                    <ExtensionStrip tokens={tokens} onPair={() => setView("pair")}/>

                    {showImport && (
                        <section aria-label="Импорт аккаунтов" className="animate-in fade-in-0 duration-200">
                            {all.length > 0 && (
                                <div className="mb-2 flex items-center justify-between">
                                    <h3 className="text-[15px] font-semibold">Добавить аккаунты</h3>
                                    <button type="button" aria-label="Свернуть импорт" onClick={() => setAdding(false)}
                                            className="grid size-10 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-card-raised hover:text-foreground">
                                        <X className="size-4"/>
                                    </button>
                                </div>
                            )}
                            <ImportBox autoFocus={all.length > 0} onImported={() => {
                                void mutate()
                                if (all.length > 0) setAdding(false)
                            }}/>
                        </section>
                    )}

                    {accounts === undefined && (
                        <div className="space-y-2">{Array.from({length: 3}, (_, i) => <div key={i} className="h-[62px] animate-pulse rounded-xl bg-muted/40"/>)}</div>
                    )}

                    {all.length > 0 && (
                        <section aria-label="Аккаунты в панели" className="space-y-3">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-auto text-[15px] font-semibold">
                                    Аккаунты <span className="font-normal tabular text-muted-foreground">{all.length}</span>
                                </h3>
                                {all.length > 3 && (
                                    <Segmented size="sm" value={filter} onChange={setFilter} options={[
                                        ["all", "Все"],
                                        ["wait", `Ждут входа · ${all.length - doneCount}`],
                                        ["done", `Подключены · ${doneCount}`],
                                    ] as const}/>
                                )}
                                {!adding && (
                                    <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
                                        <Plus className="size-4"/> Добавить
                                    </Button>
                                )}
                            </div>
                            {all.length > 8 && (
                                <label className="relative block">
                                    <span className="sr-only">Поиск по почте или номеру</span>
                                    <MagnifyingGlass className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-subtle"/>
                                    <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Почта или номер аккаунта"
                                           className="h-10 w-full rounded-xl border border-border-strong bg-input pl-10 pr-3.5 text-[14px] outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-subtle hover:border-subtle focus:border-amber/70 focus:ring-[3px] focus:ring-amber/15"/>
                                </label>
                            )}
                            {/* Список прокручивается сам, а окно целиком остаётся на экране — без двух полос прокрутки. */}
                            <ul className={cn(
                                "space-y-1.5 overflow-y-auto overscroll-contain pr-0.5 [scrollbar-width:thin]",
                                showHow ? "max-h-[max(200px,calc(92dvh-500px))]" : "max-h-[max(240px,calc(92dvh-360px))]",
                            )}>
                                {shown.map((account) => (
                                    <AccountRow key={account.id} account={account}
                                                onDelete={() => removeAccount(account.account_id)}/>
                                ))}
                                {!shown.length && (
                                    <li className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-[14px] text-muted-foreground">
                                        Ничего не найдено
                                    </li>
                                )}
                            </ul>
                        </section>
                    )}

                    {showHow && <ol className="grid gap-2 border-t border-border pt-5 sm:grid-cols-3">
                        {HOW.map((step, i) => (
                            <li key={step.title} className="flex gap-3 sm:block">
                                <span className="font-mono text-[12px] leading-[22px] text-amber">0{i + 1}</span>
                                <div className="min-w-0">
                                    <p className="text-[14px] font-medium sm:mt-1">{step.title}</p>
                                    <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{step.text}</p>
                                </div>
                            </li>
                        ))}
                    </ol>}
                </div>
            )}
        </Dialog>
    )
}
