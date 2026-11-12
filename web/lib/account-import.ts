type Row = {account_id: string; email: string; password: string}

const ACCOUNT_ID = /^[0-9]{3,12}$/
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const BLOCK_HEADER = /^аккаунт\s+(?:[№#]\s*)?[0-9]+\s*[:.]?\s*\\?$/i
const FIELDS = new Map<string, keyof Row>([
    ["почта", "email"], ["email", "email"], ["e-mail", "email"],
    ["логин", "account_id"], ["номер аккаунта", "account_id"], ["login", "account_id"], ["account id", "account_id"],
    ["пароль", "password"], ["password", "password"],
])

function parseLine(line: string): Row {
    const first = line.indexOf("|")
    const last = line.lastIndexOf("|")
    const email = first < 0 ? "" : line.slice(0, first).trim().replace(/^\d+(?:\\?[.)])?\s+/, "")
    const password = first !== last ? line.slice(first + 1, last).trim() : ""
    const account_id = line.slice(last + 1).trim().replace(/\\$/, "").trimEnd()
    return {account_id, email, password}
}

function readBlockValue(value: string = "", markdown: boolean): string {
    if (!markdown) return value
    // В исходном Markdown обратная косая черта в конце строки обозначает перенос.
    // Внутри строки Markdown может экранировать любой ASCII-знак пунктуации.
    return value.replace(/\\$/, "").trimEnd().replace(/\\([!-/:-@[-`{-~])/g, "$1")
}

export function parseAccountImport(text: string): {rows: Row[]; invalid: number} {
    const rows: Row[] = []
    let invalid = 0
    let block: Partial<Row> | null = null
    let markdown = false
    let duplicate = false

    function add(row: Row) {
        if (ACCOUNT_ID.test(row.account_id) && (!row.email || EMAIL.test(row.email))) rows.push(row)
        else invalid++
    }

    function flushBlock() {
        if (block) {
            const email = readBlockValue(block.email, markdown)
            const account_id = readBlockValue(block.account_id, markdown)
            const password = readBlockValue(block.password, markdown)
            if (!duplicate && email && password) add({account_id, email, password})
            else invalid++
        }
        block = null
        markdown = false
        duplicate = false
    }

    for (const raw of text.split(/\r\n?|\n/)) {
        const line = raw.trim()
        if (line.startsWith("#")) continue
        if (BLOCK_HEADER.test(line)) {
            flushBlock()
            markdown = line.endsWith("\\")
            continue
        }
        if (!line || line === "\\") {
            flushBlock()
            continue
        }

        const separator = line.indexOf(":")
        const field = separator < 0 ? undefined : FIELDS.get(line.slice(0, separator).trim().toLowerCase())
        if (field) {
            const value = line.slice(separator + 1).trim()
            block ??= {}
            if (field in block) duplicate = true
            block[field] = value
            if (field !== "password" && value.endsWith("\\")) markdown = true
            continue
        }

        flushBlock()
        add(parseLine(line))
    }
    flushBlock()
    return {rows, invalid}
}
