"use client"

import {useState} from "react"
import {toast} from "sonner"
import {ArrowsClockwise, Copy, Crosshair, ShieldCheck, Trash} from "@phosphor-icons/react"
import {ConfirmDialog} from "@/components/confirm"
import {Badge, CHECK_STATUS} from "@/components/status"
import {Button} from "@/components/ui/button"
import {Card, CardHeader, Checkbox, Empty, Hint, Mono, PageHeader, Skeleton, Tip} from "@/components/ui/primitives"
import {ApiError, patch, post, useApi} from "@/lib/api"
import {fmtAgo, fmtDateTime} from "@/lib/format"
import type {Caught, FloatingIp, Key} from "@/lib/types"

function copy(ip: string) {
    navigator.clipboard.writeText(ip).then(() => toast.success(`${ip} скопирован`))
}

function AccountIps({k}: { k: Key }) {
    const {data, error, isLoading, mutate: reload} = useApi<{ items: FloatingIp[] }>(`/keys/${k.id}/floating-ips`, {
        revalidateOnFocus: false,
    })
    const [deleting, setDeleting] = useState<FloatingIp | null>(null)

    const protect = async (fip: FloatingIp, value: boolean) => {
        if (!fip.caught) return
        await patch(`/caught/${fip.caught.id}`, {protected: value})
        reload()
    }

    return (
        <Card>
            <CardHeader
                title={<span className="flex items-center gap-2">{k.name} <span className="font-mono text-[12px] font-normal text-subtle">{k.account_id}</span></span>}
                action={
                    <div className="flex items-center gap-3">
                        <span className="text-[14px] text-muted-foreground">{data ? `Адресов: ${data.items.length}` : "Загружаю…"}</span>
                        <Button variant="outline" size="icon" aria-label="Обновить список" onClick={() => reload()}>
                            <ArrowsClockwise className={isLoading ? "animate-spin" : ""}/>
                        </Button>
                    </div>
                }/>
            <div className="p-5 pt-3">
                {error ? (
                    <p className="text-[14px] text-danger">Не удалось получить список: {error.message}</p>
                ) : !data ? (
                    <Skeleton className="h-12"/>
                ) : data.items.length ? (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[620px] text-[13.5px]">
                            <thead>
                            <tr className="text-left">
                                <th className="pb-2.5 pr-4 text-left text-[13px] font-medium text-muted-foreground">IP</th>
                                <th className="pb-2.5 pr-4 text-left text-[13px] font-medium text-muted-foreground">Регион</th>
                                <th className="pb-2.5 pr-4 text-left text-[13px] font-medium text-muted-foreground">Откуда</th>
                                <th className="pb-2.5 pr-4 text-left text-[13px] font-medium text-muted-foreground">Проверка</th>
                                <th className="pb-2.5 pr-4 text-left text-[13px] font-medium text-muted-foreground">
                                    <span className="inline-flex items-center gap-1.5">Защита<Hint>Защищённый адрес нельзя удалить из панели, пока защиту не снимут. Найденные адреса защищаются автоматически.</Hint></span>
                                </th>
                                <th/>
                            </tr>
                            </thead>
                            <tbody>
                            {data.items.map((f) => (
                                <tr key={f.floating_ip_id} className={f.white ? "border-t border-border/60 bg-amber/[0.04]" : "border-t border-border/60"}>
                                    <td className="py-2.5 pr-4">
                                        <span className="inline-flex items-center gap-2">
                                            {f.white && <span className="h-5 w-[3px] rounded-full bg-brand"/>}
                                            <Mono className="text-[13px] text-foreground">{f.ip}</Mono>
                                            <Tip label="Скопировать"><button onClick={() => copy(f.ip)} aria-label={`Скопировать ${f.ip}`} className="grid size-8 place-items-center rounded-lg text-subtle hover:bg-card-raised hover:text-amber"><Copy className="size-[18px]"/></button></Tip>
                                        </span>
                                    </td>
                                    <td className="pr-4 font-mono text-[12.5px] text-muted-foreground">{f.region}</td>
                                    <td className="pr-4 text-[12.5px]">
                                        {f.caught ? (
                                            <Badge tone="brand">{f.caught.source === "hunt" ? `Найден ${fmtDateTime(f.caught.caught_at)}` : "Подходит, был на аккаунте"}</Badge>
                                        ) : f.white ? <Badge tone="ok">Подходит</Badge> : <span className="text-muted-foreground">Не из ваших подсетей</span>}
                                        <span className="ml-2 text-[13px] text-subtle">{f.attached ? "· привязан к серверу" : "· не привязан"}</span>
                                    </td>
                                    <td className="pr-4">
                                        {f.caught && <Badge tone={CHECK_STATUS[f.caught.check_status]?.tone}>{CHECK_STATUS[f.caught.check_status]?.label}</Badge>}
                                    </td>
                                    <td className="pr-4">
                                        {f.caught ? (
                                            <span className="inline-flex items-center gap-2 text-[14px]"><Checkbox checked={f.caught.protected} onChange={(v) => protect(f, v)} label="Не удалять"/>Не удалять</span>
                                        ) : <span className="text-subtle">—</span>}
                                    </td>
                                    <td className="text-right">
                                        <button aria-label={`Удалить ${f.ip} с аккаунта`} title={f.caught?.protected ? "Адрес защищён — сначала снимите «Не удалять»" : "Удалить с аккаунта"}
                                                disabled={f.caught?.protected}
                                                onClick={() => setDeleting(f)}
                                                className="grid size-10 place-items-center rounded-xl text-subtle transition-colors enabled:hover:bg-danger/10 enabled:hover:text-danger disabled:opacity-30">
                                            <Trash className="size-[18px]"/>
                                        </button>
                                    </td>
                                </tr>
                            ))}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    <p className="text-[14px] text-muted-foreground">На этом аккаунте сейчас нет плавающих IP.</p>
                )}
            </div>
            {deleting && (
                <ConfirmDialog open onOpenChange={(v) => !v && setDeleting(null)} danger confirmLabel="Удалить IP"
                               title={`Удалить ${deleting.ip} с аккаунта?`}
                               description="Адрес вернётся в Selectel, и его сможет получить кто угодно. Отменить это нельзя."
                               typeToConfirm={deleting.ip}
                               onConfirm={async () => {
                                   try {
                                       await post(`/keys/${k.id}/floating-ips/${deleting.floating_ip_id}/delete`, {confirm_ip: deleting.ip})
                                       toast.success(`${deleting.ip} удалён`)
                                       reload()
                                   } catch (e) {
                                       toast.error(e instanceof ApiError ? e.message : "Не получилось")
                                   }
                               }}/>
            )}
        </Card>
    )
}

export default function IpsPage() {
    const {data: keys} = useApi<Key[]>("/keys")
    const {data: released} = useApi<Caught[]>("/caught?on_account=false")
    return (
        <>
            <PageHeader title="IP на аккаунтах"
                        description="Плавающие IP, которые сейчас лежат на каждом аккаунте Selectel. Подходящие выделены, найденные панелью защищены от случайного удаления."/>
            <div className="space-y-3">
                {!keys ? <Skeleton className="h-32 rounded-2xl"/> : keys.length ? keys.map((k) => <AccountIps key={k.id} k={k}/>) : (
                    <Card><Empty icon={<Crosshair weight="duotone" className="size-6"/>} title="Нет ключей">Добавьте ключ Selectel — здесь появятся адреса с его аккаунта.</Empty></Card>
                )}
                <Card>
                    <CardHeader title="Найденные, но уже удалённые" hint="Адреса, которые панель нашла, но позже их удалили с аккаунта — здесь или в панели Selectel."/>
                    <div className="p-5 pt-3">
                        {released?.length ? (
                            <ul className="divide-y divide-border/60">
                                {released.map((c) => (
                                    <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-[13px]">
                                        <Mono className="text-foreground">{c.ip}</Mono>
                                        <span className="text-muted-foreground">{c.region} · {c.key_name ?? c.account_id}</span>
                                        <span className="text-subtle">найден {fmtDateTime(c.caught_at)} · удалён {fmtAgo(c.released_at)}</span>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
                                <ShieldCheck className="size-5 text-ok"/> Таких нет: все найденные адреса на месте.
                            </p>
                        )}
                    </div>
                </Card>
            </div>
        </>
    )
}
