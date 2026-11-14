import type { Outcome, Presence } from './api';

interface Props {
  src: string;
  nonce: number;
  presence: Presence | null;
  running: boolean;
  failed: boolean;
  active: boolean;
  outcome: Outcome | null;
  instruction: string;
  onError: () => void;
  onReconnect: () => void;
}

export function CameraView({
  src, nonce, presence, running, failed, active, outcome, instruction, onError, onReconnect,
}: Props) {
  const width = presence?.width || 640;
  const height = presence?.height || 480;
  const face = running && !failed ? presence?.face : null;
  const cx = face ? (face.x + face.width / 2) * width : width / 2;
  const cy = face ? (face.y + face.height / 2) * height : height * .47;
  const radius = face ? Math.min(height * .47, Math.max(face.width * width, face.height * height) * .64) : height * .31;
  const state = outcome === 'ALLOW' ? 'allow' : outcome === 'DENY' ? 'deny'
    : outcome ? 'retry' : active ? 'checking' : face ? 'found' : 'idle';

  return (
    <div className={`entry-view entry-view--${state}`}>
      {running && !failed ? (
        <img className="entry-camera" key={nonce} src={src} alt="Live camera preview" onError={onError} />
      ) : (
        <div className="entry-placeholder">
          {failed && <button className="btn btn-secondary" onClick={onReconnect}>Reconnect preview</button>}
        </div>
      )}

      {!failed && (
        <svg className="entry-guide" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          <circle className={face ? 'tracking-ring' : 'tracking-ring tracking-ring--idle'}
            cx={cx} cy={cy} r={radius} vectorEffect="non-scaling-stroke" />
        </svg>
      )}

      <div className="entry-view-top">
        <span className="entry-feed"><span className={running && !failed ? 'is-live' : ''} />{running && !failed ? 'Live camera' : 'Camera off'}</span>
        {face && <span className="entry-face">Face detected</span>}
      </div>
      <div className="entry-view-bottom"><span>{instruction}</span></div>
    </div>
  );
}
