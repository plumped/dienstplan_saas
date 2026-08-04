"""
E-Mail-Benachrichtigungen (MVP-Fahrplan Block 2.4): "mind. E-Mail" bei neuer
Absenz-/Tauschanfrage sowie bei deren Genehmigung/Ablehnung. Bewusst
best-effort -- ein fehlgeschlagener Mailversand darf die eigentliche
Aktion (Anlegen/Genehmigen/Ablehnen) nicht verhindern, siehe `_send()`.

Empfänger werden über `Employee.user.email` (Self-Service-Account) bzw.
`Membership` für Admin/Planer aufgelöst -- ohne verknüpften Account bzw.
ohne hinterlegte E-Mail-Adresse wird stillschweigend nichts verschickt
(kein Fehler), weil das in der Praxis (Mitarbeitende ohne eigenen Login)
ein normaler Zustand ist, kein Sonderfall.

"Veröffentlichung eines neuen Monatsplans" (ebenfalls in Block 2.4
genannt) ist hier bewusst NICHT enthalten -- es gibt aktuell keinen
"Veröffentlichen"-Workflow für den Monatsplan (jede Zuweisung ist sofort
für alle sichtbar), das wäre eine eigene, grössere Funktion.
"""

from django.core.mail import send_mail

from core.models import Membership


def _manager_emails(tenant):
    return list(
        Membership.objects.filter(tenant=tenant, role__in=(Membership.Role.ADMIN, Membership.Role.PLANNER))
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )


def _employee_email(employee):
    return employee.user.email if employee.user_id and employee.user.email else ""


def _send(subject, message, recipients):
    recipients = [r for r in recipients if r]
    if not recipients:
        return
    send_mail(subject, message, None, recipients, fail_silently=True)


def notify_new_absence_request(absence):
    """Mitarbeiter hat eine Absenz beantragt (PENDING) -- Admin/Planer informieren."""
    _send(
        f"Neue Abwesenheitsanfrage: {absence.employee}",
        f"{absence.employee} hat eine Abwesenheit beantragt ({absence.get_type_display()}, "
        f"{absence.start_date} bis {absence.end_date}) und wartet auf Genehmigung.",
        _manager_emails(absence.tenant),
    )


def notify_absence_decision(absence):
    """Absenz genehmigt/abgelehnt -- die antragstellende Person informieren."""
    decision = "genehmigt" if absence.status == absence.Status.APPROVED else "abgelehnt"
    _send(
        f"Deine Abwesenheitsanfrage wurde {decision}",
        f"Deine Abwesenheit ({absence.get_type_display()}, {absence.start_date} bis "
        f"{absence.end_date}) wurde {decision}.",
        [_employee_email(absence.employee)],
    )


def notify_new_trade_request(trade_request):
    """Neue Tauschanfrage -- die Zielperson informieren."""
    requester = trade_request.requester_assignment.employee
    _send(
        f"Neue Diensttausch-Anfrage von {requester}",
        f"{requester} möchte dir die Schicht am {trade_request.requester_assignment.date} "
        "übergeben bzw. dagegen tauschen.",
        [_employee_email(trade_request.target_employee)],
    )


def notify_trade_accepted_by_employee(trade_request):
    """Zielperson hat zugestimmt -- Admin/Planer zur Freigabe informieren."""
    requester = trade_request.requester_assignment.employee
    _send(
        f"Diensttausch bereit zur Freigabe: {requester} → {trade_request.target_employee}",
        f"{trade_request.target_employee} hat der Tauschanfrage von {requester} zugestimmt -- "
        "wartet auf Freigabe.",
        _manager_emails(trade_request.tenant),
    )


def notify_trade_decision(trade_request):
    """Admin/Planer hat freigegeben/abgelehnt -- beide Beteiligten informieren."""
    decision = "freigegeben" if trade_request.status == trade_request.Status.ACCEPTED else "abgelehnt"
    requester = trade_request.requester_assignment.employee
    _send(
        f"Diensttausch {decision}",
        f"Der Diensttausch zwischen {requester} und {trade_request.target_employee} wurde {decision}.",
        [_employee_email(requester), _employee_email(trade_request.target_employee)],
    )


def notify_trade_declined(trade_request):
    """Zielperson hat selbst abgelehnt -- die anbietende Person informieren."""
    requester = trade_request.requester_assignment.employee
    _send(
        "Diensttausch-Anfrage abgelehnt",
        f"{trade_request.target_employee} hat deine Tauschanfrage abgelehnt.",
        [_employee_email(requester)],
    )
