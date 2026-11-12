import * as React from "react"
import {Slot} from "@radix-ui/react-slot"
import {cva, type VariantProps} from "class-variance-authority"
import {cn} from "@/lib/utils"

const buttonVariants = cva(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium transition-[transform,opacity,background-color,border-color,filter] duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0",
    {
        variants: {
            variant: {
                brand: "bg-brand text-[#140D08] hover:-translate-y-px hover:brightness-110 active:translate-y-px active:scale-[0.985]",
                outline: "border border-border-strong bg-transparent text-foreground hover:-translate-y-px hover:border-subtle hover:bg-card-raised active:translate-y-px active:scale-[0.985]",
                ghost: "text-muted-foreground hover:bg-card hover:text-foreground",
                danger: "border border-danger/40 bg-danger/10 text-danger hover:bg-danger/20",
            },
            size: {
                sm: "h-10 px-4 text-[14px]",
                md: "h-11 px-5 text-[15px]",
                lg: "h-[52px] px-7 text-base",
                icon: "size-10",
            },
        },
        defaultVariants: {variant: "brand", size: "md"},
    },
)

function Button({
    className,
    variant,
    size,
    asChild = false,
    ...props
}: React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
    const Comp = asChild ? Slot : "button"
    return <Comp className={cn(buttonVariants({variant, size, className}))} {...props}/>
}

export {Button, buttonVariants}
