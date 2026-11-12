import {cn} from "@/lib/utils"

/**
 * Знак Toffee Roller: карамельная плитка, внутри — закручивающийся виток
 * (рулон ириски и «круг» охоты одновременно). SVG — чёткий в любом размере.
 */
export function LogoMark({className}: { className?: string }) {
    return (
        <svg viewBox="0 0 32 32" aria-hidden className={cn("size-8", className)}>
            <defs>
                <linearGradient id="tr-g" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#FFC46B"/>
                    <stop offset="0.48" stopColor="#FF9528"/>
                    <stop offset="1" stopColor="#E0631A"/>
                </linearGradient>
            </defs>
            <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#tr-g)"/>
            <path
                d="M16 9.5a6.5 6.5 0 1 1-6.5 6.5 4.6 4.6 0 0 1 4.6-4.6 3.3 3.3 0 0 1 3.3 3.3 2 2 0 0 1-2 2"
                fill="none"
                stroke="#1A0F08"
                strokeWidth="2.3"
                strokeLinecap="round"
            />
        </svg>
    )
}

export function Logo({className}: { className?: string }) {
    return (
        <span className={cn("inline-flex shrink-0 items-center gap-2.5", className)}>
            <LogoMark/>
            <span className="whitespace-nowrap text-[17px] font-semibold tracking-[-0.02em]">
                Toffee <span className="text-brand">Roller</span>
            </span>
        </span>
    )
}
