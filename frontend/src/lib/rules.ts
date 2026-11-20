/**
 * Client-side port of the backend rules engine (app/services/questionnaire.py).
 * Same JSON definition, same semantics → instant UX; the server re-validates authoritatively.
 */
import type { Answers, Condition, DocumentSpec, Question, Validator } from '../api/types'

const truthy = (v: unknown) => !(v === undefined || v === null || v === '' || v === false || v === 'false' || v === 'no')

export function evaluate(cond: Condition | undefined, a: Answers): boolean {
  if (!cond) return true
  if ('all' in cond) return cond.all.every((c) => evaluate(c, a))
  if ('any' in cond) return cond.any.some((c) => evaluate(c, a))
  if ('not' in cond) return !evaluate(cond.not, a)
  const v = a[cond.q]
  switch (cond.op) {
    case 'eq': return v === cond.value
    case 'ne': return v !== cond.value
    case 'in': return (cond.value as unknown[]).includes(v)
    case 'not_in': return !(cond.value as unknown[]).includes(v)
    case 'truthy': return truthy(v)
    case 'falsy': return !truthy(v)
    case 'gt': return v !== undefined && Number(v) > Number(cond.value)
    case 'lt': return v !== undefined && Number(v) < Number(cond.value)
  }
}

export const isVisible = (q: { visible_when?: Condition }, a: Answers) => evaluate(q.visible_when, a)
export const isRequired = (q: Question | DocumentSpec, a: Answers) =>
  ('required' in q && q.required === true) || (q.required_when !== undefined && evaluate(q.required_when, a))

/** NPI check digit — Luhn over "80840" + first 9 digits (CMS). */
export function npiValid(npi: string): boolean {
  if (!/^\d{10}$/.test(npi)) return false
  const digits = ('80840' + npi.slice(0, 9)).split('').map(Number)
  let total = 0
  digits.reverse().forEach((d, i) => {
    let x = d
    if (i % 2 === 0) { x *= 2; if (x > 9) x -= 9 }
    total += x
  })
  return (10 - (total % 10)) % 10 === Number(npi[9])
}

const ICD10 = /^[A-TV-Z]\d[0-9A-Z](\.[0-9A-Z]{1,4})?$/
const CPT = /^(\d{4}[0-9A-Z]|\d{5})$/
export const splitCodes = (s: string) => s.split(/[,\s;]+/).map((c) => c.trim().toUpperCase()).filter(Boolean)

function runValidator(v: Validator, value: unknown): string | null {
  const s = String(value)
  switch (v.type) {
    case 'pattern': return new RegExp(`^(?:${v.value})$`).test(s) ? null : v.message ?? 'Invalid format'
    case 'min_length': return s.length >= Number(v.value) ? null : v.message ?? `Minimum ${v.value} characters`
    case 'min': return Number(value) >= Number(v.value) ? null : v.message ?? `Must be ≥ ${v.value}`
    case 'max': return Number(value) <= Number(v.value) ? null : v.message ?? `Must be ≤ ${v.value}`
    case 'past_date': return new Date(s) < new Date(new Date().toDateString()) ? null : v.message ?? 'Date must be in the past'
    case 'npi': return npiValid(s) ? null : v.message ?? 'Invalid NPI (check digit failed)'
    case 'icd10_list': {
      const bad = splitCodes(s).filter((c) => !ICD10.test(c))
      return bad.length ? `Invalid ICD-10: ${bad.join(', ')}` : null
    }
    case 'cpt_list': {
      const bad = splitCodes(s).filter((c) => !CPT.test(c))
      return bad.length ? `Invalid CPT: ${bad.join(', ')}` : null
    }
  }
  return null
}

export function validateQuestion(q: Question, a: Answers): string | null {
  if (!isVisible(q, a)) return null
  const value = a[q.id]
  const empty = value === undefined || value === null || value === ''
  if (empty) return isRequired(q, a) ? 'This field is required' : null
  for (const v of q.validators ?? []) {
    const err = runValidator(v, value)
    if (err) return err
  }
  return null
}

/** Strip answers to hidden questions — mirrors what the server persists. */
export function visibleAnswers(questions: Question[], a: Answers): Answers {
  const out: Answers = {}
  for (const q of questions) if (isVisible(q, a) && a[q.id] !== undefined) out[q.id] = a[q.id]
  return out
}
