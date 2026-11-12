"use client"

import Link from "next/link"
import {usePathname, useRouter} from "next/navigation"
import {useEffect, useState} from "react"
import {Command} from "cmdk"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import {
    ChartLineUp, ClockCounterClockwise, Coins, Crosshair, GearSix, Globe, Key, ListBullets, MagnifyingGlass,
    ShieldCheck, SignOut, SquaresFour, Storefront, TreeStructure, X, House,
} from "@phosphor-icons/react"
import {Toaster} from "sonner"
import {TOAST_ICONS} from "@/components/toast-icons"
import {Logo} from "@/components/logo"
import {SLOGAN} from "@/lib/site"
import {Kbd, TooltipProvider} from "@/components/ui/primitives"
import {api, post, useApi} from "@/lib/api"
import {plural} from "@/lib/format"
import {StreamProvider, useStream} from "@/lib/stream"
import type {Overview} from "@/lib/types"
import {cn} from "@/lib/utils"

interface NavItem {
    href: string
    label: string
    icon: React.ComponentType<{ className?: string; weight?: "fill" | "regular" }>
    about: string
}

export const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
    {
        title: "Поиск IP",
        items: [
            {href: "/", label: "Обзор", icon: House, about: "Что происходит сейчас"},
            {href: "/keys", label: "Ключи", icon: Key, about: "Аккаунты Selectel, на которых ищутся IP"},
            {href: "/proxies", label: "Прокси", icon: Globe, about: "Через что ключи ходят в Selectel"},
            {href: "/subnets", label: "Подсети", icon: TreeStructure, about: "В каких подсетях искать адреса"},
        ],
    },
    {
        title: "Результаты",
        items: [
            {href: "/ips", label: "IP на аккаунтах", icon: Crosshair, about: "Что сейчас лежит на аккаунтах"},
            {href: "/history", label: "Найденные IP", icon: ClockCounterClockwise, about: "Все найденные адреса за всё время"},
            {href: "/metrics", label: "Статистика", icon: ChartLineUp, about: "Скорость, ошибки, находки по аккаунтам"},
            {href: "/finance", label: "Финансы", icon: Coins, about: "Балансы, расходы, журнал доходов"},
        ],
    },
    {
        title: "Система",
        items: [
            {href: "/journal", label: "Журнал", icon: ListBullets, about: "Каждое действие поиска по времени"},
            {href: "/settings", label: "Настройки", icon: GearSix, about: "Паузы, регионы, уведомления"},
            {href: "/security", label: "Безопасность", icon: ShieldCheck, about: "Пароль, 2FA, сессии"},
        ],
    },
]

export const NAV: NavItem[] = [
    ...NAV_GROUPS.flatMap((g) => g.items),
    {href: "/market", label: "Маркет IP", icon: Storefront, about: "Скоро: IP и услуги других продавцов"},
]

// На телефоне внизу — главное; остальное в «Ещё».
const MOBILE = ["/", "/keys", "/ips", "/metrics"]

function isActive(pathname: string, href: string) {
    return href === "/" ? pathname === "/" : pathname.startsWith(href)
}

function EngineChip() {
    const {connected} = useStream()
    const {data} = useApi<Overview>("/overview", {refreshInterval: 30_000})
    const hunting = data?.keys.hunting ?? 0
    const tone = !connected ? "bg-danger" : hunting ? "bg-ok" : "bg-subtle"
    const text = !connected
        ? "Нет связи с сервером"
        : hunting
            ? `Ищут IP: ${hunting} ${plural(hunting, "ключ", "ключа", "ключей")}`
            : "Поиск остановлен"
    const short = !connected ? "Нет связи" : hunting ? `Ищут: ${hunting}` : "Стоит"
    return (
        <Link href="/keys" title={text} aria-label={text} className="inline-flex h-9 shrink-0 items-center gap-2.5 whitespace-nowrap rounded-full border border-border bg-card px-3.5 text-[13.5px] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground max-[359px]:px-3">
            <span className="relative flex size-2">
                {connected && hunting > 0 && <span className={cn("absolute inset-0 rounded-full animate-ping-soft", tone)}/>}
                <span className={cn("relative size-2 rounded-full", tone)}/>
            </span>
            <span className="hidden sm:inline">{text}</span>
            <span className="hidden min-[360px]:inline sm:hidden">{short}</span>
        </Link>
    )
}

function CommandPalette({open, setOpen}: { open: boolean; setOpen: (v: boolean) => void }) {
    const router = useRouter()
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
                e.preventDefault()
                setOpen(!open)
            }
        }
        window.addEventListener("keydown", onKey)
        return () => window.removeEventListener("keydown", onKey)
    }, [open, setOpen])
    const go = (href: string) => {
        setOpen(false)
        router.push(href)
    }
    const itemCls = "flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-[14.5px] text-foreground data-[selected=true]:bg-card-raised"
    const groupCls = "px-1 text-[12.5px] text-subtle [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5"
    return (
        <Command.Dialog
            open={open}
            onOpenChange={setOpen}
            label="Быстрый переход"
            className="fixed left-1/2 top-[15%] z-50 w-[calc(100vw-32px)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-border-strong bg-card shadow-2xl"
            overlayClassName="fixed inset-0 z-50 bg-black/65"
        >
            <div className="flex items-center gap-2 border-b border-border px-4">
                <MagnifyingGlass className="size-5 text-subtle"/>
                <Command.Input placeholder="Раздел или действие, например «добавить ключ»" className="h-14 flex-1 bg-transparent text-[15px] outline-none placeholder:text-subtle"/>
            </div>
            <Command.List className="max-h-[55dvh] overflow-y-auto p-2">
                <Command.Empty className="px-3 py-6 text-center text-[14px] text-muted-foreground">Ничего не нашлось</Command.Empty>
                <Command.Group heading="Действия" className={groupCls}>
                    {[
                        {label: "Добавить ключ Selectel", href: "/keys?add=1"},
                        {label: "Добавить прокси", href: "/proxies?add=1"},
                        {label: "Добавить подсети", href: "/subnets?add=1"},
                        {label: "Скачать найденные IP в CSV", href: "/api/caught.csv"},
                    ].map((a) => (
                        <Command.Item key={a.label} value={a.label} className={itemCls}
                                      onSelect={() => (a.href.startsWith("/api") ? (window.location.href = a.href) : go(a.href))}>
                            <span className="text-amber">+</span>
                            {a.label}
                        </Command.Item>
                    ))}
                </Command.Group>
                {NAV_GROUPS.map((group) => (
                    <Command.Group key={group.title} heading={group.title} className={groupCls}>
                        {group.items.map((item) => (
                            <Command.Item key={item.href} value={`${item.label} ${item.about}`} onSelect={() => go(item.href)} className={itemCls}>
                                <item.icon className="size-5 text-amber"/>
                                <span>{item.label}</span>
                                <span className="ml-auto truncate text-[13px] text-subtle">{item.about}</span>
                            </Command.Item>
                        ))}
                    </Command.Group>
                ))}
            </Command.List>
        </Command.Dialog>
    )
}

/** Вход в маркет — рядом с выходом, заметнее обычного пункта меню. */
function MarketLink({onNavigate, className}: { onNavigate?: () => void; className?: string }) {
    const pathname = usePathname()
    const active = pathname.startsWith("/market")
    return (
        <Link href="/market" onClick={onNavigate} aria-current={active ? "page" : undefined}
              className={cn(
                  "group flex h-11 items-center gap-3 rounded-xl border px-3 text-[14.5px] font-medium transition-[transform,border-color,background-color] duration-150 ease-[cubic-bezier(.2,.8,.2,1)] hover:-translate-y-px active:translate-y-0 active:scale-[0.985] motion-reduce:transform-none",
                  active ? "border-amber/50 bg-amber/[0.09] text-foreground" : "border-amber/25 bg-amber/[0.04] text-foreground hover:border-amber/45 hover:bg-amber/[0.08]",
                  className,
              )}>
            <Storefront className="size-[19px] text-amber" weight={active ? "fill" : "regular"}/>
            Маркет IP
            <span className="ml-auto rounded-full bg-amber/15 px-2 py-0.5 text-[11.5px] font-medium text-amber">скоро</span>
        </Link>
    )
}

async function logout(router: ReturnType<typeof useRouter>) {
    await post("/auth/logout").catch(() => undefined)
    router.replace("/login")
}

function NavLinks({onNavigate}: { onNavigate?: () => void }) {
    const pathname = usePathname()
    return (
        <nav aria-label="Разделы" className="flex flex-col gap-5">
            {NAV_GROUPS.map((group) => (
                <div key={group.title}>
                    <p className="mb-1.5 px-3 text-[12.5px] font-medium text-subtle">{group.title}</p>
                    <div className="flex flex-col gap-0.5">
                        {group.items.map((item) => {
                            const active = isActive(pathname, item.href)
                            return (
                                <Link key={item.href} href={item.href} onClick={onNavigate}
                                      aria-current={active ? "page" : undefined}
                                      className={cn(
                                          "group relative flex h-10 items-center gap-3 rounded-xl px-3 text-[14.5px] transition-colors",
                                          active ? "bg-card-raised font-medium text-foreground" : "text-muted-foreground hover:bg-card hover:text-foreground",
                                      )}>
                                    {active && <span className="absolute left-0 top-2.5 h-5 w-[3px] rounded-r-full bg-brand"/>}
                                    <item.icon className={cn("size-[19px]", active ? "text-amber" : "text-subtle group-hover:text-muted-foreground")}
                                               weight={active ? "fill" : "regular"}/>
                                    {item.label}
                                </Link>
                            )
                        })}
                    </div>
                </div>
            ))}
        </nav>
    )
}

function Sidebar({onSearch}: { onSearch: () => void }) {
    const router = useRouter()
    return (
        <aside className="sticky top-0 hidden h-dvh w-[252px] shrink-0 flex-col overflow-y-auto border-r border-border bg-background px-3 py-5 lg:flex">
            <Link href="/" className="px-3"><Logo/></Link>
            <button onClick={onSearch}
                    className="mx-0.5 mt-6 flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-3 text-[14px] text-subtle transition-colors hover:border-border-strong hover:text-muted-foreground">
                <MagnifyingGlass className="size-[18px]"/>
                <span className="flex-1 text-left">Найти раздел</span>
                <Kbd>Ctrl K</Kbd>
            </button>
            <div className="mt-6 flex-1"><NavLinks/></div>
            <MarketLink className="mt-4"/>
            <button onClick={() => logout(router)}
                    className="mt-1.5 flex h-10 items-center gap-3 rounded-xl px-3 text-[14.5px] text-muted-foreground transition-colors hover:bg-card hover:text-foreground">
                <SignOut className="size-[19px] text-subtle"/>
                Выйти
            </button>
        </aside>
    )
}

function MobileNav() {
    const pathname = usePathname()
    const router = useRouter()
    const [open, setOpen] = useState(false)
    const moreActive = !MOBILE.some((h) => isActive(pathname, h))
    return (
        <>
            <nav aria-label="Главные разделы"
                 className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-border bg-background pb-[env(safe-area-inset-bottom)] lg:hidden">
                {NAV.filter((n) => MOBILE.includes(n.href)).map((item) => {
                    const active = isActive(pathname, item.href)
                    return (
                        <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                              className={cn("flex h-16 flex-col items-center justify-center gap-1 text-[11.5px]",
                                  active ? "text-foreground" : "text-subtle")}>
                            <item.icon className={cn("size-6", active && "text-amber")} weight={active ? "fill" : "regular"}/>
                            {item.href === "/ips" ? "IP" : item.href === "/metrics" ? "Статистика" : item.label}
                        </Link>
                    )
                })}
                <button onClick={() => setOpen(true)} aria-expanded={open}
                        className={cn("flex h-16 flex-col items-center justify-center gap-1 text-[11.5px]", moreActive ? "text-foreground" : "text-subtle")}>
                    <SquaresFour className={cn("size-6", moreActive && "text-amber")} weight={moreActive ? "fill" : "regular"}/>
                    Ещё
                </button>
            </nav>
            <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
                <DialogPrimitive.Portal>
                    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/65 lg:hidden"/>
                    <DialogPrimitive.Content className="fixed inset-x-0 bottom-0 z-50 max-h-[88dvh] overflow-y-auto rounded-t-2xl border-t border-border-strong bg-card px-4 pb-[calc(16px+env(safe-area-inset-bottom))] pt-4 data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom-6 lg:hidden">
                        <div className="mb-4 flex items-center justify-between">
                            <DialogPrimitive.Title className="px-2 text-[17px] font-semibold">Все разделы</DialogPrimitive.Title>
                            <DialogPrimitive.Description className="sr-only">Навигация по панели</DialogPrimitive.Description>
                            <DialogPrimitive.Close className="grid size-10 place-items-center rounded-xl text-muted-foreground hover:bg-card-raised" aria-label="Закрыть">
                                <X className="size-5"/>
                            </DialogPrimitive.Close>
                        </div>
                        <NavLinks onNavigate={() => setOpen(false)}/>
                        <MarketLink className="mt-4" onNavigate={() => setOpen(false)}/>
                        <button onClick={() => logout(router)}
                                className="mt-1.5 flex h-11 w-full items-center gap-3 rounded-xl px-3 text-[15px] text-muted-foreground hover:bg-card-raised">
                            <SignOut className="size-5 text-subtle"/>
                            Выйти
                        </button>
                    </DialogPrimitive.Content>
                </DialogPrimitive.Portal>
            </DialogPrimitive.Root>
        </>
    )
}

function AuthGate({children}: { children: React.ReactNode }) {
    const router = useRouter()
    const [ok, setOk] = useState(false)
    useEffect(() => {
        api<{ needs_setup: boolean; authenticated: boolean }>("/auth/state")
            .then((s) => (s.authenticated ? setOk(true) : router.replace("/login")))
            .catch(() => router.replace("/login"))
    }, [router])
    if (!ok) {
        return (
            <div className="grid min-h-dvh place-items-center">
                <div className="size-8 animate-spin rounded-full border-2 border-border-strong border-t-amber"/>
            </div>
        )
    }
    return <>{children}</>
}

function PageTitle() {
    const pathname = usePathname()
    const item = NAV.find((n) => isActive(pathname, n.href))
    useEffect(() => {
        // На главной — название со слоганом, в разделах — раздел первым, чтобы вкладки различались.
        document.title = !item || item.href === "/" ? `Toffee Roller — ${SLOGAN}` : `${item.label} · Toffee Roller`
    }, [item])
    return null
}

export function Shell({children}: { children: React.ReactNode }) {
    const [paletteOpen, setPaletteOpen] = useState(false)
    return (
        <AuthGate>
            <TooltipProvider>
                <StreamProvider>
                    <PageTitle/>
                    <div className="flex min-h-dvh">
                        <Sidebar onSearch={() => setPaletteOpen(true)}/>
                        <div className="min-w-0 flex-1">
                            <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-border bg-background px-4 sm:px-6 lg:justify-end lg:px-8">
                                <Link href="/" className="lg:hidden" aria-label="На обзор"><Logo/></Link>
                                <div className="flex items-center gap-2">
                                    <button onClick={() => setPaletteOpen(true)} aria-label="Быстрый переход"
                                            className="grid size-10 place-items-center rounded-xl text-muted-foreground hover:bg-card lg:hidden">
                                        <MagnifyingGlass className="size-5"/>
                                    </button>
                                    <EngineChip/>
                                </div>
                            </header>
                            <main className="mx-auto w-full max-w-[1240px] px-4 pb-32 pt-7 sm:px-6 lg:px-8 lg:pb-16 lg:pt-9">
                                {children}
                            </main>
                        </div>
                    </div>
                    <MobileNav/>
                    <CommandPalette open={paletteOpen} setOpen={setPaletteOpen}/>
                    <Toaster theme="dark" position="bottom-right" closeButton offset={{bottom: 24}} mobileOffset={{bottom: 88}}
                             gap={10} icons={TOAST_ICONS} toastOptions={{className: "toffee-toast", closeButtonAriaLabel: "Закрыть уведомление"}}/>
                </StreamProvider>
            </TooltipProvider>
        </AuthGate>
    )
}
