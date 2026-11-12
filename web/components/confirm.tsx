"use client"

import {useState} from "react"
import {Button} from "@/components/ui/button"
import {Dialog, Input} from "@/components/ui/primitives"

/** Подтверждение опасного действия. С typeToConfirm — нужно вписать строку (например, сам IP). */
export function ConfirmDialog({open, onOpenChange, title, description, confirmLabel = "Да", danger, typeToConfirm, onConfirm}: {
    open: boolean
    onOpenChange: (v: boolean) => void
    title: string
    description?: React.ReactNode
    confirmLabel?: string
    danger?: boolean
    typeToConfirm?: string
    onConfirm: () => Promise<void> | void
}) {
    const [typed, setTyped] = useState("")
    const [busy, setBusy] = useState(false)
    const ok = !typeToConfirm || typed.trim() === typeToConfirm
    return (
        <Dialog open={open} onOpenChange={(v) => {
            setTyped("")
            onOpenChange(v)
        }} title={title} description={description}>
            {typeToConfirm && (
                <div className="mb-5">
                    <p className="mb-2 text-[14px] text-muted-foreground">Чтобы подтвердить, введите <span className="font-mono text-foreground">{typeToConfirm}</span></p>
                    <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="font-mono" autoFocus/>
                </div>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
                <Button variant={danger ? "danger" : "brand"} disabled={!ok || busy} onClick={async () => {
                    setBusy(true)
                    try {
                        await onConfirm()
                        onOpenChange(false)
                    } finally {
                        setBusy(false)
                    }
                }}>{busy ? "…" : confirmLabel}</Button>
            </div>
        </Dialog>
    )
}
