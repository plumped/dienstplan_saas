import { useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_LABELS = {
  active: "Aktiv",
  trialing: "Testphase",
  past_due: "Zahlung überfällig",
  canceled: "Gekündigt",
  incomplete: "Zahlung ausstehend",
};

function daysUntil(isoDate) {
  if (!isoDate) return null;
  const diffMs = new Date(isoDate).getTime() - Date.now();
  return Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
}

// Abrechnung (README Block 6): Admin-only Modul (siehe SettingsPanel.jsx),
// spiegelt core/billing_views.py -- Checkout/Portal sind bewusst simple
// Redirects (window.location.href), die eigentliche Zahlungsseite ist
// komplett Stripe-gehostet, hier gibt es kein eigenes Zahlungsformular.
export default function BillingSettings({ onError }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getBillingStatus()
      .then((data) => !cancelled && setStatus(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCheckout() {
    setRedirecting(true);
    try {
      const { checkout_url } = await api.createCheckoutSession();
      window.location.href = checkout_url;
    } catch (e) {
      onError(e.message);
      setRedirecting(false);
    }
  }

  async function handlePortal() {
    setRedirecting(true);
    try {
      const { portal_url } = await api.createBillingPortalSession();
      window.location.href = portal_url;
    } catch (e) {
      onError(e.message);
      setRedirecting(false);
    }
  }

  if (loading || !status) return <p className="loading-state">Einstellungen werden geladen …</p>;

  const trialDaysLeft = status.subscription_status === "trialing" ? daysUntil(status.trial_ends_at) : null;

  return (
    <div className="panel-form">
      <h2>Abrechnung</h2>
      <p className="panel-hint">
        Ein Abo, Preis pro aktivem Mitarbeitenden/Monat -- Zahlungsabwicklung läuft komplett über
        Stripe, keine Kartendaten werden von dieser App gespeichert.
      </p>

      {!status.billing_configured && (
        <p className="panel-hint">
          Abrechnung ist für diesen Betrieb noch nicht eingerichtet -- bitte an den Support wenden.
        </p>
      )}

      <fieldset className="panel-form-group">
        <h3>Status</h3>
        <p>
          <strong>{STATUS_LABELS[status.subscription_status] ?? status.subscription_status}</strong>
        </p>
        {status.subscription_status === "trialing" && (
          <p>
            {trialDaysLeft === null
              ? "Keine zeitliche Einschränkung."
              : trialDaysLeft > 0
                ? `Noch ${trialDaysLeft} Tag${trialDaysLeft === 1 ? "" : "e"} Testphase.`
                : "Die Testphase ist abgelaufen."}
            {" "}Aktive Mitarbeitende: {status.active_employee_count} / {status.trial_employee_limit}.
          </p>
        )}
        {!status.has_active_access && (
          <p className="field-error">
            Kein aktiver Zugriff mehr -- neue Einträge (z. B. neue Dienste, Mitarbeitende) sind
            gesperrt, bis ein Abo abgeschlossen ist. Bestehende Daten bleiben einsehbar.
          </p>
        )}
      </fieldset>

      {status.billing_configured && (
        <div className="panel-form-group">
          <button type="button" className="btn-primary" onClick={handleCheckout} disabled={redirecting}>
            Abo abschliessen
          </button>
          <button type="button" className="btn-ghost" onClick={handlePortal} disabled={redirecting}>
            Abrechnung verwalten
          </button>
        </div>
      )}
    </div>
  );
}
