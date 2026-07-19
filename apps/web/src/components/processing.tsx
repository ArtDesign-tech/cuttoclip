import { AlertCircle, ArrowLeft, RotateCcw, Square, WandSparkles } from "lucide-react";
import type { Job, JobError } from "../types";
import type { Phase, T } from "../app/types";

export function ProcessingScreen({ t, phase, job, progress, uploadProgress, error, cancelling, onCancel, onRetry, onBack }: { t: T; phase: Phase | null; job: Job | null; progress: number; uploadProgress: number; error: JobError | null; cancelling: boolean; onCancel: () => void; onRetry: () => void; onBack: () => void }) {
  const label = job ? t(job.stageKey, job.stageParams) : phase === "create" && uploadProgress ? t("source.uploading") : t(`job.${phase ?? "starting"}`);
  if (error) return <section className="processing-screen"><div className="processing-symbol error-symbol"><AlertCircle size={28} /></div><span className="eyebrow centered"><span />{t("common.error")}</span><h1>{error.code.replaceAll("_", " ")}</h1><p>{error.message}</p><div className="error-actions"><button className="secondary-button" onClick={onBack}><ArrowLeft size={16} />{t("processing.back")}</button>{error.retryable && <button className="primary-button" onClick={onRetry}><RotateCcw size={16} />{t("processing.retry")}</button>}</div></section>;
  return <section className="processing-screen">
    <div className="processing-symbol"><WandSparkles size={28} /><span /></div>
    <span className="eyebrow centered"><span />{t(`job.${phase ?? "starting"}`)}</span>
    <h1>{t("processing.title")}</h1><p>{t("processing.copy")}</p>
    <div className="progress-block"><div className="progress-copy"><b>{label}</b><span>{Math.round(progress)}%</span></div><div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} aria-label={label}><span style={{ width: `${progress}%` }} /></div><div className="progress-steps"><span className={progress >= 10 ? "done" : "active"}>Source</span><span className={progress >= 75 ? "done" : progress >= 10 ? "active" : ""}>Transcript</span><span className={progress >= 100 ? "done" : progress >= 75 ? "active" : ""}>Moments</span></div></div>
    <button className="cancel-button" onClick={onCancel} disabled={cancelling}><Square size={13} fill="currentColor" />{cancelling ? t("processing.cancelling") : t("processing.cancel")}</button>
  </section>;
}
