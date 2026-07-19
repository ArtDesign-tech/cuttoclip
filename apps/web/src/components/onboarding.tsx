import { useState } from "react";
import { KeyRound, ShieldCheck, Ticket } from "lucide-react";
import type { T } from "../app/types";
import type { ProviderMode } from "../types";
import { activateManaged, saveByokCredentials, setProviderMode } from "../lib/provider";

type Step = "choose" | "managed" | "byok";

export function OnboardingScreen({ t, onComplete, onSkip }: { t: T; onComplete: () => void; onSkip: () => void }) {
  const [step, setStep] = useState<Step>("choose");
  const [invite, setInvite] = useState("");
  // Two optional key slots per provider; the worker rotates through them.
  const [groqKeys, setGroqKeys] = useState(["", ""]);
  const [geminiKeys, setGeminiKeys] = useState(["", ""]);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const setKeyAt = (setter: typeof setGroqKeys, index: number) => (value: string) =>
    setter((current) => current.map((key, i) => (i === index ? value : key)));

  const finish = async (mode: ProviderMode) => {
    setBusy(true);
    setNotice("");
    try {
      const action = mode === "managed"
        ? await activateManaged(invite.trim())
        : await saveByokCredentials(groqKeys, geminiKeys);
      if (!action.ok) {
        setNotice(action.needsDesktop ? t("onboarding.needsDesktop") : action.message);
        return;
      }
      const modeResult = await setProviderMode(mode);
      if (!modeResult.ok) {
        setNotice(modeResult.needsDesktop ? t("onboarding.needsDesktop") : modeResult.message);
        return;
      }
      onComplete();
    } finally {
      setBusy(false);
    }
  };

  const managedReady = invite.trim().length > 0 && agreed;
  // At least one key per provider is required; the second slot is optional.
  const byokReady = groqKeys.some((k) => k.trim()) && geminiKeys.some((k) => k.trim()) && agreed;

  return (
    <section className="onboarding-screen content-width">
      <header>
        <span className="eyebrow"><span />{t("onboarding.eyebrow")}</span>
        <h1>{t("onboarding.title")}</h1>
        <p>{t("onboarding.copy")}</p>
      </header>

      {step === "choose" && (
        <div className="onboarding-choices">
          <button className="onboarding-card" onClick={() => { setStep("managed"); setNotice(""); }}>
            <Ticket size={22} />
            <b>{t("onboarding.managedTitle")}</b>
            <span>{t("onboarding.managedCopy")}</span>
          </button>
          <button className="onboarding-card" onClick={() => { setStep("byok"); setNotice(""); }}>
            <KeyRound size={22} />
            <b>{t("onboarding.byokTitle")}</b>
            <span>{t("onboarding.byokCopy")}</span>
          </button>
          <button className="text-button onboarding-skip" onClick={onSkip}>{t("onboarding.skip")}</button>
        </div>
      )}

      {step === "managed" && (
        <div className="onboarding-form panel">
          <label htmlFor="invite-code">{t("onboarding.inviteLabel")}</label>
          <input id="invite-code" type="text" value={invite} onChange={(e) => setInvite(e.target.value)} placeholder={t("onboarding.invitePlaceholder")} autoComplete="off" />
          <PrivacyNotice t={t} agreed={agreed} setAgreed={setAgreed} />
          {notice && <div className="inline-error" role="alert">{notice}</div>}
          <div className="onboarding-actions">
            <button className="secondary-button" onClick={() => setStep("choose")} disabled={busy}>{t("onboarding.back")}</button>
            <button className="primary-button" onClick={() => void finish("managed")} disabled={!managedReady || busy}>{busy ? t("onboarding.activating") : t("onboarding.activate")}</button>
          </div>
        </div>
      )}

      {step === "byok" && (
        <div className="onboarding-form panel">
          <label htmlFor="groq-key-0">{t("onboarding.groqLabel")}</label>
          {groqKeys.map((key, index) => (
            <input
              key={`groq-${index}`}
              id={`groq-key-${index}`}
              type="password"
              value={key}
              onChange={(e) => setKeyAt(setGroqKeys, index)(e.target.value)}
              placeholder={index === 0 ? t("onboarding.groqPlaceholder") : t("onboarding.keyBackupPlaceholder")}
              autoComplete="off"
            />
          ))}
          <label htmlFor="gemini-key-0">{t("onboarding.geminiLabel")}</label>
          {geminiKeys.map((key, index) => (
            <input
              key={`gemini-${index}`}
              id={`gemini-key-${index}`}
              type="password"
              value={key}
              onChange={(e) => setKeyAt(setGeminiKeys, index)(e.target.value)}
              placeholder={index === 0 ? t("onboarding.geminiPlaceholder") : t("onboarding.keyBackupPlaceholder")}
              autoComplete="off"
            />
          ))}
          <PrivacyNotice t={t} agreed={agreed} setAgreed={setAgreed} />
          {notice && <div className="inline-error" role="alert">{notice}</div>}
          <div className="onboarding-actions">
            <button className="secondary-button" onClick={() => setStep("choose")} disabled={busy}>{t("onboarding.back")}</button>
            <button className="primary-button" onClick={() => void finish("byok")} disabled={!byokReady || busy}>{busy ? t("onboarding.saving") : t("onboarding.saveKeys")}</button>
          </div>
        </div>
      )}
    </section>
  );
}

function PrivacyNotice({ t, agreed, setAgreed }: { t: T; agreed: boolean; setAgreed: (value: boolean) => void }) {
  return (
    <div className="onboarding-privacy">
      <div className="onboarding-privacy-head"><ShieldCheck size={17} /><b>{t("onboarding.privacyTitle")}</b></div>
      <p>{t("onboarding.privacyCopy")}</p>
      <label><input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />{t("onboarding.privacyAgree")}</label>
    </div>
  );
}
