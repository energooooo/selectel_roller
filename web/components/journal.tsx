"use client"

import {CheckCircle, Info, Target, Warning, XCircle} from "@phosphor-icons/react"
import {fmtTime} from "@/lib/format"
import type {EventItem} from "@/lib/types"
import {cn} from "@/lib/utils"

const LEVEL = {
    debug: {icon: Info, cls: "text-subtle", label: "Отладка"},
    info: {icon: Info, cls: "text-info", label: "Событие"},
    success: {icon: CheckCircle, cls: "text-ok", label: "Успех"},
    warn: {icon: Warning, cls: "text-warn", label: "Предупреждение"},
    error: {icon: XCircle, cls: "text-danger", label: "Ошибка"},
} as const

export function EventRow({e, dense}: { e: EventItem; dense?: boolean }) {
    const caught = e.kind === "caught"
    const L = LEVEL[e.level] ?? LEVEL.info
    const Icon = caught ? Target : L.icon
    // В журнале сообщение начинается с «[имя ключа]» — показываем имя отдельно, чтобы строка читалась.
    const match = e.message.match(/^\[([^\]]+)]\s*(.*)$/s)
    const keyName = match?.[1]
    const text = match ? match[2] : e.message
    return (
        <div className={cn(
            "flex gap-3 border-b border-border/60 last:border-0",
            dense ? "py-2.5" : "py-3",
            caught && "-mx-2 my-1 rounded-xl border-0 bg-amber/[0.08] px-3",
        )}>
            <span className="w-12 shrink-0 pt-0.5 font-mono text-[12.5px] text-subtle tabular">{fmtTime(e.ts)}</span>
            <Icon aria-label={caught ? "Найден IP" : L.label} weight={caught ? "fill" : "regular"} className={cn("mt-0.5 size-[18px] shrink-0", caught ? "text-amber" : L.cls)}/>
            <p className={cn("min-w-0 flex-1 break-words text-[14px] leading-relaxed",
                e.level === "error" ? "text-danger/90" : caught ? "font-medium text-foreground" : "text-muted-foreground")}>
                {keyName && <span className="mr-1.5 font-medium text-foreground/85">{keyName}:</span>}
                {text.charAt(0).toUpperCase() + text.slice(1)}
            </p>
        </div>
    )
}
