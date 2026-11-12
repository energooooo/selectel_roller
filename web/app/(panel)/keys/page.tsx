"use client"

import {useEffect, useState} from "react"
import {mutate} from "swr"
import {toast} from "sonner"
import {Key, Plus, Warning} from "@phosphor-icons/react"
import {ExtensionConnect} from "@/components/extension-connect"
import {KeysTable} from "@/components/keys-table"
import {Button} from "@/components/ui/button"
import {
    Card, Dialog, Empty, Field, Input, Note, PageHeader, Segmented, Select, Skeleton, Textarea,
} from "@/components/ui/primitives"
import {ApiError, patch, post, useApi} from "@/lib/api"
import {proxyName} from "@/lib/format"
import type {Key as KeyT, ProxyItem} from "@/lib/types"

const PROXY_MODES = {
    auto: "Случайный с ротацией",
    fixed: "Выбрать вручную",
    none: "Без прокси",
} as const

async function refresh() {
    await Promise.allSettled([mutate("/keys"), mutate("/overview")])
}

function errorText(e: unknown) {
    return e instanceof ApiError ? e.message : "Не получилось — проверьте связь с сервером"
}

function WhereToGet() {
    return (
        <details className="mb-5 rounded-xl border border-border bg-card-raised/40 px-4 py-3 text-[14px]">
            <summary className="cursor-pointer select-none font-medium text-foreground marker:text-subtle">Где взять эти данные в Selectel</summary>
            <ol className="mt-3 list-decimal space-y-1.5 pl-5 leading-relaxed text-muted-foreground">
                <li>Номер аккаунта — цифры в правом верхнем углу панели my.selectel.ru.</li>
                <li>Откройте «Управление доступом» → «Сервисные пользователи» → «Добавить».</li>
                <li>Задайте имя и пароль, выдайте роль «Участник» в проекте облачной платформы.</li>
                <li>Введите номер аккаунта, имя и пароль ниже. Проект панель найдёт сама.</li>
            </ol>
        </details>
    )
}

function AddKeyDialog({open, onOpenChange, proxies}: { open: boolean; onOpenChange: (v: boolean) => void; proxies: ProxyItem[] }) {
    const [tab, setTab] = useState<"one" | "bulk">("one")
    const empty = {account_id: "", username: "", password: "", project_name: "", name: "", proxy_mode: "auto", proxy_id: ""}
    const [form, setForm] = useState(empty)
    const [bulk, setBulk] = useState("")
    const [busy, setBusy] = useState(false)
    const [results, setResults] = useState<{ line: number; ok: boolean; name?: string; error?: string }[]>([])
    const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({...form, [k]: e.target.value})
    const working = proxies.filter((p) => p.status !== "dead")

    const submitOne = async (e: React.FormEvent) => {
        e.preventDefault()
        setBusy(true)
        try {
            const res = await post<KeyT & { regions: string[] }>("/keys", {
                ...form, proxy_id: form.proxy_mode === "fixed" ? Number(form.proxy_id) || null : null, start: true,
            })
            toast.success(`Ключ «${res.name}» добавлен и уже ищет IP`, {description: `Проект ${res.project_name}, регионы ${res.regions.join(", ")}`})
            setForm(empty)
            onOpenChange(false)
            refresh()
        } catch (err) {
            toast.error(errorText(err))
        } finally {
            setBusy(false)
        }
    }

    const submitBulk = async () => {
        setBusy(true)
        try {
            const res = await post<typeof results>("/keys/bulk", {text: bulk, proxy_mode: "auto", start: true})
            setResults(res)
            const ok = res.filter((r) => r.ok).length
            toast[ok ? "success" : "error"](`Добавлено ключей: ${ok} из ${res.length}`)
            refresh()
        } catch (err) {
            toast.error(errorText(err))
        } finally {
            setBusy(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange} title="Добавить ключ Selectel" className="sm:max-w-xl"
                description="Панель проверит вход, найдёт или создаст проект в облаке, выберет прокси и сразу начнёт поиск.">
            <div className="mb-5">
                <Segmented value={tab} onChange={setTab} options={[["one", "Один ключ"], ["bulk", "Несколько сразу"]]}/>
            </div>
            {tab === "one" ? (
                <form onSubmit={submitOne}>
                    <WhereToGet/>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Номер аккаунта">
                            <Input value={form.account_id} onChange={set("account_id")} required inputMode="numeric" pattern="\d{3,12}"
                                   title="Только цифры" placeholder="Например, 694207" className="font-mono"/>
                        </Field>
                        <Field label="Сервисный пользователь">
                            <Input value={form.username} onChange={set("username")} required autoComplete="off" placeholder="Имя пользователя"/>
                        </Field>
                        <Field label="Пароль сервисного пользователя" className="sm:col-span-2" hint="Хранится в зашифрованном виде.">
                            <Input type="password" value={form.password} onChange={set("password")} required autoComplete="new-password"/>
                        </Field>
                        <Field label="Название в панели" hint="Необязательно. По умолчанию — имя пользователя.">
                            <Input value={form.name} onChange={set("name")} placeholder="Например, Основной"/>
                        </Field>
                        <Field label="Проект в облаке" hint="Необязательно. Пусто — возьмём существующий или создадим новый.">
                            <Input value={form.project_name} onChange={set("project_name")} placeholder="Название проекта"/>
                        </Field>
                        <Field label="Прокси" className={form.proxy_mode === "fixed" ? "" : "sm:col-span-2"}
                               help="В автоматическом режиме прокси меняется перед каждой попыткой поиска IP. Предыдущий исключается, если доступны другие."
                               hint={form.proxy_mode === "auto" ? `Случайный прокси перед каждой попыткой поиска (${working.length}).` : form.proxy_mode === "none" ? "Не рекомендуется: Selectel быстро блокирует частые запросы с одного адреса." : undefined}>
                            <Select value={form.proxy_mode} onChange={set("proxy_mode")}>
                                {Object.entries(PROXY_MODES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </Select>
                        </Field>
                        {form.proxy_mode === "fixed" && (
                            <Field label="Какой прокси">
                                <Select value={form.proxy_id} onChange={set("proxy_id")} required>
                                    <option value="">Выберите</option>
                                    {proxies.map((p) => <option key={p.id} value={p.id}>{proxyName(p)}{p.exit_ip && ` · ${p.exit_ip}`}</option>)}
                                </Select>
                            </Field>
                        )}
                    </div>
                    <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
                        <Button type="submit" disabled={busy}>
                            {busy ? "Проверяем вход в Selectel…" : <><Plus weight="bold"/> Добавить и запустить</>}
                        </Button>
                    </div>
                </form>
            ) : (
                <div>
                    <Field label="Ключи, по одному в строке"
                           hint="Номер аккаунта, имя пользователя, пароль и, если нужно, проект — через пробел. Прокси выберутся автоматически.">
                        <Textarea value={bulk} onChange={(e) => setBulk(e.target.value)} className="min-h-40"
                                  placeholder={"694207 Angeline пароль\n710039 Clarabelle пароль мой-проект"}/>
                    </Field>
                    {results.length > 0 && (
                        <ul className="mt-3 space-y-1 text-[14px]">
                            {results.map((r) => (
                                <li key={r.line} className={r.ok ? "text-ok" : "text-danger"}>
                                    Строка {r.line}: {r.ok ? `добавлен ключ «${r.name}»` : r.error}
                                </li>
                            ))}
                        </ul>
                    )}
                    <div className="mt-6 flex justify-end">
                        <Button onClick={submitBulk} disabled={busy || !bulk.trim()}>{busy ? "Добавляем…" : "Добавить все"}</Button>
                    </div>
                </div>
            )}
        </Dialog>
    )
}

function EditKeyDialog({k, proxies, onClose}: { k: KeyT | null; proxies: ProxyItem[]; onClose: () => void }) {
    const [form, setForm] = useState({name: "", password: "", proxy_mode: "auto", proxy_id: "", ip_goal: ""})
    const [busy, setBusy] = useState(false)
    useEffect(() => {
        if (k) setForm({name: k.name, password: "", proxy_mode: k.proxy_mode, proxy_id: String(k.proxy_id ?? ""), ip_goal: String(k.ip_goal ?? "")})
    }, [k])
    if (!k) return null
    const set = (f: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({...form, [f]: e.target.value})
    const save = async (e: React.FormEvent) => {
        e.preventDefault()
        setBusy(true)
        try {
            await patch(`/keys/${k.id}`, {
                name: form.name, password: form.password || undefined, proxy_mode: form.proxy_mode,
                proxy_id: form.proxy_mode === "fixed" ? Number(form.proxy_id) || null : null,
                ip_goal: form.ip_goal ? Number(form.ip_goal) : 0,
            })
            toast.success("Сохранено, изменения уже действуют")
            refresh()
            onClose()
        } catch (err) {
            toast.error(errorText(err))
        } finally {
            setBusy(false)
        }
    }
    return (
        <Dialog open={!!k} onOpenChange={(v) => !v && onClose()} title={`Настройки ключа «${k.name}»`}
                description={`Аккаунт ${k.account_id}, пользователь ${k.username}`}>
            <form onSubmit={save} className="space-y-5">
                <Field label="Название в панели"><Input value={form.name} onChange={set("name")}/></Field>
                <Field label="Новый пароль" hint="Оставьте пустым, чтобы не менять.">
                    <Input type="password" value={form.password} onChange={set("password")} autoComplete="new-password"/>
                </Field>
                <Field label="Сколько IP найти" hint="Когда на аккаунте будет столько адресов, поиск остановится. Пусто — как в общих настройках.">
                    <Input type="number" min={1} max={50} value={form.ip_goal} onChange={set("ip_goal")} placeholder="Как в настройках"/>
                </Field>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label="Прокси">
                        <Select value={form.proxy_mode} onChange={set("proxy_mode")}>
                            {Object.entries(PROXY_MODES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </Select>
                    </Field>
                    {form.proxy_mode === "fixed" && (
                        <Field label="Какой прокси">
                            <Select value={form.proxy_id} onChange={set("proxy_id")} required>
                                <option value="">Выберите</option>
                                {proxies.map((p) => <option key={p.id} value={p.id}>{proxyName(p)}{p.exit_ip && ` · ${p.exit_ip}`}</option>)}
                            </Select>
                        </Field>
                    )}
                </div>
                <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end">
                    <Button type="button" variant="ghost" onClick={onClose}>Отмена</Button>
                    <Button type="submit" disabled={busy}>Сохранить</Button>
                </div>
            </form>
        </Dialog>
    )
}

export default function KeysPage() {
    const {data: keys, error, mutate: reloadKeys} = useApi<KeyT[]>("/keys", {refreshInterval: 20_000})
    const {data: proxies} = useApi<ProxyItem[]>("/proxies")
    const [adding, setAdding] = useState(false)
    const [extension, setExtension] = useState(false)
    const [editing, setEditing] = useState<KeyT | null>(null)

    useEffect(() => {
        if (new URLSearchParams(window.location.search).get("add")) setAdding(true)
    }, [])

    return (
        <>
            <PageHeader title="Ключи"
                        description="Поиск IP на аккаунтах Selectel"
                        action={
                            <>
                                <Button variant="outline" size="sm" className="max-[374px]:px-3" onClick={() => setAdding(true)}>Добавить ключ</Button>
                                <Button size="sm" className="max-[374px]:px-3" aria-label="Подключить аккаунты" onClick={() => setExtension(true)}>
                                    <Plus weight="bold"/> <span>Подключить<span className="max-[374px]:hidden"> аккаунты</span></span>
                                </Button>
                            </>
                        }/>
            {proxies && !proxies.some((p) => p.status !== "dead") && keys?.some((k) => k.proxy_mode !== "none") && (
                <Note tone="warn" className="mb-4" title="Нет рабочих прокси">
                    Ключи с автоматическим выбором прокси не начнут поиск. Добавьте прокси во вкладке «Прокси».
                </Note>
            )}
            {!keys ? error ? (
                <Card>
                    <Empty icon={<Warning className="size-6"/>} title="Не удалось загрузить ключи"
                           action={<Button variant="outline" onClick={() => void reloadKeys().catch(() => undefined)}>Повторить</Button>}>
                        {errorText(error)}
                    </Empty>
                </Card>
            ) : (
                <Card className="space-y-3 p-4" aria-busy="true" aria-label="Загрузка ключей">
                    <Skeleton className="mb-5 h-10 w-full rounded-xl"/>
                    {Array.from({length: 6}, (_, i) => <Skeleton key={i} className="h-10 rounded-lg"/>)}
                </Card>
            ) : keys.length ? (
                <KeysTable keys={keys} onEdit={setEditing} refresh={refresh}/>
            ) : (
                <Card>
                    <Empty icon={<Key weight="duotone" className="size-6"/>} title="Пока нет ни одного ключа"
                           action={<Button onClick={() => setAdding(true)}><Plus weight="bold"/> Добавить первый ключ</Button>}>
                        Подключите аккаунт Selectel по IAM-токену или добавьте готового сервисного пользователя.
                    </Empty>
                </Card>
            )}
            <AddKeyDialog open={adding} onOpenChange={setAdding} proxies={proxies ?? []}/>
            <ExtensionConnect open={extension} onOpenChange={setExtension}/>
            <EditKeyDialog k={editing} proxies={proxies ?? []} onClose={() => setEditing(null)}/>
        </>
    )
}
