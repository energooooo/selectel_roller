import {Check, CircleNotch, Info, Warning, WarningCircle} from "@phosphor-icons/react"
import {cn} from "@/lib/utils"

/** Значки уведомлений: тонированный круг в цвет статуса — тип читается краем глаза. */
function Disc({tone, children}: { tone: string; children: React.ReactNode }) {
    return <span className={cn("grid size-7 place-items-center rounded-full border [&>svg]:size-4", tone)}>{children}</span>
}

export const TOAST_ICONS = {
    success: <Disc tone="border-ok/35 bg-ok/12 text-ok"><Check weight="bold"/></Disc>,
    error: <Disc tone="border-danger/35 bg-danger/12 text-danger"><WarningCircle weight="bold"/></Disc>,
    warning: <Disc tone="border-warn/35 bg-warn/12 text-warn"><Warning weight="bold"/></Disc>,
    info: <Disc tone="border-amber/35 bg-amber/12 text-amber"><Info weight="bold"/></Disc>,
    loading: <Disc tone="border-amber/35 bg-amber/12 text-amber"><CircleNotch weight="bold" className="animate-spin"/></Disc>,
}
