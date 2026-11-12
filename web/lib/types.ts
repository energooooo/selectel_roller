export type KeyStatus =
    | "starting" | "hunting" | "sleeping" | "paused" | "error" | "banned" | "auth_failed" | "done"

export interface Runtime {
    state: string
    round: number
    current: string
    attempts_run: number
    caught_run: number
    uptime_s: number
    targets_total: number
    targets_active: number
    paused_regions: Record<string, number>
    proxy: string
    next_action_in: number | null
    last_error: string
    pace?: number
    pace_reason?: string
    skipped_cold?: number
}

export interface Key {
    id: number
    name: string
    account_id: string
    username: string
    project_name: string
    enabled: boolean
    status: KeyStatus
    status_reason: string
    proxy_mode: "auto" | "fixed" | "none"
    proxy_id: number | null
    proxy: { id: number; display: string; label: string; status: string; exit_ip: string } | null
    ip_goal: number | null
    ip_goal_effective: number
    attempts_total: number
    caught_total: number
    held_ips: number
    last_ok_at: string | null
    last_caught_at: string | null
    created_at: string
    rate_per_min: number
    runtime: Runtime | null
    pace: number
    pace_reason: string
}

export interface ProxyItem {
    id: number
    display: string
    label: string
    kind: string
    session_mode: "none" | "password_suffix"
    status: "ok" | "partial" | "dead" | "unchecked"
    exit_ip: string
    exit_country: string
    latency_ms: number | null
    regions_ok: string[]
    regions_failed: string[]
    last_error: string
    checked_at: string | null
    keys: number
}

export interface SubnetItem {
    id: number
    cidr: string
    region: string
    white_count: number | null
    enabled: boolean
    source: string
    note: string
    caught_total: number
    offered: boolean
    attempts_total: number
    heat: number
    tier: "hot" | "normal" | "cold" | "new"
    weight: number
}

export interface Caught {
    id: number
    ip: string
    region: string
    key_id: number | null
    key_name: string | null
    account_id: string
    pool_cidr: string
    floating_ip_id: string
    caught_at: string
    attempts_spent: number | null
    protected: boolean
    on_account: boolean
    released_at: string | null
    check_status: "pending" | "ok" | "bad" | "error" | "skipped"
    checked_at: string | null
    note: string
    source: string
    client: string
    tags: string[]
    expires_at: string | null
    income: number
    vds: VdsBrief | null
}

export interface VdsBrief {
    id: number
    status: "creating" | "active" | "error" | "deleting" | "deleted"
    step: string
    flavor: string
    zone: string
    error: string
    month_cost: number | null
    has_password: boolean
}

export interface VdsQuote {
    region: string
    zone: string | null
    flavor: string
    server_month: number | null
    disk_gb: number
    disk_month: number | null
    router_month: number
    total_month: number
    total_with_router: number
    available: boolean
}

export interface AccountMoney {
    account_id: string
    keys: string[]
    balance: number | null
    hours_left: number | null
    days_left: number | null
    error: string
    checked_at: string | null
    ips: number
    ip_month: number
    vds: number
    vds_month: number
    month: number
    day: number
}

export type SaleKind = "ip" | "rent" | "vds" | "service" | "other"

export interface Sale {
    id: number
    ts: string
    kind: SaleKind
    amount: number
    buyer: string
    caught_id: number | null
    ip: string
    note: string
}

export interface FinanceSummary {
    balances: AccountMoney[]
    balance_total: number
    cost_month: number
    cost_day: number
    ips_held: number
    income_month: number
    income_prev_month: number
    income_total: number
    profit_month: number
    by_kind: { kind: SaleKind; label: string; amount: number }[]
    months: { month: string; income: number }[]
}

export interface EventItem {
    id?: number
    ts: string
    level: "debug" | "info" | "success" | "warn" | "error"
    kind: string
    key_id: number | null
    region: string
    message: string
    data: Record<string, unknown>
}

export interface Overview {
    keys: { total: number; enabled: number; hunting: number; problems: number }
    proxies: { total: number; ok: number; partial: number; dead: number }
    subnets: { total: number; enabled: number }
    caught: { total: number; today: number; held: number }
    rate_per_min: number
    attempts_24h: number
    last_caught: Caught[]
}

export interface FloatingIp {
    floating_ip_id: string
    ip: string
    region: string
    status: string
    attached: boolean
    white: boolean
    subnet: string | null
    caught: { id: number; caught_at: string; protected: boolean; check_status: string; source: string } | null
}

export type Settings = Record<string, unknown> & {
    busy_delay_min: number
    busy_delay_max: number
    round_delay_min: number
    round_delay_max: number
    long_pause_every: number
    long_pause_min: number
    long_pause_max: number
    ip_goal: number
    request_timeout: number
    connect_timeout: number
    max_consecutive_errors: number
    start_stagger: number
    proxy_check_interval_min: number
    regions: string[]
    exact_only: boolean
    tg_chat_id: string
    tg_admin_chat_id: string
    tg_bot_token_set: boolean
    notify_caught: boolean
    notify_alerts: boolean
    notify_logins: boolean
    notify_rotations: boolean
    notify_daily: boolean
    bsboard_url: string
    bsboard_key_set: boolean
    bsboard_recheck_hours: number
    pace_governor: boolean
    pace_max: number
    pace_relax_min: number
    hot_priority: boolean
    cold_share: number
    balance_check_min: number
    balance_warn_days: number
    ip_expiry_warn_days: number
    notify_money: boolean
    vds_disk_gb: number
}
