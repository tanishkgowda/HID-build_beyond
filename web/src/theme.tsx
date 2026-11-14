// Light / dark / system theme switch. The resolved theme is written to
// <html data-theme="light|dark">, which styles.css keys its tokens on.
// index.html applies the stored choice before first paint, so there is no flash.

import { useEffect, useState } from 'react';

export type ThemePref = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'kiosk-theme';
const DEFAULT_PREF: ThemePref = 'light';
const OPTIONS: Array<{ value: ThemePref; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
];

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'light' || v === 'dark' || v === 'system') return v;
  } catch {
    // Storage blocked (private window, kiosk policy): fall back to the default.
  }
  return DEFAULT_PREF;
}

function writePref(pref: ThemePref) {
  try {
    localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    // Not persisted; the choice still applies for this session.
  }
}

const systemDark = () => window.matchMedia('(prefers-color-scheme: dark)').matches;

function applyPref(pref: ThemePref) {
  const dark = pref === 'dark' || (pref === 'system' && systemDark());
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(readPref);

  useEffect(() => {
    applyPref(pref);
    writePref(pref);
    if (pref !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => applyPref('system');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [pref]);

  return (
    <div className="segmented" role="radiogroup" aria-label="Theme">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={pref === o.value}
          className={pref === o.value ? 'is-active' : undefined}
          onClick={() => setPref(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
