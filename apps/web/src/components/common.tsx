export function SignalMark() {
  return <div className="signal-mark" aria-hidden="true"><span>SIGNAL MAP</span><div>{Array.from({ length: 34 }, (_, index) => <i key={index} style={{ height: `${20 + ((index * 29) % 70)}%` }} />)}</div><small>TRANSCRIPT → MOMENTS → CLIPS</small></div>;
}
