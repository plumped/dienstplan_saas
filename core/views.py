from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework import viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Membership, Tenant, TenantHolidayOverride
from core.onboarding import seed_demo_tenant, unique_tenant_slug
from core.permissions import IsTenantAdmin
from core.serializers import (
    MembershipCreateSerializer,
    MembershipSerializer,
    SignupSerializer,
    TenantHolidayOverrideSerializer,
    TenantSerializer,
)
from core.tenancy import apply_tenant_scoped_initial, resolve_membership_for_user

User = get_user_model()

_EMPTY_TASK_COUNTS = {"absences": 0, "trades": 0, "time_records": 0}


class TenantScopedAPIMixin:
    """
    Gemeinsame initial()-Logik für core-Views, die auf request.tenant/
    request.membership angewiesen sind (core.permissions), aber -- anders
    als scheduling.views.TenantScopedViewSet -- kein scheduling importieren
    dürfen (core bleibt die "unterste" App, siehe MeView-Docstring). request.
    tenant wird bewusst hier gesetzt, NACH self.perform_authentication()
    (Token-/Session-Login), nicht in einer Middleware, wo request.user bei
    Token-Logins noch nicht aufgelöst wäre (siehe core/tenancy.py). Vorher
    dreimal dupliziert (TenantView, TenantHolidayOverrideViewSet,
    TenantHolidaysView) -- ab hier ein gemeinsamer Mixin.
    """

    def initial(self, request, *args, **kwargs):
        apply_tenant_scoped_initial(self, request, *args, **kwargs)


def _task_counts(membership, employee):
    """
    "Offene Tasks"-Zähler für die Header-Badges (MVP-Fahrplan Block 2.4):
    Admin/Planer sehen, was auf tenant-weite Freigabe wartet; Mitarbeitende
    sehen nur eigene, tatsächlich an sie persönlich adressierte Tasks
    (Tauschanfragen, bei denen sie die Zielperson sind) -- Absenzen/
    Zeiterfassung genehmigen sie ohnehin nicht, dort bleibt der Zähler 0.
    Import von scheduling.models hier aus demselben Grund wie in MeView.get()
    (core bleibt die "unterste" App).

    Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen, bis ich
    die zu bestätigende Erfassung finde" -- die Zähler zählten bisher
    tenant-weit, unabhängig davon, ob ein Planer inzwischen (siehe
    Membership.scoped_nodes) auf einzelne Stationen eingeschränkt ist. Jetzt
    stationsübergreifend über GENAU die Stationen gezählt, die der Planer in
    den neuen "Zu bestätigen"/"Zu genehmigen"/"Offen"-Übersichten auch
    tatsächlich sieht (dieselbe Scoping-Logik wie TimeRecordViewSet/
    AbsenceViewSet/ShiftTradeRequestViewSet, siehe _employee_scoped_node_ids)
    -- sonst würde die Badge-Zahl wieder nicht zu dem passen, was ein Klick
    darauf zeigt.
    """
    from scheduling.models import Absence, ShiftTradeRequest, TimeRecord
    from scheduling.views import _employee_scoped_node_ids

    if membership.role in (Membership.Role.ADMIN, Membership.Role.PLANNER):
        node_ids = _employee_scoped_node_ids(membership, None)
        time_records_qs = TimeRecord.all_objects.filter(
            tenant=membership.tenant, status=TimeRecord.Status.SUBMITTED
        )
        absences_qs = Absence.all_objects.filter(tenant=membership.tenant, status=Absence.Status.PENDING)
        # EMPLOYEE_ACCEPTED, nicht PENDING: das ist der Stand, an dem die
        # Anfrage tatsächlich auf Admin/Planer-Freigabe wartet (siehe
        # ShiftTradeRequest-Docstring) -- ein PENDING-Request wartet in
        # aller Regel zuerst auf die Zielperson.
        trades_qs = ShiftTradeRequest.all_objects.filter(
            tenant=membership.tenant, status=ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED
        )
        if node_ids is not None:
            time_records_qs = time_records_qs.filter(assignment__node_id__in=node_ids)
            absences_qs = absences_qs.filter(employee__nodes__id__in=node_ids).distinct()
            trades_qs = trades_qs.filter(requester_assignment__node_id__in=node_ids)
        return {
            "absences": absences_qs.count(),
            "trades": trades_qs.count(),
            "time_records": time_records_qs.count(),
        }
    if membership.role == Membership.Role.EMPLOYEE and employee:
        return {
            "absences": 0,
            "trades": ShiftTradeRequest.all_objects.filter(
                tenant=membership.tenant,
                target_employee=employee,
                status=ShiftTradeRequest.Status.PENDING,
            ).count(),
            "time_records": 0,
        }
    return dict(_EMPTY_TASK_COUNTS)


class MeView(APIView):
    """
    Rolle + (falls vorhanden) verknüpfte Employee des eingeloggten Users --
    Grundlage für ein rollenbewusstes Frontend (MVP-Fahrplan, Block 2.2):
    Admin/Planer sehen die volle Bearbeitungs-Oberfläche, Mitarbeitende/HR
    eine eingeschränkte Self-Service-/Reporting-Ansicht.

    Import von scheduling.models.Employee bewusst hier (nicht in core/models.py),
    damit core keine Modul-Level-Abhängigkeit zu scheduling bekommt --
    core bleibt die "unterste" App, auf die scheduling aufbaut, nicht umgekehrt.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from scheduling.models import Employee

        membership = resolve_membership_for_user(request.user)
        if not membership:
            return Response(
                {
                    "role": None,
                    "tenant_name": None,
                    "tenant_onboarding_completed": None,
                    "employee": None,
                    "task_counts": dict(_EMPTY_TASK_COUNTS),
                }
            )

        employee = Employee.all_objects.filter(tenant=membership.tenant, user=request.user).first()
        return Response(
            {
                "role": membership.role,
                "tenant_name": membership.tenant.name,
                # Nutzer-Feedback (2026-08, README Block 3): Self-Signup landet direkt in
                # OnboardingWizard.jsx, solange dieses Flag False ist -- siehe
                # core.views.SignupView/core.onboarding.seed_demo_tenant.
                "tenant_onboarding_completed": membership.tenant.onboarding_completed,
                "must_change_password": request.user.must_change_password,
                # Nutzer-Feedback (2026-08): "oben Links sollte auch noch der
                # Name stehen, damit man weiss wer gerade eingeloggt ist" --
                # Fallback fürs Frontend, falls kein Employee-Profil verknüpft
                # ist (z. B. "Konten ohne Mitarbeiterprofil"), wo `employee`
                # unten None bleibt.
                "username": request.user.username,
                "employee": (
                    {
                        "id": employee.id,
                        "first_name": employee.first_name,
                        "last_name": employee.last_name,
                    }
                    if employee
                    else None
                ),
                "task_counts": _task_counts(membership, employee),
            }
        )


class ChangePasswordView(APIView):
    """
    Eigenes Passwort ändern -- insbesondere für den erzwungenen Wechsel nach
    admin-seitiger Direktanlage mit Temp-Passwort (User.must_change_password,
    siehe core.serializers.MembershipCreateSerializer und Nutzer-Feedback
    2026-08 zum Verzicht auf E-Mail-Einladung). Bewusst ohne IsTenantAdmin/
    Tenant-Bezug -- jeder eingeloggte Account darf nur sein EIGENES Passwort
    ändern, unabhängig von Rolle oder Mitgliedschaft.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get("current_password") or ""
        new_password = request.data.get("new_password") or ""
        if not request.user.check_password(current_password):
            raise ValidationError({"current_password": ["Aktuelles Passwort ist falsch."]})
        try:
            validate_password(new_password, user=request.user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": exc.messages})
        request.user.set_password(new_password)
        request.user.must_change_password = False
        request.user.save(update_fields=["password", "must_change_password"])
        return Response({"detail": "Passwort geändert."})


class SignupView(APIView):
    """
    Self-Signup (README Block 3): erzeugt Tenant + User + Admin-Membership +
    Demo-Daten in einem Zug und loggt sofort ein. Nutzer-Feedback (2026-08):
    Direkt-Signup ohne Magic-Link/E-Mail-Versand, konsistent mit der
    Grundsatzentscheidung in core.serializers.MembershipCreateSerializer --
    hier setzt die anlegende Person direkt ihr eigenes Passwort statt ein
    Temp-Passwort zu erhalten.

    Bewusst KEIN TenantScopedAPIMixin (es existiert noch kein Tenant/keine
    Membership für diesen Request) und AllowAny -- neben obtain_auth_token
    der einzige bewusst unauthentifizierte Schreib-Endpoint der API.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        for _attempt in range(2):
            slug = unique_tenant_slug(data["tenant_name"])
            try:
                with transaction.atomic():
                    tenant = Tenant.objects.create(
                        name=data["tenant_name"],
                        slug=slug,
                        canton=data.get("canton", ""),
                        onboarding_completed=False,
                        subscription_status=Tenant.SubscriptionStatus.TRIALING,
                        trial_ends_at=timezone.now() + timedelta(days=settings.TRIAL_PERIOD_DAYS),
                    )
                    user = User.objects.create_user(
                        username=data["username"],
                        password=data["password"],
                        first_name=data.get("first_name", ""),
                        last_name=data.get("last_name", ""),
                        email=data.get("email", ""),
                        must_change_password=False,
                    )
                    Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.ADMIN)
                    seed_demo_tenant(tenant)
                    token, _created = Token.objects.get_or_create(user=user)
                break
            except IntegrityError:
                continue
        else:
            raise ValidationError("Firma konnte nicht angelegt werden, bitte erneut versuchen.")

        return Response(
            {"token": token.key, "tenant_name": tenant.name, "username": user.username},
            status=201,
        )


class TenantView(TenantScopedAPIMixin, APIView):
    """
    Tenant-Konfiguration (MVP-Fahrplan Block 2, Punkt 14): GET liefert die
    numerischen ArG-/Zuschlags-Grenzwerte des eigenen Tenants (Lesen wie
    überall in der App für alle vier Rollen offen), PATCH ändert sie
    (Admin-only, siehe core.permissions.IsTenantAdmin) -- bislang nur im
    Django-Admin editierbar. Single-Object-Endpoint analog zu MeView, kein
    ViewSet mit Liste: es gibt genau einen Tenant pro eingeloggtem Account.
    """

    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        return Response(TenantSerializer(request.tenant).data)

    def patch(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        serializer = TenantSerializer(request.tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class TenantHolidayOverrideViewSet(TenantScopedAPIMixin, viewsets.ModelViewSet):
    """
    Manuelle Feiertags-Ausnahmen zum kantonalen Kalender (Arbeitszeitmodell,
    README Block 2.7 Punkt 7, siehe Tenant.public_holidays()). Selbe
    Berechtigungs-Struktur wie TenantView, da Teil derselben Tenant-
    Konfiguration -- Lesen für alle vier Rollen offen, Schreiben nur Admin
    (IsTenantAdmin).
    """

    serializer_class = TenantHolidayOverrideSerializer
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_queryset(self):
        # Explizit über all_objects + request.tenant statt ContextVar-Manager
        # (dieselbe "explizite Filterung ist die echte Grenze"-Philosophie
        # wie in scheduling.views.TenantScopedViewSet).
        if not self.request.tenant:
            return TenantHolidayOverride.all_objects.none()
        return TenantHolidayOverride.all_objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


class MembershipViewSet(TenantScopedAPIMixin, viewsets.ModelViewSet):
    """
    Nutzer-Feedback (2026-08): "Kann man [Planer] Stationen zuweisen?" --
    Admin-only Verwaltung von Membership.scoped_nodes (siehe
    scheduling.views._employee_scoped_node_ids und MembershipSerializer).

    Nutzer-Feedback (2026-08): "Applikationsmanager wird den Benutzer anlegen
    und nicht per Mail einladen -- was ist am effizientesten und
    intuitivsten?" -- Admin darf hier inzwischen auch neue Mitgliedschaften
    anlegen (POST, siehe MembershipCreateSerializer) und die Rolle
    bestehender ändern (PATCH `role`, siehe MembershipSerializer).

    Nutzer-Feedback (2026-08): "Konten ohne Mitarbeiterprofil sollten
    ebenfalls eine Löschfunktion haben" -- DELETE jetzt erlaubt, aber bewusst
    nur für Konten OHNE Mitarbeiterprofil (siehe perform_destroy): ein Konto
    MIT Mitarbeiterprofil über diesen Weg zu löschen würde User.delete() via
    Employee.user (OneToOneField, on_delete=CASCADE) das Mitarbeiterprofil
    samt dessen Historie mitreissen -- das ist eine andere, folgenreichere
    Operation als das hier gemeinte "seltener Sonderfall ohne Profil"
    (MembershipAccessSettings.jsx-Docstring). Für Mitarbeitende mit Profil
    bleibt Employee.is_active (EmployeeSettings.jsx-Checkbox "Aktiv") die
    vorgesehene Deaktivierung -- die entfernt nur die Sichtbarkeit im
    Planblatt, nicht den Login (Employee.is_active und User.is_active sind
    unabhängige Felder, siehe scheduling.models.Employee).
    """

    http_method_names = ["get", "post", "head", "options", "patch", "delete"]
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_serializer_class(self):
        return MembershipCreateSerializer if self.action == "create" else MembershipSerializer

    def get_queryset(self):
        if not self.request.tenant:
            return Membership.objects.none()
        return Membership.objects.filter(tenant=self.request.tenant).select_related("user").order_by(
            "role", "user__username"
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        new_role = serializer.validated_data.get("role", instance.role)

        # Verteidigungslinie gegen eine strukturell eigentlich unmögliche
        # Kombination (User.save()/Membership.save() verhindern sie für NEUE
        # Datensätze, siehe deren Docstrings) -- ein is_staff/is_superuser-
        # Account mit Membership kann trotzdem vorkommen (z. B. über
        # loaddata/dumpdata-Fixtures, die Model.save() umgehen). Ohne diese
        # Prüfung würde Membership.save() weiter unten eine
        # django.core.exceptions.ValidationError werfen, die DRF NICHT
        # automatisch in eine 400-Antwort übersetzt -- Ergebnis wäre ein
        # nackter 500 statt einer verständlichen Fehlermeldung.
        if instance.user.is_staff or instance.user.is_superuser:
            raise ValidationError(
                {"role": ["Dieser Account hat Django-Admin-Zugriff -- Rolle kann hier nicht geändert werden."]}
            )

        # ADMIN ist in _employee_scoped_node_ids() unbedingt uneingeschränkt
        # (Nutzer-Vorgabe: "Nur Admin darf immer alles sehen") -- scoped_nodes
        # auf einer (neuen oder bestehenden) Admin-Mitgliedschaft zu speichern
        # hätte also nie einen Effekt. Klarer Fehler statt eines stillen No-Ops.
        #
        # Bugfix: DRF wrappt einen einzelnen String-Wert in einem
        # ValidationError-Dict NICHT automatisch in eine Liste (nur der
        # Top-Level-Fall tut das) -- api.js liest Feldfehler aber konsequent
        # als `[0]` (erwartet also ein Array). Ohne die Liste hier kam beim
        # Rendern nur das erste ZEICHEN der Meldung an ("E" statt der ganzen
        # Nachricht).
        if new_role == Membership.Role.ADMIN and serializer.validated_data.get("scoped_nodes"):
            raise ValidationError(
                {"scoped_nodes": ["Admin sieht immer alle Stationen -- keine Einschränkung möglich."]}
            )

        # Schutz vor versehentlichem "Aussperren": ein Tenant ohne Admin
        # könnte sich selbst nicht mehr verwalten (Django-Admin ist bewusst
        # kein Kundenzugriff, siehe User.save()-Docstring).
        if instance.role == Membership.Role.ADMIN and new_role != Membership.Role.ADMIN:
            other_admins_exist = (
                Membership.objects.filter(tenant=instance.tenant, role=Membership.Role.ADMIN)
                .exclude(pk=instance.pk)
                .exists()
            )
            if not other_admins_exist:
                raise ValidationError(
                    {"role": ["Es muss mindestens eine Admin-Mitgliedschaft je Mandant erhalten bleiben."]}
                )

        serializer.save()

    def perform_destroy(self, instance):
        # Nutzer-Feedback (2026-08): "Konten ohne Mitarbeiterprofil sollten
        # ebenfalls eine Löschfunktion haben" -- gelöscht wird der User
        # (nicht nur die Membership), sonst bliebe ein verwaister Login-
        # Account ohne jede Mitgliedschaft übrig. User.delete() reisst über
        # die CASCADE-FK automatisch auch die Membership selbst mit.
        if hasattr(instance.user, "employee_profile"):
            raise ValidationError(
                "Konten mit Mitarbeiterprofil können hier nicht gelöscht werden -- "
                "dafür bei „Mitarbeitende“ die Aktiv-Checkbox verwenden."
            )
        if instance.user.is_staff or instance.user.is_superuser:
            raise ValidationError("Dieser Account hat Django-Admin-Zugriff -- kann hier nicht gelöscht werden.")
        if instance.user_id == self.request.user.id:
            raise ValidationError("Der eigene Account kann hier nicht gelöscht werden.")
        if instance.role == Membership.Role.ADMIN:
            other_admins_exist = (
                Membership.objects.filter(tenant=instance.tenant, role=Membership.Role.ADMIN)
                .exclude(pk=instance.pk)
                .exists()
            )
            if not other_admins_exist:
                raise ValidationError("Es muss mindestens eine Admin-Mitgliedschaft je Mandant erhalten bleiben.")
        instance.user.delete()

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """
        Nutzer-Feedback (2026-08): "Ja mach passwort reset" (README Block 3,
        Punkt 5: "Passwort-Reset bei vergessenem Passwort -- aktuell nicht
        vorhanden [...] müsste das über den Applikationsmanager laufen
        [Konto-Reset = neues Temp-Passwort vergeben], nicht per
        Magic-Link/E-Mail"). Bewusst kein Self-Service-Flow ohne bekanntes
        Passwort (siehe ChangePasswordView-Docstring: "current_password"
        wird dort verlangt) -- ohne E-Mail-Infrastruktur (Grundsatzentscheid,
        siehe README Block 3 Punkt 6) gibt es keinen Kanal für einen
        Magic-Link, also übernimmt der Admin dieselbe Rolle wie bei der
        Erstanlage (setup_access/MembershipCreateSerializer): neues
        Temp-Passwort generieren, `must_change_password` erzwingen, EINMALIG
        im Response zurückgeben.

        Kein zusätzlicher Rollen-Check nötig -- IsTenantAdmin (Viewset-Ebene)
        verlangt für jeden schreibenden Request bereits Admin, anders als bei
        EmployeeViewSet (dort IsTenantManager, Admin+Planer, daher dort
        explizite Admin-only-Checks in setup_access/deactivate/reactivate).
        """
        membership = self.get_object()
        if membership.user.is_staff or membership.user.is_superuser:
            raise ValidationError(
                "Dieser Account hat Django-Admin-Zugriff -- Passwort kann hier nicht zurückgesetzt werden."
            )
        temp_password = get_random_string(12)
        membership.user.set_password(temp_password)
        membership.user.must_change_password = True
        membership.user.save(update_fields=["password", "must_change_password"])
        return Response({"username": membership.user.username, "temporary_password": temp_password})


class TenantHolidaysView(TenantScopedAPIMixin, APIView):
    """
    Aufgelöste Feiertagsdaten (inkl. Name) für ein Kalenderjahr
    (Arbeitszeitmodell, README Block 2.7 Punkt 7) -- Grundlage für die
    Feiertags-Markierung im Planblatt/Jahresplan (PlanGrid.jsx/YearPlan.jsx).
    Anders als /api/tenant/ (dort steht nur der Kanton-Code) liefert dieser
    Endpoint die vom Kanton + TenantHolidayOverride bereits aufgelöste Liste,
    weil die eigentliche Berechnung (holidays-Bibliothek, bewegliche Feste)
    bewusst nur im Backend passiert -- siehe
    Tenant.public_holidays_with_names(). Lesen wie bei /api/tenant/ für alle
    vier Rollen offen, kein eigener Schreibzugriff (die Konfiguration läuft
    über canton/TenantHolidayOverride).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        year_param = request.query_params.get("year")
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                raise ValidationError({"year": "Ungültiges Jahr."})
        else:
            year = timezone.localdate().year
        entries = sorted(request.tenant.public_holidays_with_names(year).items())
        return Response({"year": year, "dates": [{"date": d.isoformat(), "name": n} for d, n in entries]})
