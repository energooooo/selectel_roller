"use client"

import Link from "next/link"
import {type ChangeEvent, type FormEvent, useRef, useState} from "react"
import {MailCode} from "@/components/mail-code"
import {Button} from "@/components/ui/button"
import {Dialog, Field, Input, Note, Textarea} from "@/components/ui/primitives"
import {api, ApiError} from "@/lib/api"
import type {Key, ProxyItem} from "@/lib/types"
import {cn} from "@/lib/utils"

type Result = {kind: "done" | "payment" | "error"; message: string}
type Account = {accountId: string; email: string; result?: Result}

const STATUS = {done: "Подключён", payment: "Нужно пополнить", error: "Ошибка"}
const ACCOUNT_ID = /^[0-9]{3,12}$/
const SELECTEL_PANEL = "https://my.selectel.ru/login"
const HELP_CLASS = "underline underline-offset-4"

function parseAccounts(text: string): {accounts: Account[]; invalid: number} {
    const accounts: Account[] = []
    let invalid = 0
    for (const raw of text.split(/\r?\n/)) {
        const line = raw.trim()
        if (!line || line.startsWith("#")) continue
        const firstSeparator = line.indexOf("|")
        const email = firstSeparator < 0 ? "" : line.slice(0, firstSeparator).trim().replace(/^\d+(?:\\?[.)])?\s+/, "")
        const accountId = line.slice(line.lastIndexOf("|") + 1).trim().replace(/\\$/, "").trimEnd()
        if (!ACCOUNT_ID.test(accountId) || (firstSeparator >= 0 && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) {
            invalid++
            continue
        }
        accounts.push({accountId, email})
    }
    return {accounts, invalid}
}

export function AccountOnboarding({open, onOpenChange, proxies, onConnected}: {
    open: boolean; onOpenChange: (open: boolean) => void; proxies: ProxyItem[]; onConnected: () => void
}) {
    const [accounts, setAccounts] = useState<Account[]>([])
    const [selectedId, setSelectedId] = useState("")
    const [singleAccountId, setSingleAccountId] = useState("")
    const [importing, setImporting] = useState(true)
    const [importMessage, setImportMessage] = useState("")
    const [reading, setReading] = useState(false)
    const [busy, setBusy] = useState(false)
    const [singleResult, setSingleResult] = useState<Result>()
    const fileInput = useRef<HTMLInputElement>(null)
    const listInput = useRef<HTMLTextAreaElement>(null)
    const tokenInput = useRef<HTMLInputElement>(null)

    const working = proxies.filter((proxy) => proxy.status !== "dead")
    const selected = accounts.find((account) => account.accountId === selectedId)
    const accountId = selected?.accountId ?? singleAccountId
    const result = selected ? selected.result : singleResult
    const next = accounts.find((account) => account.accountId !== selectedId && account.result?.kind !== "done")
    const loginUrl = ACCOUNT_ID.test(accountId) ? SELECTEL_PANEL + "#toffee-account=" + accountId : SELECTEL_PANEL

    function addAccounts(text: string) {
        const parsed = parseAccounts(text)
        const knownIds = new Set(accounts.map(({accountId}) => accountId))
        const added = parsed.accounts.filter(({accountId}) => {
            if (knownIds.has(accountId)) return false
            knownIds.add(accountId)
            return true
        })
        const counts = ["Добавлено: " + added.length]
        const duplicateCount = parsed.accounts.length - added.length
        if (duplicateCount) counts.push("повторы: " + duplicateCount)
        if (parsed.invalid) counts.push("не распознано: " + parsed.invalid)
        setImportMessage(counts.join(" · "))
        if (!added.length) return
        setAccounts([...accounts, ...added])
        setSelectedId(selectedId || added[0].accountId)
        setImporting(false)
    }

    async function loadFiles(event: ChangeEvent<HTMLInputElement>) {
        const files = Array.from(event.currentTarget.files ?? [])
        event.currentTarget.value = ""
        if (!files.length) return
        setReading(true)
        try {
            addAccounts((await Promise.all(files.map((file) => file.text()))).join("\n"))
        } catch {
            setImportMessage("Не удалось прочитать TXT. Попробуйте вставить список вручную.")
        } finally {
            setReading(false)
        }
    }

    function updateResult(nextResult: Result) {
        if (selected) {
            setAccounts((rows) => rows.map((row) => row.accountId === selectedId ? {...row, result: nextResult} : row))
        } else {
            setSingleResult(nextResult)
        }
    }

    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const input = tokenInput.current
        if (busy || !input || !working.length) return
        const token = input.value.trim()
        if (!/^[!-~]+$/.test(token)) {
            updateResult({kind: "error", message: "Скопируйте IAM-токен целиком из панели Selectel."})
            return
        }
        const fields = new FormData(event.currentTarget)
        input.value = ""
        setBusy(true)
        try {
            const {key, added} = await api<{key: Key; added: boolean}>("/keys/onboard", {
                method: "POST",
                headers: {Authorization: "Bearer " + token},
                json: {
                    account_id: accountId,
                    project_name: String(fields.get("project_name") ?? "").trim(),
                },
            })
            updateResult({
                kind: "done",
                message: added ? `Ключ «${key.name}» добавлен на паузе.` : `Ключ «${key.name}» уже есть в панели.`,
            })
            onConnected()
        } catch (error) {
            if (error instanceof ApiError && error.status === 402) {
                input.value = token
                updateResult({kind: "payment", message: error.message})
            } else {
                updateResult({kind: "error", message: error instanceof ApiError ? error.message : "Проверьте связь с сервером."})
            }
        } finally {
            setBusy(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={(value) => {
            if (!busy && !reading) onOpenChange(value)
        }}
                title="Подключить аккаунты Selectel" className="sm:max-w-2xl"
                description={importing ? "Загрузите TXT или вставьте список аккаунтов." : undefined}>
            <div className="space-y-5">
                {importing ? (
                    <div className="space-y-3">
                        <Field label="Список аккаунтов" hint="Номер аккаунта или email | номер аккаунта. Пароли из TXT импортируйте в расширение Toffee Roller.">
                            <Textarea ref={listInput} placeholder="user@example.com | 123456" className="min-h-24"
                                      disabled={reading} autoComplete="off" spellCheck={false}/>
                        </Field>
                        <input ref={fileInput} type="file" accept=".txt,text/plain" multiple hidden onChange={loadFiles}/>
                        <div className="flex flex-wrap gap-2">
                            <Button type="button" size="sm" disabled={reading} onClick={() => {
                                if (!listInput.current) return
                                const text = listInput.current.value
                                listInput.current.value = ""
                                addAccounts(text)
                            }}>Добавить строки</Button>
                            <Button type="button" size="sm" variant="outline" disabled={reading}
                                    onClick={() => fileInput.current?.click()}>{reading ? "Читаем TXT…" : "Загрузить TXT"}</Button>
                            <Button type="button" size="sm" variant="ghost" disabled={reading}
                                    onClick={() => setImporting(false)}>{accounts.length ? "Вернуться к списку" : "Один аккаунт"}</Button>
                        </div>
                    </div>
                ) : (
                    <Button type="button" size="sm" variant="outline" disabled={busy}
                            onClick={() => setImporting(true)}>Добавить в список</Button>
                )}
                {importMessage && <p role="status" className="text-sm text-muted-foreground">{importMessage}</p>}
                {!!accounts.length && !importing && (
                    <ul aria-label="Аккаунты из списка" className="max-h-48 space-y-1.5 overflow-y-auto">
                        {accounts.map((account) => (
                            <li key={account.accountId}>
                                <button type="button" disabled={busy} aria-pressed={account.accountId === selectedId}
                                        onClick={() => setSelectedId(account.accountId)} className={cn(
                                            "flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-xl border px-3 py-2 text-left text-sm",
                                            account.accountId === selectedId ? "border-amber/70 bg-card-raised" : "border-border hover:bg-card-raised",
                                        )}>
                                    <span className="min-w-0 break-all">{account.email && account.email + " · "}{account.accountId}</span>
                                    <span className="text-muted-foreground">{account.result ? STATUS[account.result.kind] : "Ожидает подключения"}</span>
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
                {!importing && (result?.kind === "done" ? (
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <p role="status" className="text-sm text-ok">{result.message}</p>
                        {next && <>
                            <Button type="button" size="sm" onClick={() => setSelectedId(next.accountId)}>Следующий аккаунт</Button>
                            <p className="w-full text-sm text-muted-foreground">Перед входом в следующий аккаунт выйдите из текущего в Selectel.</p>
                        </>}
                        {!selected && <Button type="button" size="sm" onClick={() => {
                            setSingleAccountId("")
                            setSingleResult(undefined)
                        }}>Ещё аккаунт</Button>}
                    </div>
                ) : (
                    <form key={selectedId || "manual"} onSubmit={submit} aria-busy={busy} className="space-y-4">
                        <fieldset disabled={busy} className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
                            <Field label="Номер аккаунта">
                                <Input name="account_id" required inputMode="numeric" pattern="[0-9]{3,12}" maxLength={12}
                                       value={accountId} readOnly={!!selected} onChange={(event) => {
                                           setSingleAccountId(event.target.value)
                                           setSingleResult(undefined)
                                       }} className="font-mono"/>
                            </Field>
                            <Field label="Прокси для запросов Roller">
                                <Input readOnly value={"Случайный из доступных · " + working.length}/>
                            </Field>
                        </fieldset>
                        <div className="space-y-2">
                            <Button asChild variant="outline" size="sm">
                                <a href={loginUrl} target="_blank" rel="noopener noreferrer">Открыть Selectel</a>
                            </Button>
                            <p className="text-sm text-muted-foreground">
                                Если установлено расширение Toffee Roller, нажмите в нём «Подставить данные» во вкладке Selectel.
                                Пройдите CAPTCHA и подтвердите вход в Selectel.
                            </p>
                        </div>
                        {open && <details className="text-sm text-muted-foreground">
                            <summary className="mb-2 cursor-pointer">Получить почтовый код через MailyGet</summary>
                            <MailCode email={selected?.email}/>
                        </details>}
                        <fieldset disabled={busy} className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
                            <Field label="IAM-токен аккаунта" hint="В Selectel откройте «Профиль → Доступ → IAM-токен». Выберите область «Аккаунт».">
                                <Input ref={tokenInput} type="password" required autoComplete="off" spellCheck={false}/>
                            </Field>
                            <Field label="Проект в облаке" hint="По умолчанию используется имя toffee-roller с номером аккаунта.">
                                <Input name="project_name" maxLength={64} placeholder="Название проекта"/>
                            </Field>
                        </fieldset>
                        {!working.length && <p className="text-sm text-muted-foreground">Для подключения нужен доступный прокси. <Link className={HELP_CLASS} href="/proxies">Открыть «Прокси»</Link></p>}
                        {result?.kind === "payment" && (
                            <div role="status">
                                <Note tone="warn" title="Нужно пополнить баланс">
                                    <p>{result.message}</p>
                                    <p className="mt-2">В <a className={HELP_CLASS} href={loginUrl} target="_blank" rel="noopener noreferrer">Selectel</a> откройте «Биллинг → Обзор биллинга → Пополнить баланс».</p>
                                </Note>
                            </div>
                        )}
                        {result?.kind === "error" && <p role="alert" className="text-sm text-danger">{result.message}</p>}
                        <div className="flex justify-end">
                            <Button type="submit" disabled={busy || !working.length}>
                                {busy ? "Подключаем…" : result?.kind === "payment" ? "Проверить после оплаты" : "Подключить аккаунт"}
                            </Button>
                        </div>
                    </form>
                ))}
            </div>
        </Dialog>
    )
}
