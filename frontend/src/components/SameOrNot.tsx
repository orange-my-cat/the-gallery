/**
 * "Are they the same person?" - quick questions on the People screen that
 * teach the matcher (see backend people/services/questions.py). The app asks
 * about the faces it is least sure of; each answer is an undoable change and
 * is followed by a quick re-match. Keys: Y yes, N no, F not a face, S skip.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { keys } from '@/lib/queries'
import type { Question } from '@/lib/types'

type Answer = 'yes' | 'no' | 'notface' | 'skip'

export function SameOrNot() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const [queue, setQueue] = useState<Question[]>([])
  const [left, setLeft] = useState(0)
  const [people, setPeople] = useState<number | null>(null)
  const [answered, setAnswered] = useState(0)
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<{ opId: string | null; text: string } | null>(null)
  const [hidden, setHidden] = useState(false)
  const skipped = useRef<string[]>([])

  const load = useCallback(async (keep: Question[] = []) => {
    const body = await api.peopleQuestions(skipped.current)
    const have = new Set(keep.map((q) => q.face.id))
    setQueue([...keep, ...body.questions.filter((q) => !have.has(q.face.id))])
    setLeft(body.left ?? 0)
    setPeople(body.people)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const q = queue[0]
  const who = q?.person.name || 'this person'

  const answer = useCallback(
    async (kind: Answer) => {
      if (!q || busy) return
      setBusy(true)
      try {
        let opId: string | null = null
        if (kind === 'yes') opId = (await api.assignFaces([q.face.id], q.person.id)).operationId
        else if (kind === 'no') opId = (await api.notThem(q.face.id, q.person.id)).operationId
        else if (kind === 'notface') opId = (await api.ignoreFaces([q.face.id])).operationId
        else skipped.current = [...skipped.current, q.face.id]
        const text = { yes: `Yes, ${who}`, no: `Not ${who}`, notface: 'Not a face', skip: 'Skipped' }[kind]
        setLast({ opId, text })
        if (kind !== 'skip') setAnswered((n) => n + 1)
        const rest = queue.slice(1)
        setQueue(rest)
        // Answers change what is worth asking: top up from the server.
        if (rest.length < 3) await load(rest)
        if ((answered + 1) % 5 === 0) void client.invalidateQueries({ queryKey: keys.people })
      } finally {
        setBusy(false)
      }
    },
    [q, busy, who, queue, load, answered, client],
  )

  const undo = async () => {
    if (!last?.opId) return
    await api.undo(last.opId)
    setLast(null)
    setAnswered((n) => Math.max(0, n - 1))
    await load()
    void client.invalidateQueries({ queryKey: keys.people })
  }

  useEffect(() => {
    if (hidden) return
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.ctrlKey || e.metaKey || e.altKey) return
      const kind = ({ y: 'yes', n: 'no', f: 'notface', s: 'skip' } as const)[e.key.toLowerCase() as 'y' | 'n' | 'f' | 's']
      if (kind) {
        e.preventDefault()
        void answer(kind)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [answer, hidden])

  if (people === null || people === 0) return null // nobody confirmed yet: nothing to ask

  return (
    <section className="same-or-not">
      <div className="son-head">
        <h2>Are they the same person?</h2>
        <span className="muted num">
          {q
            ? `${count(left)} ${plural(left, 'face')} the app isn’t sure about · your answers teach it${answered ? ` · ${count(answered)} answered` : ''}`
            : 'Nothing to ask right now'}
        </span>
        <div className="grow" />
        <button type="button" className="pill-btn ghost small" onClick={() => setHidden((h) => !h)}>
          {hidden ? 'Show' : 'Hide'}
        </button>
      </div>

      {!hidden && q && (
        <div className={`son-card${busy ? ' busy' : ''}`}>
          <div className="son-face">
            <button
              type="button"
              className="face-big"
              style={{ backgroundImage: `url("${q.face.url}")` }}
              title="Open the photo"
              onClick={() => navigate(`/photo/${q.face.assetId}`)}
            />
          </div>
          <div className="son-q">
            <b>{q.matched ? `Is this really ${who}?` : `Is this ${who}?`}</b>
            <span className="muted">{q.matched ? 'The app put them together, only just.' : 'The app thinks it might be.'}</span>
            <div className="son-them">
              {q.person.faces.map((url) => (
                <span key={url} className="face-small" style={{ backgroundImage: `url("${url}")` }} />
              ))}
            </div>
            <div className="son-actions">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void answer('yes')}>
                <Icon name="check" size={15} />
                Yes <kbd>Y</kbd>
              </button>
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void answer('no')}>
                <Icon name="x" size={15} />
                No <kbd>N</kbd>
              </button>
              <button type="button" className="pill-btn" disabled={busy} onClick={() => void answer('notface')}>
                Not a face <kbd>F</kbd>
              </button>
              <button type="button" className="pill-btn ghost" disabled={busy} onClick={() => void answer('skip')}>
                Skip <kbd>S</kbd>
              </button>
            </div>
          </div>
        </div>
      )}
      {!hidden && last && (
        <p className="son-last muted">
          {`Last: ${last.text}`}
          {last.opId && (
            <button type="button" className="link" onClick={() => void undo()}>
              Undo
            </button>
          )}
        </p>
      )}
    </section>
  )
}
