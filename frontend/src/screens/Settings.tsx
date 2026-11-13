/**
 * Settings (LIB-01): library folder, exclusions, scan schedule, trash
 * retention, metadata write-back and duplicate sensitivity.
 *
 * Not in the Claude Design handoff; built from the design system's own form
 * controls (.field, .input, .seg, .radio, .tag). Each change saves on its own
 * - there is no Save button to forget - and a refusal shows beside its field.
 * Settings whose feature arrives in a later phase say so, and are kept anyway
 * so they are ready when it lands.
 */

import { useEffect, useState, type ReactNode } from 'react'

import { Icon } from '@/components/Icon'
import { ApiError } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useBrowseFolders, useFolderListing, useSaveSettings, useSettings } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { AppSettings, SettingsPatch } from '@/lib/types'

const INTERVALS: readonly [number, string][] = [
  [0, 'Off'],
  [15, '15 min'],
  [60, 'Hourly'],
  [360, 'Every 6 h'],
  [1440, 'Daily'],
]


export function Settings() {
  const settings = useSettings().data
  const save = useSaveSettings()
  const toast = useToast()
  const [errors, setErrors] = useState<Record<string, string>>({})
  const folders = useFolderListing('').data?.folders ?? []

  const apply = (patch: SettingsPatch, what: string) => {
    setErrors((e) => {
      const next = { ...e }
      for (const key of Object.keys(patch)) delete next[key]
      return next
    })
    save.mutate(patch, {
      onSuccess: ({ scanStarted }) =>
        toast.show({
          msg: `${what} saved`,
          detail: scanStarted ? 'Scanning the library again to match' : '',
        }),
      onError: (error) => {
        if (error instanceof ApiError && Object.keys(error.fields).length) setErrors((e) => ({ ...e, ...error.fields }))
        else toast.show({ msg: 'Nothing was saved', detail: error.message, tone: 'error' })
      },
    })
  }

  if (!settings) return <div className="timeline" />

  return (
    <div className="timeline" data-screen-label="Settings">
      <div className="settings">
        <div className="folders-head">
          <span className="display">Settings</span>
        </div>

        <Section title="Library" icon="drive" note="The folder The Gallery shows. Files stay where they are; nothing is copied. Changing the folder or the exclusions starts a scan.">
          <LibraryFolderField settings={settings} error={errors.librarySubfolder} onSave={apply} />
          <ExclusionsField settings={settings} folders={folders.map((f) => f.name)} error={errors.exclusions} onSave={apply} />
        </Section>

        <Section title="Scanning" icon="refresh" note="New, changed, moved and removed files are found by rescanning.">
          <div className="field">
            <label>Rescan automatically</label>
            <div className="seg" role="radiogroup" aria-label="Rescan automatically">
              {withCurrent(settings.scanIntervalMinutes).map(([value, label]) => (
                <label key={value} className="seg-opt">
                  <input
                    type="radio"
                    name="interval"
                    checked={settings.scanIntervalMinutes === value}
                    onChange={() => apply({ scanIntervalMinutes: value }, 'Scan schedule')}
                  />
                  {label}
                </label>
              ))}
            </div>
            <FieldError message={errors.scanIntervalMinutes} />
          </div>
        </Section>

        <Section title="Trash" icon="trash" note="Deleted items wait in a trash folder inside the library before they are removed for good.">
          <NumberField
            label="Keep deleted items for"
            unit="days"
            value={settings.trashRetentionDays}
            min={1}
            max={365}
            error={errors.trashRetentionDays}
            onSave={(n) => apply({ trashRetentionDays: n }, 'Trash retention')}
          />
        </Section>

        <Section title="Metadata" icon="pencil" note="Where titles, ratings, tags and merged details are written, so other apps see them too.">
          <div className="field radios">
            <Radio
              checked={settings.writeBackMode === 'embedded'}
              onChange={() => apply({ writeBackMode: 'embedded' }, 'Write-back')}
              title="Inside the file where it is safe"
              detail="JPEG, HEIC, TIFF, PNG and DNG get embedded XMP. Camera RAW and video get an .xmp file beside them."
            />
            <Radio
              checked={settings.writeBackMode === 'sidecar'}
              onChange={() => apply({ writeBackMode: 'sidecar' }, 'Write-back')}
              title="Always beside the file"
              detail="An .xmp file next to every photo and video. The originals are never changed."
            />
            <FieldError message={errors.writeBackMode} />
          </div>
        </Section>

        <Section title="Duplicates" icon="copy" note="Only identical copies are offered as duplicates: the same bytes, or the same picture to the pixel with different details. Photos that merely look alike never are.">
          <div className="field">
            <label className="radio check">
              <input
                type="checkbox"
                checked={settings.autoMergeExact}
                onChange={(e) => apply({ autoMergeExact: e.target.checked }, 'Automatic merging')}
              />
              <span className="dot" />
              <span className="lines">
                <span>Merge byte-identical copies automatically</span>
                <small className="muted">Only exact copies with no conflicting details. Every merge can be undone.</small>
              </span>
            </label>
          </div>
        </Section>
      </div>
    </div>
  )
}

function withCurrent(current: number): readonly [number, string][] {
  return INTERVALS.some(([v]) => v === current) ? INTERVALS : [...INTERVALS, [current, `Every ${count(current)} min`]]
}

function Section({
  title,
  icon,
  note,
  phase,
  children,
}: {
  readonly title: string
  readonly icon: Parameters<typeof Icon>[0]['name']
  readonly note: string
  readonly phase?: string
  readonly children: ReactNode
}) {
  return (
    <section className="settings-section">
      <div className="settings-intro">
        <div className="settings-title">
          <Icon name={icon} size={17} />
          <b>{title}</b>
          {phase && <span className="tag tag-outline">{`Used from phase ${phase}`}</span>}
        </div>
        <p className="muted">{note}</p>
      </div>
      <div className="settings-fields">{children}</div>
    </section>
  )
}

function FieldError({ message }: { readonly message: string | undefined }) {
  if (!message) return null
  return (
    <small className="field-error" role="alert">
      <Icon name="alert" size={13} />
      {message}
    </small>
  )
}

function Radio(props: { readonly checked: boolean; readonly onChange: () => void; readonly title: string; readonly detail: string }) {
  return (
    <label className="radio">
      <input type="radio" name="writeback" checked={props.checked} onChange={props.onChange} />
      <span className="dot" />
      <span className="lines">
        <span>{props.title}</span>
        <small className="muted">{props.detail}</small>
      </span>
    </label>
  )
}

function NumberField(props: {
  readonly label: string
  readonly unit: string
  readonly value: number
  readonly min: number
  readonly max: number
  readonly error: string | undefined
  readonly onSave: (n: number) => void
}) {
  const [draft, setDraft] = useState(String(props.value))
  useEffect(() => setDraft(String(props.value)), [props.value])
  const commit = () => {
    const n = Number(draft)
    if (draft.trim() === '' || !Number.isInteger(n)) return setDraft(String(props.value))
    if (n !== props.value) props.onSave(n)
  }
  return (
    <div className="field">
      <label>{props.label}</label>
      <div className="inline">
        <input
          className="input num"
          style={{ width: 96 }}
          inputMode="numeric"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === 'Enter' && commit()}
        />
        <span className="muted">{props.unit}</span>
      </div>
      <FieldError message={props.error} />
    </div>
  )
}

interface FolderFieldProps {
  readonly settings: AppSettings
  readonly folders: readonly string[]
  readonly error: string | undefined
  readonly onSave: (patch: SettingsPatch, what: string) => void
}

/** Choose the library folder by browsing, one level at a time (LIB-01). */
function LibraryFolderField({ settings, error, onSave }: Omit<FolderFieldProps, 'folders'>) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(settings.librarySubfolder)
  const listing = useBrowseFolders(at, open)
  const here = listing.data
  const browseError = listing.error instanceof ApiError ? (listing.error.fields.path ?? listing.error.message) : undefined

  const choose = () => {
    if (!here) return
    onSave({ librarySubfolder: here.path }, 'Library folder')
    setOpen(false)
  }

  return (
    <div className="field">
      <label>Library folder</label>
      <div className="inline">
        <span className={`readonly mono${settings.libraryPath ? '' : ' muted'}`}>
          {settings.libraryPath ?? (settings.libraryRoot ? 'Not chosen yet' : 'No library available')}
        </span>
        <div className="grow" />
        <button
          type="button"
          className="pill-btn"
          disabled={!settings.libraryRoot}
          onClick={() => {
            setAt(settings.librarySubfolder)
            setOpen((o) => !o)
          }}
        >
          <Icon name="folder-open" size={15} />
          {settings.libraryPath ? 'Change…' : 'Choose…'}
        </button>
      </div>

      {open && (
        <div className="folder-picker">
          <div className="fp-head">
            <button
              type="button"
              className="icon-btn sm"
              title="Up one level"
              disabled={!here || here.parent === null}
              onClick={() => here && here.parent !== null && setAt(here.parent)}
            >
              <Icon name="arrow-left" size={15} />
            </button>
            <span className="mono">{here?.display ?? '…'}</span>
          </div>
          <ul className="fp-list">
            {here?.folders.map((f) => (
              <li key={f.path}>
                <button type="button" onClick={() => setAt(f.path)}>
                  <Icon name="folder" size={15} />
                  <span className="grow">{f.name}</span>
                  <Icon name="chevron-right" size={14} />
                </button>
              </li>
            ))}
          </ul>
          {here && here.folders.length === 0 && <p className="muted fp-empty">No folders inside.</p>}
          <FieldError message={browseError} />
          <div className="fp-foot">
            <small className="muted">
              {settings.libraryPath
                ? 'Photos outside the new folder leave the timeline after the next two scans. Nothing on disk changes.'
                : 'The first scan starts as soon as you choose.'}
            </small>
            <button type="button" className="pill-btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!here?.canChoose || here.path === settings.librarySubfolder}
              onClick={choose}
            >
              Use this folder
            </button>
          </div>
        </div>
      )}

      <small className="subtle">
        {settings.libraryRoot
          ? settings.mustChoose
            ? `Any folder on ${settings.libraryRoot} that The Gallery can see.`
            : `Inside ${settings.libraryRoot}, set as LIBRARY_ROOT in .env.`
          : 'Set LIBRARY_ROOT in .env to a folder that exists.'}
      </small>
      <FieldError message={error} />
    </div>
  )
}

function ExclusionsField({ settings, folders, error, onSave }: FolderFieldProps) {
  const [draft, setDraft] = useState('')
  const set = (next: readonly string[]) => onSave({ exclusions: [...next] }, 'Excluded folders')
  const add = () => {
    const value = draft.trim()
    if (!value) return
    setDraft('')
    if (!settings.exclusions.some((x) => x.toLowerCase() === value.toLowerCase())) set([...settings.exclusions, value])
  }
  return (
    <div className="field">
      <label htmlFor="exclude">Skip these folders</label>
      {settings.exclusions.length > 0 && (
        <div className="chips">
          {settings.exclusions.map((x) => (
            <span key={x} className="chip mono">
              {x}
              <button type="button" title={`Scan ${x} again`} onClick={() => set(settings.exclusions.filter((y) => y !== x))}>
                <Icon name="x" size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="inline">
        <datalist id="library-folders">
          {folders.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
        <input
          id="exclude"
          className="input mono"
          list="library-folders"
          placeholder="Folder to skip, e.g. Exports"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
        />
        <button type="button" className="pill-btn" disabled={!draft.trim()} onClick={add}>
          <Icon name="plus" size={14} />
          Skip
        </button>
      </div>
      <small className="subtle">
        {settings.exclusions.length
          ? `${count(settings.exclusions.length)} ${plural(settings.exclusions.length, 'folder')} skipped, with everything inside. The trash is always skipped.`
          : 'Everything inside a skipped folder is left out. The trash is always skipped.'}
      </small>
      <FieldError message={error} />
    </div>
  )
}
