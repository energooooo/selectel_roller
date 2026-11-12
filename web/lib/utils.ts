import {clsx, type ClassValue} from "clsx"
import {twMerge} from "tailwind-merge"

/** Склейка классов с разрешением конфликтов Tailwind: последний выигрывает. */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs))
}
