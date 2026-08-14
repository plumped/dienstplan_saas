import { useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_LABELS = {
  active: "Aktiv",
  trialing: "Testphase",
  past_due: "Zahlung überfällig",
  canceled: "Gekündigt",
  incomplete: "Zahlung ausstehend",
};

const INTERVAL_LABELS = { day: "Tag", week: "Woche", month: "Monat", year: "Jahr" };

const INVOICE_STATUS_LABELS = {
  paid: "bezahlt",
  open: "offen",
  draft: "Entwurf",
  uncollectible: "nicht einziehbar",
  void: "storniert",
};

function daysUntil(isoDate) {
  if (!isoDate) return null;
  const diffMs = new Date(isoDate).getTime() - Date.now();
  return Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
}

// Beträge kommen von Stripe immer in der kleinsten Währungseinheit (Rappen/
// Cents), price_currency ist der ISO-Code (z. B. "chf") -- Intl.NumberFormat
// übernimmt Rundung/Tausendertrennzeichen/Symbol-Position.
function formatMoney(amountInSmallestUnit, currency) {
  if (amountInSmallestUnit == null || !currency) return null;
  return new Intl.NumberFormat("de-CH", { style: "currency", currency: currency.toUpperCase() }).format(
    amountInSmallestUnit / 100
  );
}

function formatDate(isoDate) {
  if (!isoDate) return null;
  return new Date(isoDate).toLocaleDateString("de-CH", { year: "numeric", month: "long", day: "numeric" });
}

// Abrechnung (README Block 6): Admin-only Modul (siehe SettingsPanel.jsx),
// spiegelt core/billing_views.py -- Checkout/Portal sind bewusst simple
// Redirects (window.location.href), die eigentliche Zahlungsseite ist
// komplett Stripe-gehostet, hier gibt es kein eigenes Zahlungsformular.
//
// Nutzer-Feedback (2026-08): "Das ist viel zu wenig Info, ich will
// möglichst viel Informationen zur subscription sehen" -- neben den reinen
// Tenant-Feldern (subscription_status, active_employee_count) zeigt dieser
// Screen jetzt zusätzlich die LIVE-Stripe-Subscription-Details
// (status.subscription, siehe core.billing.get_subscription_details):
// nächste Abrechnung, Preis × Menge, Zahlungsmittel, letzte Rechnung.
// status.subscription ist null, solange noch kein Abo abgeschlossen wurde.
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
  const sub = status.subscription;
  const intervalLabel = sub ? (INTERVAL_LABELS[sub.price_interval] ?? sub.price_interval) : null;

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
        <p>
          Aktive Mitarbeitende: {status.active_employee_count}
          {status.subscription_status === "trialing" && ` / ${status.trial_employee_limit}`}
        </p>
        {status.subscription_status === "trialing" && (
          <p>
            {trialDaysLeft === null
              ? "Keine zeitliche Einschränkung."
              : trialDaysLeft > 0
                ? `Noch ${trialDaysLeft} Tag${trialDaysLeft === 1 ? "" : "e"} Testphase.`
                : "Die Testphase ist abgelaufen."}
          </p>
        )}
        {!status.has_active_access && (
          <p className="field-error">
            Kein aktiver Zugriff mehr -- neue Einträge (z. B. neue Dienste, Mitarbeitende) sind
            gesperrt, bis ein Abo abgeschlossen ist. Bestehende Daten bleiben einsehbar.
          </p>
        )}
      </fieldset>

      {sub && (
        <fieldset className="panel-form-group">
          <h3>Aktuelles Abo</h3>
          <p>
            {formatMoney(sub.price_amount, sub.price_currency)} pro Mitarbeitendem/{intervalLabel} ×{" "}
            {sub.quantity} Mitarbeitende = <strong>
              {formatMoney(sub.price_amount != null ? sub.price_amount * sub.quantity : null, sub.price_currency)}
            </strong>{" "}
            / {intervalLabel}
          </p>
          {sub.current_period_end && (
            <p>
              Nächste Abrechnung: {formatDate(sub.current_period_end)}
              {sub.cancel_at_period_end && " -- wird danach NICHT verlängert (Kündigung aktiv)"}
            </p>
          )}
          {sub.payment_method && (
            <p>
              Zahlungsmittel: {sub.payment_method.brand.toUpperCase()} •••• {sub.payment_method.last4}
            </p>
          )}
          {sub.latest_invoice_status && (
            <p>
              Letzte Rechnung: {INVOICE_STATUS_LABELS[sub.latest_invoice_status] ?? sub.latest_invoice_status}
              {sub.latest_invoice_status === "open" && sub.latest_invoice_amount_due
                ? ` -- offener Betrag: ${formatMoney(sub.latest_invoice_amount_due, sub.price_currency)}`
                : ""}
            </p>
          )}
        </fieldset>
      )}

      <div className="panel-form-group">
        {status.billing_configured && status.subscription_status !== "active" && (
          <button type="button" className="btn-primary" onClick={handleCheckout} disabled={redirecting}>
            Abo abschliessen
          </button>
        )}
        {status.billing_configured && sub && (
          <button type="button" className="btn-ghost" onClick={handlePortal} disabled={redirecting}>
            Abrechnung verwalten
          </button>
        )}
      </div>
    </div>
  );
}
