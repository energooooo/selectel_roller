"use client"

import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"
import {DotsThreeVertical, Question, X} from "@phosphor-icons/react"
import {cn} from "@/lib/utils"

export function Card({className, ...props}: React.ComponentProps<"div">) {
    return <div className={cn("rounded-2xl border border-border bg-card", className)} {...props}/>
}

export function CardHeader({title, hint, action, className}: {
    title: React.ReactNode
    hint?: React.ReactNode
    action?: React.ReactNode
    className?: string
}) {
    return (
        <div className={cn("flex flex-wrap items-start justify-between gap-3 px-5 pt-5 sm:px-6", className)}>
            <div className="min-w-0">
                <h2 className="text-[16px] font-semibold tracking-[-0.01em]">{title}</h2>
                {hint && <p className="mt-1 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">{hint}</p>}
            </div>
            {action}
        </div>
    )
}

export function PageHeader({title, description, action}: {
    title: string
    description?: React.ReactNode
    action?: React.ReactNode
}) {
    return (
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0">
                <h1 className="text-[28px] font-semibold tracking-[-0.03em] sm:text-[32px]">{title}</h1>
                {description && <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted-foreground">{description}</p>}
            </div>
            {action && <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">{action}</div>}
        </div>
    )
}

const inputBase =
    "w-full rounded-xl border border-border-strong bg-input px-3.5 text-[15px] outline-none transition-colors placeholder:text-subtle hover:border-subtle focus:border-amber/80 focus:ring-2 focus:ring-amber/20 disabled:opacity-60"

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
    function Input({className, ...props}, ref) {
        return <input ref={ref} className={cn(inputBase, "h-11", className)} {...props}/>
    },
)

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(
    function Textarea({className, ...props}, ref) {
        return (
            <textarea
                ref={ref}
                spellCheck={false}
                className={cn(inputBase, "min-h-36 resize-y py-3 font-mono text-[13.5px] leading-relaxed", className)}
                {...props}
            />
        )
    },
)

export function Select({className, ...props}: React.ComponentProps<"select">) {
    return <select className={cn(inputBase, "h-11 cursor-pointer pr-8", className)} {...props}/>
}

export function Field({label, hint, help, children, className}: {
    label: React.ReactNode
    hint?: React.ReactNode
    help?: React.ReactNode
    children: React.ReactNode
    className?: string
}) {
    return (
        <label className={cn("block", className)}>
            <span className="flex items-center gap-1.5 text-[14px] font-medium text-foreground/90">
                {label}
                {help && <Hint>{help}</Hint>}
            </span>
            <div className="mt-2">{children}</div>
            {hint && <span className="mt-1.5 block text-[13px] leading-relaxed text-muted-foreground">{hint}</span>}
        </label>
    )
}

/** Знак «?» с пояснением термина. Работает по наведению, по фокусу с клавиатуры и по тапу. */
export function Hint({children, className}: { children: React.ReactNode; className?: string }) {
    const [open, setOpen] = React.useState(false)
    return (
        <TooltipPrimitive.Root open={open} onOpenChange={setOpen} delayDuration={150}>
            <TooltipPrimitive.Trigger asChild>
                <button type="button" aria-label="Пояснение" onClick={(e) => {
                    e.preventDefault()
                    setOpen((v) => !v)
                }}
                        className={cn("inline-grid size-5 shrink-0 place-items-center rounded-full text-subtle transition-colors hover:text-amber", className)}>
                    <Question className="size-4"/>
                </button>
            </TooltipPrimitive.Trigger>
            <TooltipPrimitive.Portal>
                <TooltipPrimitive.Content side="top" sideOffset={6} collisionPadding={12}
                                          className="z-[60] max-w-[300px] rounded-xl border border-border-strong bg-card-raised px-3.5 py-2.5 text-[13.5px] font-normal leading-relaxed text-foreground shadow-2xl data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0">
                    {children}
                </TooltipPrimitive.Content>
            </TooltipPrimitive.Portal>
        </TooltipPrimitive.Root>
    )
}

/** Подсказка-тултип над любым элементом (например, кнопкой-иконкой). */
export function Tip({label, children}: { label: string; children: React.ReactElement }) {
    return (
        <TooltipPrimitive.Root delayDuration={300}>
            <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
            <TooltipPrimitive.Portal>
                <TooltipPrimitive.Content side="top" sideOffset={6}
                                          className="z-[60] rounded-lg border border-border-strong bg-card-raised px-2.5 py-1.5 text-[13px] text-foreground shadow-xl">
                    {label}
                </TooltipPrimitive.Content>
            </TooltipPrimitive.Portal>
        </TooltipPrimitive.Root>
    )
}

export const TooltipProvider = TooltipPrimitive.Provider

export interface MenuItem {
    label: string
    icon?: React.ReactNode
    onSelect: () => void
    danger?: boolean
    disabled?: boolean
}

/** Меню «⋯» для второстепенных действий: каждое действие подписано словами. */
export function ActionMenu({items, label = "Ещё действия"}: { items: (MenuItem | "sep")[]; label?: string }) {
    return (
        <DropdownPrimitive.Root modal={false}>
            <DropdownPrimitive.Trigger asChild>
                <button type="button" aria-label={label}
                        className="grid size-10 place-items-center rounded-xl border border-border-strong text-muted-foreground transition-colors hover:border-subtle hover:bg-card-raised hover:text-foreground data-[state=open]:bg-card-raised data-[state=open]:text-foreground">
                    <DotsThreeVertical weight="bold" className="size-5"/>
                </button>
            </DropdownPrimitive.Trigger>
            <DropdownPrimitive.Portal>
                <DropdownPrimitive.Content align="end" sideOffset={6} collisionPadding={12}
                                           className="z-[60] min-w-56 rounded-xl border border-border-strong bg-card-raised p-1.5 shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
                    {items.map((item, i) => item === "sep" ? (
                        <DropdownPrimitive.Separator key={i} className="my-1.5 h-px bg-border"/>
                    ) : (
                        <DropdownPrimitive.Item key={item.label} disabled={item.disabled} onSelect={item.onSelect}
                                                className={cn(
                                                    "flex h-10 cursor-pointer select-none items-center gap-3 rounded-lg px-3 text-[14px] outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-40",
                                                    item.danger ? "text-danger data-[highlighted]:bg-danger/10" : "text-foreground data-[highlighted]:bg-card",
                                                )}>
                            <span className={cn("size-[18px] [&>svg]:size-[18px]", !item.danger && "text-muted-foreground")}>{item.icon}</span>
                            {item.label}
                        </DropdownPrimitive.Item>
                    ))}
                </DropdownPrimitive.Content>
            </DropdownPrimitive.Portal>
        </DropdownPrimitive.Root>
    )
}

export function Switch({checked, onChange, disabled, label}: {
    checked: boolean
    onChange: (v: boolean) => void
    disabled?: boolean
    label?: string
}) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            aria-label={label}
            disabled={disabled}
            onClick={() => onChange(!checked)}
            className={cn(
                "relative inline-flex h-[26px] w-[46px] shrink-0 items-center rounded-full border transition-colors disabled:opacity-50",
                checked ? "border-transparent bg-brand" : "border-border-strong bg-input hover:border-subtle",
            )}
        >
            <span
                className={cn(
                    "absolute size-5 rounded-full shadow transition-transform duration-150",
                    checked ? "translate-x-[22px] bg-[#1A0F08]" : "translate-x-[3px] bg-muted-foreground",
                )}
            />
        </button>
    )
}

export function Checkbox({checked, onChange, label}: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
    return (
        <button
            type="button"
            role="checkbox"
            aria-checked={checked}
            aria-label={label}
            onClick={() => onChange(!checked)}
            className={cn(
                "grid size-5 shrink-0 place-items-center rounded-md border transition-colors",
                checked ? "border-transparent bg-brand text-[#1A0F08]" : "border-border-strong bg-input hover:border-amber/60",
            )}
        >
            {checked && (
                <svg viewBox="0 0 12 12" className="size-3" aria-hidden>
                    <path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
            )}
        </button>
    )
}

/** Переключатель вариантов (вместо вкладок-кнопочек). */
export function Segmented<T extends string>({value, onChange, options, size = "md"}: {
    value: T
    onChange: (v: T) => void
    options: readonly (readonly [T, React.ReactNode])[]
    size?: "sm" | "md"
}) {
    return (
        <div role="tablist" className="inline-flex max-w-full overflow-x-auto rounded-xl border border-border bg-input p-1">
            {options.map(([v, label]) => (
                <button key={v} role="tab" aria-selected={value === v} onClick={() => onChange(v)}
                        className={cn(
                            "shrink-0 whitespace-nowrap rounded-lg px-3.5 font-medium transition-colors",
                            size === "sm" ? "h-8 text-[13px]" : "h-9 text-[14px]",
                            value === v ? "bg-card-raised text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                        )}>
                    {label}
                </button>
            ))}
        </div>
    )
}

export function Skeleton({className}: { className?: string }) {
    return <div className={cn("animate-pulse rounded-xl bg-muted/50", className)}/>
}

export function Empty({icon, title, children, action}: {
    icon?: React.ReactNode
    title: string
    children?: React.ReactNode
    action?: React.ReactNode
}) {
    return (
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
            {icon && <div className="grid size-12 place-items-center rounded-2xl border border-border bg-card-raised text-amber">{icon}</div>}
            <div>
                <p className="text-[16px] font-medium">{title}</p>
                {children && <p className="mx-auto mt-1.5 max-w-md text-[14px] leading-relaxed text-muted-foreground">{children}</p>}
            </div>
            {action && <div className="mt-1">{action}</div>}
        </div>
    )
}

export function Mono({className, ...props}: React.ComponentProps<"span">) {
    return <span className={cn("font-mono text-[13px] tabular", className)} {...props}/>
}

export function Kbd({children}: { children: React.ReactNode }) {
    return (
        <kbd className="rounded-md border border-border-strong bg-card-raised px-1.5 py-0.5 font-mono text-[11.5px] text-muted-foreground">
            {children}
        </kbd>
    )
}

/** Пояснительная плашка: что это и что делать. */
export function Note({tone = "info", title, children, className}: {
    tone?: "info" | "warn" | "danger" | "ok"
    title?: React.ReactNode
    children: React.ReactNode
    className?: string
}) {
    const tones = {
        info: "border-border-strong bg-card-raised/60",
        warn: "border-warn/30 bg-warn/[0.07]",
        danger: "border-danger/30 bg-danger/[0.07]",
        ok: "border-ok/30 bg-ok/[0.07]",
    }
    return (
        <div className={cn("rounded-xl border px-4 py-3 text-[14px] leading-relaxed", tones[tone], className)}>
            {title && <p className="font-medium text-foreground">{title}</p>}
            <div className={cn("text-muted-foreground", title && "mt-0.5")}>{children}</div>
        </div>
    )
}

export function Dialog({open, onOpenChange, title, description, children, className}: {
    open: boolean
    onOpenChange: (v: boolean) => void
    title: string
    description?: React.ReactNode
    children: React.ReactNode
    className?: string
}) {
    return (
        <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
            <DialogPrimitive.Portal>
                <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/65 data-[state=open]:animate-in data-[state=open]:fade-in-0"/>
                <DialogPrimitive.Content
                    className={cn(
                        "fixed inset-x-0 bottom-0 z-50 max-h-[92dvh] overflow-y-auto rounded-t-2xl border border-border-strong bg-card p-5 pb-[calc(20px+env(safe-area-inset-bottom))] shadow-2xl data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom-4",
                        "sm:inset-x-auto sm:bottom-auto sm:left-1/2 sm:top-1/2 sm:w-[calc(100vw-32px)] sm:max-w-lg sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:p-7 sm:data-[state=open]:zoom-in-95 sm:data-[state=open]:slide-in-from-bottom-0",
                        className,
                    )}
                >
                    <DialogPrimitive.Title className="pr-10 text-[20px] font-semibold tracking-[-0.02em]">{title}</DialogPrimitive.Title>
                    {description ? (
                        <DialogPrimitive.Description className="mt-2 text-[14.5px] leading-relaxed text-muted-foreground">{description}</DialogPrimitive.Description>
                    ) : (
                        <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
                    )}
                    <div className="mt-6">{children}</div>
                    <DialogPrimitive.Close className="absolute right-4 top-4 grid size-10 place-items-center rounded-xl text-muted-foreground hover:bg-card-raised hover:text-foreground" aria-label="Закрыть">
                        <X className="size-5"/>
                    </DialogPrimitive.Close>
                </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
    )
}
