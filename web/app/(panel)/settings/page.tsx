"use client"

import {useEffect, useState} from "react"
import {toast} from "sonner"
import {CheckCircle, FloppyDisk, PaperPlaneTilt} from "@phosphor-icons/react"
import {Button} from "@/components/ui/button"
import {Card, Checkbox, Field, Hint, Input, Mono, Note, PageHeader, Skeleton, Switch} from "@/components/ui/primitives"
import {ApiError, post, put, useApi} from "@/lib/api"
import type {Settings} from "@/lib/types"
import {cn} from "@/lib/utils"

const REGIONS = [
    ["ru-1", "Санкт-Петербург"],
    ["ru-2", "Москва"],
    ["ru-3", "Санкт-Петербург"],
    ["ru-9", "Москва"],
] as const

/** Готовые режимы темпа. «Обычно» соответствует настройкам сервера по умолчанию. */
const PRESETS = [
    {
        id: "safe", title: "Бережно", text: "Пауза 15–20 секунд между запросами и минута между проходами. Для новых аккаунтов и после блокировки.",
        values: {busy_delay_min: 15, busy_delay_max: 20, round_delay_min: 60, round_delay_max: 90, long_pause_every: 20, long_pause_min: 600, long_pause_max: 900},
    },
    {
        id: "normal", title: "Обычно", text: "Пауза 5 секунд между запросами и 2–3 минуты между проходами. Режим по умолчанию.",
        values: {busy_delay_min: 5, busy_delay_max: 5, round_delay_min: 120, round_delay_max: 180, long_pause_every: 0, long_pause_min: 720, long_pause_max: 1080},
    },
    {
        id: "fast", title: "Быстрее", text: "Пауза 5–7 секунд между запросами. Selectel может заблокировать аккаунт на сутки.",
        values: {busy_delay_min: 5, busy_delay_max: 7, round_delay_min: 15, round_delay_max: 25, long_pause_every: 0, long_pause_min: 720, long_pause_max: 1080},
    },
] as const

type Form = Record<string, unknown>

function presetOf(form: Form): string | null {
    for (const p of PRESETS) {
        if (Object.entries(p.values).every(([k, v]) => Number(form[k]) === v)) return p.id
    }
    return null
}

function Section({title, text, children}: { title: string; text?: React.ReactNode; children: React.ReactNode }) {
    return (
        <Card className="p-5 sm:p-6">
            <h2 className="text-[17px] font-semibold tracking-[-0.01em]">{title}</h2>
            {text && <p className="mt-1.5 text-[14px] leading-relaxed text-muted-foreground">{text}</p>}
            <div className="mt-5">{children}</div>
        </Card>
    )
}

function Row({label, help, hint, children}: { label: string; help?: string; hint?: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-col gap-2 border-b border-border/60 py-3.5 first:pt-0 last:border-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
            <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-[15px]">{label}{help && <Hint>{help}</Hint>}</div>
                {hint && <div className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{hint}</div>}
            </div>
            <div className="shrink-0">{children}</div>
        </div>
    )
}

/** Колонка единиц измерения одной ширины у всех полей — тогда поля стоят ровно друг под другом. */
const UNIT = "w-[4.5rem] shrink-0 text-[14px] text-muted-foreground"

function Num({k, form, set, unit, min = 0, step = 1}: { k: string; form: Form; set: (k: string, v: unknown) => void; unit?: string; min?: number; step?: number }) {
    return (
        <div className="flex items-center gap-2">
            <Input type="number" min={min} step={step} value={String(form[k] ?? "")} aria-label={k}
                   onChange={(e) => set(k, e.target.value === "" ? "" : Number(e.target.value))} className="w-24 font-mono"/>
            {/* Место под единицы есть всегда — даже пустое, чтобы все поля стояли в одну колонку. */}
            <span className={UNIT}>{unit}</span>
        </div>
    )
}

function Range({lo, hi, form, set}: { lo: string; hi: string; form: Form; set: (k: string, v: unknown) => void }) {
    return (
        <div className="flex items-center gap-2 text-[14px] text-muted-foreground">
            от <Input type="number" min={0} step="0.5" value={String(form[lo] ?? "")} aria-label={`${lo}`} onChange={(e) => set(lo, Number(e.target.value))} className="w-24 font-mono"/>
            до <Input type="number" min={0} step="0.5" value={String(form[hi] ?? "")} aria-label={`${hi}`} onChange={(e) => set(hi, Number(e.target.value))} className="w-24 font-mono"/>
            <span className={UNIT}>с</span>
        </div>
    )
}

export default function SettingsPage() {
    const {data, mutate} = useApi<Settings>("/settings")
    const [form, setForm] = useState<Form>({})
    const [secrets, setSecrets] = useState({tg_bot_token: "", bsboard_key: ""})
    const [busy, setBusy] = useState(false)
    const [chats, setChats] = useState<{ id: string; title: string }[]>([])

    useEffect(() => {
        if (data) setForm(data)
    }, [data])

    if (!data) return <Skeleton className="h-96 rounded-2xl"/>

    const set = (k: string, v: unknown) => setForm((f) => ({...f, [k]: v}))
    const changed = Object.keys(form).filter((k) => JSON.stringify(form[k]) !== JSON.stringify(data[k]))
    const dirty = changed.length > 0 || Boolean(secrets.tg_bot_token || secrets.bsboard_key)
    const preset = presetOf(form)

    const save = async () => {
        setBusy(true)
        try {
            const body: Form = {}
            for (const k of changed) body[k] = form[k]
            if (secrets.tg_bot_token) body.tg_bot_token = secrets.tg_bot_token
            if (secrets.bsboard_key) body.bsboard_key = secrets.bsboard_key
            await put("/settings", body)
            setSecrets({tg_bot_token: "", bsboard_key: ""})
            await mutate()
            toast.success("Настройки сохранены и уже действуют")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        } finally {
            setBusy(false)
        }
    }

    const tgTest = async () => {
        try {
            await post("/settings/telegram/test", {token: secrets.tg_bot_token, chat_id: String(form.tg_chat_id ?? "")})
            toast.success("Сообщение отправлено — проверьте Telegram")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        }
    }
    const findChats = async () => {
        try {
            const res = await post<{ id: string; title: string }[]>("/settings/telegram/chats", {token: secrets.tg_bot_token})
            setChats(res)
            if (res.length === 1) set("tg_chat_id", res[0].id)
            if (!res.length) toast("Чатов не видно. Отправьте боту /start и нажмите ещё раз.")
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        }
    }

    const regions = (form.regions as string[]) ?? []

    return (
        <>
            <PageHeader title="Настройки" description="Общие для всех ключей. Сохранённые изменения действуют сразу — перезапускать ничего не нужно."
                        action={<Button size="sm" onClick={save} disabled={!dirty || busy}><FloppyDisk weight="bold"/> Сохранить</Button>}/>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Section title="Скорость поиска"
                         text="Чем чаще запросы, тем быстрее находится адрес, но тем выше риск, что Selectel временно заблокирует аккаунт.">
                    <div role="radiogroup" aria-label="Скорость поиска" className="grid gap-2 sm:grid-cols-3">
                        {PRESETS.map((p) => {
                            const active = preset === p.id
                            return (
                                <button key={p.id} role="radio" aria-checked={active} onClick={() => Object.entries(p.values).forEach(([k, v]) => set(k, v))}
                                        className={cn("flex flex-col items-start rounded-xl border p-4 text-left transition-[border-color,background-color,transform] hover:-translate-y-0.5",
                                            active ? "border-amber/60 bg-amber/[0.07]" : "border-border bg-card-raised/40 hover:border-border-strong")}>
                                    <span className="flex w-full items-center justify-between text-[15px] font-medium">
                                        {p.title}
                                        {active && <CheckCircle weight="fill" className="size-5 text-amber"/>}
                                    </span>
                                    <span className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">{p.text}</span>
                                </button>
                            )
                        })}
                    </div>
                    {preset === "fast" && (
                        <Note tone="warn" className="mt-3">При таком темпе Selectel уже блокировал аккаунты. Используйте, только если готовы рискнуть.</Note>
                    )}
                    <details className="mt-4 rounded-xl border border-border px-4 py-3" open={preset === null}>
                        <summary className="cursor-pointer select-none text-[14.5px] font-medium">
                            Тонкая настройка {preset === null && <span className="font-normal text-muted-foreground">· свои значения</span>}
                        </summary>
                        <div className="mt-4">
                            <Row label="Пауза между запросами" hint="После каждого запроса к Selectel.">
                                <Range lo="busy_delay_min" hi="busy_delay_max" form={form} set={set}/>
                            </Row>
                            <Row label="Пауза между проходами" help="Проход — по одному запросу в каждую включённую подсеть. После прохода ключ ждёт столько.">
                                <Range lo="round_delay_min" hi="round_delay_max" form={form} set={set}/>
                            </Row>
                            <Row label="Длинный перерыв каждые" hint="0 — без длинных перерывов.">
                                <Num k="long_pause_every" form={form} set={set} unit="проходов"/>
                            </Row>
                            <Row label="Длина длинного перерыва">
                                <Range lo="long_pause_min" hi="long_pause_max" form={form} set={set}/>
                            </Row>
                        </div>
                    </details>
                </Section>

                <Section title="Поиск">
                    <Row label="Сколько IP найти на ключ" hint="Когда на аккаунте будет столько подходящих адресов, поиск на нём остановится. У ключа можно задать своё число.">
                        <Num k="ip_goal" form={form} set={set} unit="IP" min={1}/>
                    </Row>
                    <Row label="Только целиком подходящие подсети"
                         help="Если подсеть Selectel шире вашей, адрес может не подойти — тогда панель сразу вернёт его обратно. С этой опцией такие подсети пропускаются."
                         hint="Без лишних выданных и возвращённых адресов.">
                        <Switch checked={Boolean(form.exact_only)} onChange={(v) => set("exact_only", v)} label="Только целиком подходящие подсети"/>
                    </Row>
                    <Row label="Запускать ключи по очереди, каждые"
                         help="Когда стартует сразу много ключей (после перезапуска панели или «Запустить все»), они входят в Selectel по одному с этим интервалом — без всплеска запросов и рейт-лимитов."
                         hint="0 — все сразу.">
                        <Num k="start_stagger" form={form} set={set} unit="с" min={0}/>
                    </Row>
                    <Row label="Останавливать ключ после ошибок подряд" hint="0 — не останавливать.">
                        <Num k="max_consecutive_errors" form={form} set={set}/>
                    </Row>
                    <Row label="Ждать ответа Selectel" hint="Если Selectel не ответил за это время, запрос считается неудачным.">
                        <Num k="request_timeout" form={form} set={set} unit="с" min={5}/>
                    </Row>
                    <Row label="Проверять прокси каждые">
                        <Num k="proxy_check_interval_min" form={form} set={set} unit="мин" min={5}/>
                    </Row>
                    <div className="pt-4">
                        <div className="text-[15px]">Регионы Selectel</div>
                        <div className="mt-0.5 text-[13px] text-muted-foreground">Где искать адреса. Выключенные регионы пропускаются.</div>
                        <div className="mt-3 grid grid-cols-2 gap-2">
                            {REGIONS.map(([r, city]) => (
                                <label key={r} className={cn("flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-3 transition-colors",
                                    regions.includes(r) ? "border-amber/40 bg-amber/[0.05]" : "border-border hover:border-border-strong")}>
                                    <Checkbox checked={regions.includes(r)} label={r}
                                              onChange={(v) => set("regions", v ? [...regions, r].sort() : regions.filter((x) => x !== r))}/>
                                    <span><Mono className="text-foreground">{r}</Mono> <span className="text-[13px] text-muted-foreground">· {city}</span></span>
                                </label>
                            ))}
                        </div>
                    </div>
                </Section>

                <Section title="Темп и приоритет"
                         text="Панель сама подстраивает скорость под Selectel и чаще заглядывает туда, где адреса освобождаются.">
                    <Row label="Регулятор темпа"
                         help="Если Selectel просит снизить частоту (429), начинает отвечать ошибками или банит соседний аккаунт, паузы ключа растягиваются. Когда долго всё спокойно, темп возвращается к настройкам. После бана аккаунта ключ при следующем запуске сам ищет медленнее."
                         hint="Множитель пауз виден в списке ключей.">
                        <Switch checked={Boolean(form.pace_governor)} onChange={(v) => set("pace_governor", v)} label="Регулятор темпа"/>
                    </Row>
                    <Row label="Замедлять не больше чем в" hint="Например, 4 — пауза 10 с станет максимум 40 с.">
                        <Num k="pace_max" form={form} set={set} unit="раза" min={1} step={0.5}/>
                    </Row>
                    <Row label="Ускорять, если всё спокойно" hint="Сколько минут без сигналов от Selectel до следующего шага разгона.">
                        <Num k="pace_relax_min" form={form} set={set} unit="мин" min={1}/>
                    </Row>
                    <Row label="Горячие подсети первыми"
                         help="Подсети, где адреса находились чаще среднего, проверяются в начале каждого прохода. Холодные — где тысячи попыток ничего не дали — пропускаются в части проходов. Запросов от этого только меньше."
                         hint="Рейтинг подсетей — на странице «Подсети».">
                        <Switch checked={Boolean(form.hot_priority)} onChange={(v) => set("hot_priority", v)} label="Горячие подсети первыми"/>
                    </Row>
                    <Row label="Холодные подсети проверять" hint="Доля проходов: 0,35 — примерно каждый третий.">
                        <Num k="cold_share" form={form} set={set} min={0.05} step={0.05}/>
                    </Row>
                </Section>

                <Section title="Деньги"
                         text="Баланс аккаунтов Selectel и сроки выданных IP. Предупреждения приходят в Telegram.">
                    <Row label="Проверять баланс каждые">
                        <Num k="balance_check_min" form={form} set={set} unit="мин" min={10}/>
                    </Row>
                    <Row label="Предупреждать, когда денег осталось меньше чем на" hint="0 — не предупреждать.">
                        <Num k="balance_warn_days" form={form} set={set} unit="дн." min={0}/>
                    </Row>
                    <Row label="Напоминать о сроке IP за" hint="Срок задаётся у адреса на странице «IP на аккаунтах». 0 — не напоминать.">
                        <Num k="ip_expiry_warn_days" form={form} set={set} unit="дн." min={0}/>
                    </Row>
                    <Row label="Диск сервера под IP" hint="Размер загрузочного диска, когда панель поднимает сервер под адрес.">
                        <Num k="vds_disk_gb" form={form} set={set} unit="ГБ" min={5}/>
                    </Row>
                </Section>

                <Section title="Уведомления в Telegram"
                         text="Бот только присылает сообщения, управлять через него ничего не нужно.">
                    <ol className="mb-5 list-decimal space-y-1 pl-5 text-[14px] leading-relaxed text-muted-foreground">
                        <li>Создайте бота у <Mono className="text-foreground">@BotFather</Mono> и вставьте его токен ниже.</li>
                        <li>Напишите своему боту <Mono className="text-foreground">/start</Mono>.</li>
                        <li>Нажмите «Найти чат», сохраните и отправьте тестовое сообщение.</li>
                    </ol>
                    <div className="space-y-4">
                        <Field label="Токен бота" hint={data.tg_bot_token_set ? "Токен сохранён. Впишите новый, только если хотите заменить бота." : undefined}>
                            <Input type="password" value={secrets.tg_bot_token} autoComplete="off" placeholder={data.tg_bot_token_set ? "Сохранён" : "123456789:AA…"}
                                   onChange={(e) => setSecrets({...secrets, tg_bot_token: e.target.value})}/>
                        </Field>
                        <Field label="Чат для уведомлений" hint="Ваш личный чат с ботом или группа, куда добавлен бот.">
                            <div className="flex gap-2">
                                <Input value={String(form.tg_chat_id ?? "")} onChange={(e) => set("tg_chat_id", e.target.value)} className="font-mono" placeholder="Номер чата"/>
                                <Button type="button" variant="outline" className="shrink-0" onClick={findChats}>Найти чат</Button>
                            </div>
                        </Field>
                        {chats.length > 1 && (
                            <div className="flex flex-wrap gap-2">
                                {chats.map((c) => (
                                    <button key={c.id} onClick={() => set("tg_chat_id", c.id)}
                                            className={cn("h-10 rounded-xl border px-3 text-[14px]", form.tg_chat_id === c.id ? "border-amber/60 bg-amber/[0.07]" : "border-border-strong hover:border-subtle")}>
                                        {c.title || "Чат"} <Mono className="text-subtle">{c.id}</Mono>
                                    </button>
                                ))}
                            </div>
                        )}
                        <Field label="Второй чат для отчётов" help="Необязательно. Сюда дублируются все найденные IP — удобно для общего канала команды.">
                            <Input value={String(form.tg_admin_chat_id ?? "")} onChange={(e) => set("tg_admin_chat_id", e.target.value)} className="font-mono" placeholder="Не задан"/>
                        </Field>
                        <Button type="button" variant="outline" onClick={tgTest}><PaperPlaneTilt/> Отправить тестовое сообщение</Button>
                    </div>
                    <div className="mt-6">
                        <Row label="Найденные IP"><Switch checked={Boolean(form.notify_caught)} onChange={(v) => set("notify_caught", v)} label="Найденные IP"/></Row>
                        <Row label="Проблемы" hint="Не удаётся войти, блокировка Selectel, закончился лимит, не работают прокси.">
                            <Switch checked={Boolean(form.notify_alerts)} onChange={(v) => set("notify_alerts", v)} label="Проблемы"/>
                        </Row>
                        <Row label="Входы в панель" hint="Вход с нового адреса и блокировка после неверных паролей.">
                            <Switch checked={Boolean(form.notify_logins)} onChange={(v) => set("notify_logins", v)} label="Входы в панель"/>
                        </Row>
                        <Row label="Смена внешнего IP у прокси"><Switch checked={Boolean(form.notify_rotations)} onChange={(v) => set("notify_rotations", v)} label="Смена внешнего IP"/></Row>
                        <Row label="Итоги дня в 9:00 по Москве"><Switch checked={Boolean(form.notify_daily)} onChange={(v) => set("notify_daily", v)} label="Итоги дня"/></Row>
                        <Row label="Деньги и сроки" hint="Мало денег на аккаунте, подходит срок IP, сервер под IP готов.">
                            <Switch checked={Boolean(form.notify_money)} onChange={(v) => set("notify_money", v)} label="Деньги и сроки"/>
                        </Row>
                    </div>
                </Section>

                <Section title="Проверка найденных IP"
                         text="Каждый найденный адрес можно автоматически проверять через сервис bsboard. Пока адрес сервиса не указан, проверка выключена.">
                    <div className="space-y-4">
                        <Field label="Адрес сервиса проверки" hint="Панель отправляет запрос с параметром ip и сохраняет ответ.">
                            <Input value={String(form.bsboard_url ?? "")} onChange={(e) => set("bsboard_url", e.target.value)} placeholder="https://…" className="font-mono"/>
                        </Field>
                        <Field label="Ключ доступа" hint={data.bsboard_key_set ? "Ключ сохранён. Впишите новый, только если хотите заменить." : undefined}>
                            <Input type="password" value={secrets.bsboard_key} autoComplete="off" placeholder={data.bsboard_key_set ? "Сохранён" : "Не задан"}
                                   onChange={(e) => setSecrets({...secrets, bsboard_key: e.target.value})}/>
                        </Field>
                        <Row label="Перепроверять адреса каждые" hint="Пока адрес остаётся на аккаунте.">
                            <Num k="bsboard_recheck_hours" form={form} set={set} unit="ч" min={1}/>
                        </Row>
                    </div>
                </Section>
            </div>

            {dirty && (
                <div className="sticky bottom-20 z-20 mt-6 flex justify-end lg:bottom-6">
                    <div className="flex w-full items-center justify-between gap-3 rounded-2xl border border-border-strong bg-card-raised px-4 py-3 shadow-2xl sm:w-auto">
                        <span className="text-[14px] text-muted-foreground">Есть несохранённые изменения</span>
                        <div className="flex gap-2">
                            <Button size="sm" variant="ghost" onClick={() => {
                                setForm(data)
                                setSecrets({tg_bot_token: "", bsboard_key: ""})
                            }}>Отменить</Button>
                            <Button size="sm" onClick={save} disabled={busy}><FloppyDisk weight="bold"/> Сохранить</Button>
                        </div>
                    </div>
                </div>
            )}
        </>
    )
}
