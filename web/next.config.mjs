/** @type {import('next').NextConfig} */
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8200"

const nextConfig = {
    reactStrictMode: true,
    output: "standalone",
    poweredByHeader: false,
    // Браузер ходит только на свой origin: /api/* уходит в backend.
    // Так cookie сессии живёт на одном домене и не нужен CORS.
    async rewrites() {
        return [{source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*`}]
    },
    async headers() {
        return [
            {
                source: "/:path*",
                headers: [
                    {key: "X-Content-Type-Options", value: "nosniff"},
                    {key: "Referrer-Policy", value: "same-origin"},
                    {key: "X-Frame-Options", value: "DENY"},
                    {key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()"},
                ],
            },
        ]
    },
}

export default nextConfig
