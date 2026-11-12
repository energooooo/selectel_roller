"use client"

import {useMemo, useState} from "react"
import {toast} from "sonner"
import {Check, Prohibit, ArrowCounterClockwise, UsersThree, X} from "@phosphor-icons/react"
import {ConfirmDialog} from "@/components/confirm"
import {Badge} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, Empty, Mono, PageHeader, Segmented, Skeleton} from "@/components/ui/primitives"
import {ApiError, post, useApi} from "@/lib/api"
import {fmtAgo, fmtDateTime} from "@/lib/format"
import type {PanelUser} from "@/lib/types"

const STATUS: Record<PanelUser["status"], { tone: "warn" | "ok" | "muted" | "danger"; label: string }> = {
    pending: {tone: "warn", label: "Ждёт решения"},
    active: {tone: "ok", label: "Активен"},
    rejected: {tone: "muted", label: "Отклонён"},
    blocked: {tone: "danger", label: "Заблокирован"},
}

type Filter = "pending" | "active" | "closed" | "all"

function UserRow({u, onDecide}: { u: PanelUser; onDecide: (u: PanelUser, action: "approve" | "reject" | "block") => void }) {
    const s = STATUS[u.status]
    return (
        <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:gap-6 sm:px-6">
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2.5">
                    <span className="text-[15.5px] font-semibold">{u.login}</span>
                    {u.role === "owner" ? <Badge tone="brand">Владелец панели</Badge> : <Badge tone={s.tone}>{s.label}</Badge>}
                </div>
                {u.description && (
                    <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">{u.description}</p>
                )}
                <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-subtle">
                    <span>заявка {fmtDateTime(u.created_at)}</span>
                    {u.reg_ip && <span>IP <Mono className="text-muted-foreground">{u.reg_ip}</Mono></span>}
                    {u.browser && u.browser !== "неизвестно" && <span>{u.browser}</span>}
                    {u.decided_at && <span>решено {fmtAgo(u.decided_at)}{u.decided_by ? ` · ${u.decided_by}` : ""}</span>}
                </p>
            </div>
            {u.role !== "owner" && (
                <div className="flex shrink-0 flex-wrap gap-2">
                    {u.status === "pending" && <>
                        <Button size="sm" onClick={() => onDecide(u, "approve")}><Check weight="bold"/> Принять</Button>
                        <Button size="sm" variant="outline" onClick={() => onDecide(u, "reject")}><X weight="bold"/> Отклонить</Button>
                    </>}
                    {u.status === "active" && (
                        <Button size="sm" variant="danger" onClick={() => onDecide(u, "block")}><Prohibit weight="bold"/> Заблокировать</Button>
                    )}
                    {(u.status === "blocked" || u.status === "rejected") && (
                        <Button size="sm" variant="outline" onClick={() => onDecide(u, "approve")}><ArrowCounterClockwise weight="bold"/> Вернуть доступ</Button>
                    )}
                </div>
            )}
        </li>
    )
}

export default function UsersPage() {
    const {data, mutate} = useApi<PanelUser[]>("/users")
    const [filter, setFilter] = useState<Filter>("pending")
    const [blocking, setBlocking] = useState<PanelUser | null>(null)

    const counts = useMemo(() => ({
        pending: data?.filter((u) => u.status === "pending").length ?? 0,
        active: data?.filter((u) => u.status === "active").length ?? 0,
        closed: data?.filter((u) => u.status === "blocked" || u.status === "rejected").length ?? 0,
    }), [data])
    const rows = (data ?? []).filter((u) => filter === "all"
        || (filter === "closed" ? u.status === "blocked" || u.status === "rejected" : u.status === filter))

    const decide = async (u: PanelUser, action: "approve" | "reject" | "block") => {
        try {
            await post(`/users/${u.id}/decision`, {action})
            toast.success({approve: `${u.login}: доступ открыт`, reject: `${u.login}: заявка отклонена`, block: `${u.login} заблокирован`}[action])
            mutate()
        } catch (e) {
            toast.error(e instanceof ApiError ? e.message : "Не получилось")
        }
    }

    return (
        <>
            <PageHeader title="Пользователи"
                        description="Заявки на доступ и те, кто уже работает в панели. У каждого свой кабинет: его ключи, адреса и деньги видит только он. Заявки приходят и в админ-бот — решать можно там или здесь."/>
            <Card>
                <div className="px-5 pt-5 sm:px-6">
                    <Segmented value={filter} onChange={setFilter} options={[
                        ["pending", `Ждут решения · ${counts.pending}`], ["active", `Активные · ${counts.active}`],
                        ["closed", `Закрыт доступ · ${counts.closed}`], ["all", "Все"],
                    ]}/>
                </div>
                <div className="mt-3">
                    {!data ? <div className="p-5"><Skeleton className="h-32"/></div> : rows.length ? (
                        <ul className="divide-y divide-border/60 border-t border-border">
                            {rows.map((u) => <UserRow key={u.id} u={u} onDecide={(user, action) => action === "block" ? setBlocking(user) : decide(user, action)}/>)}
                        </ul>
                    ) : (
                        <Empty icon={<UsersThree weight="duotone" className="size-6"/>}
                               title={filter === "pending" ? "Новых заявок нет" : "Здесь пусто"}>
                            {filter === "pending" ? "Когда кто-то подаст заявку на странице входа, она появится здесь и придёт в админ-бот." : "Выберите другую вкладку."}
                        </Empty>
                    )}
                </div>
            </Card>
            {blocking && (
                <ConfirmDialog open onOpenChange={(v) => !v && setBlocking(null)} danger confirmLabel="Заблокировать"
                               title={`Заблокировать ${blocking.login}?`}
                               description="Его сессии закроются, а все его ключи перестанут искать IP. Данные кабинета останутся — доступ можно вернуть."
                               onConfirm={() => decide(blocking, "block")}/>
            )}
        </>
    )
}
