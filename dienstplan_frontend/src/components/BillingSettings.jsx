import { useEffect, useState } from "react";
import { api } from "../api.js";
import { IconCreditCard } from "../icons.jsx";

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
// Screen zusätzlich die LIVE-Stripe-Subscription-Details (status.subscription,
// siehe core.billing.get_subscription_details): nächste Abrechnung,
// Preis × Menge, Zahlungsmittel, letzte Rechnung. status.subscription ist
// null, solange noch kein Abo abgeschlossen wurde.
//
// Nutzer-Feedback (2026-08, Nachtrag): "das sieht ja traurig aus" -- Stat-
// Kacheln + Status-Badge (wiederverwendet die bestehende .status-badge-
// Farbsprache aus AbsenceOverview/TradeRequestOverview) statt einer Liste
// von <p>-Zeilen.
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
  const totalPerInterval =
    sub && sub.price_amount != null ? formatMoney(sub.price_amount * sub.quantity, sub.price_currency) : null;

  return (
    <div className="panel-form">
      <div className="billing-header">
        <div className="settings-form-header">
          <span className="settings-form-icon">
            <IconCreditCard />
          </span>
          <div>
            <h2>Abrechnung</h2>
            <p className="settings-form-subtitle">Abo-Status, Testphase und Zahlungsmittel.</p>
          </div>
        </div>
        <span className={`status-badge status-badge--${status.subscription_status}`}>
          {STATUS_LABELS[status.subscription_status] ?? status.subscription_status}
        </span>
      </div>
      <p className="panel-hint">
        Ein Abo, Preis pro aktivem Mitarbeitenden/Monat -- Zahlungsabwicklung läuft komplett über
        Stripe, keine Kartendaten werden von dieser App gespeichert.
      </p>

      {!status.billing_configured && (
        <p className="panel-hint">
          Abrechnung ist für diesen Betrieb noch nicht eingerichtet -- bitte an den Support wenden.
        </p>
      )}

      {!status.has_active_access && (
        <div className="billing-warning">
          Kein aktiver Zugriff mehr -- neue Einträge (z. B. neue Dienste, Mitarbeitende) sind
          gesperrt, bis ein Abo abgeschlossen ist. Bestehende Daten bleiben einsehbar.
        </div>
      )}

      <div className="stat-tile-grid">
        <div className="stat-tile">
          <p className="stat-tile-label">Aktive Mitarbeitende</p>
          <p className="stat-tile-value">
            {status.active_employee_count}
            {status.subscription_status === "trialing" && <small> / {status.trial_employee_limit}</small>}
          </p>
        </div>

        {sub && (
          <div className="stat-tile">
            <p className="stat-tile-label">Preis pro Mitarbeitendem</p>
            <p className="stat-tile-value">
              {formatMoney(sub.price_amount, sub.price_currency)}
              <small> / {intervalLabel}</small>
            </p>
          </div>
        )}

        {sub && totalPerInterval && (
          <div className="stat-tile">
            <p className="stat-tile-label">Gesamt pro {intervalLabel}</p>
            <p className="stat-tile-value">
              {totalPerInterval}
              <small> × {sub.quantity}</small>
            </p>
          </div>
        )}

        {sub && sub.current_period_end && (
          <div className="stat-tile">
            <p className="stat-tile-label">Nächste Abrechnung</p>
            <p className="stat-tile-value">{formatDate(sub.current_period_end)}</p>
          </div>
        )}

        {status.subscription_status === "trialing" && (
          <div className="stat-tile">
            <p className="stat-tile-label">Testphase</p>
            <p className="stat-tile-value">
              {trialDaysLeft === null
                ? "unbefristet"
                : trialDaysLeft > 0
                  ? `noch ${trialDaysLeft} Tag${trialDaysLeft === 1 ? "" : "e"}`
                  : "abgelaufen"}
            </p>
          </div>
        )}
      </div>

      {sub && sub.cancel_at_period_end && (
        <div className="billing-warning">
          Das Abo wird zum {formatDate(sub.current_period_end)} NICHT verlängert (Kündigung aktiv).
        </div>
      )}

      {sub && (sub.payment_method || sub.latest_invoice_status) && (
        <div className="billing-details">
          {sub.payment_method && (
            <div className="billing-detail-row">
              <span className="billing-detail-label">Zahlungsmittel</span>
              <span className="billing-detail-value">
                {sub.payment_method.brand.toUpperCase()} •••• {sub.payment_method.last4}
              </span>
            </div>
          )}
          {sub.latest_invoice_status && (
            <div className="billing-detail-row">
              <span className="billing-detail-label">Letzte Rechnung</span>
              <span className="billing-detail-value">
                {INVOICE_STATUS_LABELS[sub.latest_invoice_status] ?? sub.latest_invoice_status}
                {sub.latest_invoice_status === "open" && sub.latest_invoice_amount_due
                  ? ` (${formatMoney(sub.latest_invoice_amount_due, sub.price_currency)} offen)`
                  : ""}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="billing-actions">
        {status.billing_configured && !sub && (
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
