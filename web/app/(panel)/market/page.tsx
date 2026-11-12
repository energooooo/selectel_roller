"use client"

import Link from "next/link"
import {ArrowLeft, Storefront} from "@phosphor-icons/react"
import {Button} from "@/components/ui/button"
import {Card} from "@/components/ui/primitives"

export default function MarketPage() {
    return (
        <>
            <div className="mb-7">
                <h1 className="flex flex-wrap items-center gap-3 text-[28px] font-semibold tracking-[-0.03em] sm:text-[32px]">
                    Маркет IP
                    <span className="inline-flex h-6 items-center rounded-full border border-amber/40 bg-amber/10 px-2.5 text-[12.5px] font-medium tracking-normal text-amber">
                        скоро
                    </span>
                </h1>
                <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted-foreground">
                    Скоро здесь будет продажа IP-адресов и услуг от других продавцов.
                </p>
            </div>
            <Card className="flex flex-col items-start gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3">
                    <Storefront className="size-5 shrink-0 text-muted-foreground"/>
                    <p className="text-[14.5px] text-muted-foreground">Объявлений пока нет — маркет откроется в одном из следующих обновлений.</p>
                </div>
                <Button variant="outline" size="sm" asChild>
                    <Link href="/"><ArrowLeft/> Вернуться к обзору</Link>
                </Button>
            </Card>
        </>
    )
}
