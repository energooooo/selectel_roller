import type {Metadata, Viewport} from "next"
import {Geist, Geist_Mono} from "next/font/google"
import {SLOGAN} from "@/lib/site"
import "./globals.css"

const geist = Geist({subsets: ["latin", "cyrillic"], variable: "--font-geist", display: "swap"})
const geistMono = Geist_Mono({
    subsets: ["latin", "cyrillic"],
    variable: "--font-geist-mono",
    display: "swap",
    weight: ["400", "500"],
})

export const viewport: Viewport = {
    themeColor: "#0B0908",
    colorScheme: "dark",
    width: "device-width",
    initialScale: 1,
}

export const metadata: Metadata = {
    title: {default: `Toffee Roller — ${SLOGAN}`, template: "%s · Toffee Roller"},
    description: "Панель, которая сама получает IP-адреса из нужных подсетей Selectel и сообщает о находке в Telegram.",
    applicationName: "Toffee Roller",
    // Панель приватная: поисковикам здесь делать нечего.
    robots: {index: false, follow: false},
}

export default function RootLayout({children}: { children: React.ReactNode }) {
    return (
        <html lang="ru" className={`${geist.variable} ${geistMono.variable}`}>
        <body className="min-h-dvh">{children}</body>
        </html>
    )
}
