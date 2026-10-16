# INTEGRATION — #31 Marketing automation (dual-purpose engine)

Feature branch: `feature/marketing-automation`. Files added (nothing else touched):

- `backend/routers/marketing_automation.py` — shop router (`/tenants/marketing`),
  public unsubscribe router (same prefix, no auth), platform-admin router (`/admin/marketing`).
- `backend/services/marketing_automation.py` — `EmailProvider`/`SmsProvider` ABCs,
  `LogEmailProvider`/`LogSmsProvider` demo defaults, `TODO(LIVE)` `ResendEmailProvider`
  skeleton + `TwilioSmsProvider` (wraps existing `services.twilio.send_sms`, demo-fallback built in),
  HMAC unsubscribe tokens, suppression helpers.
- `backend/tests/test_marketing_automation.py` — 21 tests, all passing.
- `frontend/src/pages/dashboard/Marketing.jsx` — dashboard page (Campaigns / Segments / Sequences tabs).
  NOT wired into nav/routes — coordinator does that.

## 1. server.py — router wiring

Add the import with the other router imports:

```python
from routers.marketing_automation import (
    router as marketing_automation_router,
    public_router as marketing_automation_public_router,
    admin_router as marketing_automation_admin_router,
)
```

Add the includes with the other `api.include_router(...)` lines:

```python
api.include_router(marketing_automation_router)
api.include_router(marketing_automation_public_router)
api.include_router(marketing_automation_admin_router)
```

## 2. Dashboard nav entry (coordinator wires)

- Label EN: `Marketing`
- Label ES: `Marketing`
- Route path: `/dashboard/marketing`
- Component: `frontend/src/pages/dashboard/Marketing.jsx` (default export `Marketing`)

Suggested nav placement: under the "Grow" section next to Automations; icon `Megaphone`
(lucide). The page uses `data-testid="marketing-page"` and per-row testids
(`campaign-row-<id>`, `segment-row-<id>`, `sequence-row-<id>`).

## 3. i18n keys (coordinator merges into locale files — do NOT overwrite)

Add under a top-level `"marketing"` key in `frontend/src/i18n/locales/en.json`:

```json
"marketing": {
  "eyebrow": "Grow",
  "title": "Marketing",
  "sub": "Email & SMS campaigns, smart segments, and nurture drips — for your shop and, via the platform console, for acquiring new shops.",
  "tab_campaigns": "Campaigns",
  "tab_segments": "Segments",
  "tab_sequences": "Sequences",
  "new_campaign": "New campaign",
  "new_segment": "New segment",
  "new_sequence": "New sequence",
  "empty_campaigns": "No campaigns yet — create one to blast a segment or start a drip.",
  "empty_segments": "No segments yet — e.g. “AC units 10+ years old” or “no visit in 18 months”.",
  "empty_sequences": "No sequences yet — build a day 0 / 3 / 7 nurture drip.",
  "rules": "rules",
  "steps": "steps",
  "stats": "Stats",
  "test_send": "Test",
  "send": "Send",
  "preview": "Preview",
  "status_draft": "draft",
  "status_scheduled": "scheduled",
  "status_sent": "sent",
  "confirm_delete": "Delete?",
  "confirm_send": "Send this campaign now? This cannot be undone.",
  "deleted": "Deleted",
  "created": "Created",
  "sent_toast": "Sent to {{n}} contacts",
  "test_sent_toast": "Test message sent",
  "test_send_prompt": "Send the test to which email / phone?",
  "due_toast": "{{n}} drip steps sent",
  "preview_result": "{{n}} contacts match",
  "close": "Close",
  "cancel": "Cancel",
  "save": "Save",
  "process_due": "Process due drip steps",
  "day": "day",
  "stats_title": "Campaign stats",
  "stat_sent": "Delivered",
  "stat_failed": "Failed",
  "stat_suppressed": "Unsubscribed",
  "stat_channels": "By channel",
  "field_name": "Name",
  "field_subject": "Subject",
  "field_body_hint": "Message body — use {{first_name}}, {{name}}, {{email}}, {{phone}}, {{company}}, {{unsubscribe_url}}",
  "channel_email": "Email",
  "channel_sms": "SMS",
  "pick_segment": "Pick a segment…",
  "no_sequence": "No sequence (one-shot blast)",
  "match_all": "Match ALL rules",
  "match_any": "Match ANY rule",
  "rule_field": "field (e.g. equipment_age_years)",
  "rule_value": "value",
  "add_rule": "Add rule",
  "add_step": "Add step",
  "step_n": "Step {{n}}",
  "delay_days": "delay (days)"
}
```

Add under a top-level `"marketing"` key in `frontend/src/i18n/locales/es.json`:

```json
"marketing": {
  "eyebrow": "Crecer",
  "title": "Marketing",
  "sub": "Campañas de email y SMS, segmentos inteligentes y secuencias de nutrición — para tu negocio y, desde la consola de plataforma, para captar nuevos negocios.",
  "tab_campaigns": "Campañas",
  "tab_segments": "Segmentos",
  "tab_sequences": "Secuencias",
  "new_campaign": "Nueva campaña",
  "new_segment": "Nuevo segmento",
  "new_sequence": "Nueva secuencia",
  "empty_campaigns": "Aún no hay campañas — crea una para enviar a un segmento o iniciar un goteo.",
  "empty_segments": "Aún no hay segmentos — p. ej. «aires de 10+ años» o «sin visita en 18 meses».",
  "empty_sequences": "Aún no hay secuencias — crea un goteo de nutrición día 0 / 3 / 7.",
  "rules": "reglas",
  "steps": "pasos",
  "stats": "Estadísticas",
  "test_send": "Probar",
  "send": "Enviar",
  "preview": "Vista previa",
  "status_draft": "borrador",
  "status_scheduled": "programada",
  "status_sent": "enviada",
  "confirm_delete": "¿Eliminar?",
  "confirm_send": "¿Enviar esta campaña ahora? No se puede deshacer.",
  "deleted": "Eliminado",
  "created": "Creado",
  "sent_toast": "Enviado a {{n}} contactos",
  "test_sent_toast": "Mensaje de prueba enviado",
  "test_send_prompt": "¿Enviar la prueba a qué email / teléfono?",
  "due_toast": "{{n}} pasos de goteo enviados",
  "preview_result": "{{n}} contactos coinciden",
  "close": "Cerrar",
  "cancel": "Cancelar",
  "save": "Guardar",
  "process_due": "Procesar pasos de goteo vencidos",
  "day": "día",
  "stats_title": "Estadísticas de la campaña",
  "stat_sent": "Entregados",
  "stat_failed": "Fallidos",
  "stat_suppressed": "Dados de baja",
  "stat_channels": "Por canal",
  "field_name": "Nombre",
  "field_subject": "Asunto",
  "field_body_hint": "Cuerpo del mensaje — usa {{first_name}}, {{name}}, {{email}}, {{phone}}, {{company}}, {{unsubscribe_url}}",
  "channel_email": "Email",
  "channel_sms": "SMS",
  "pick_segment": "Elige un segmento…",
  "no_sequence": "Sin secuencia (envío único)",
  "match_all": "Cumplir TODAS las reglas",
  "match_any": "Cumplir ALGUNA regla",
  "rule_field": "campo (p. ej. equipment_age_years)",
  "rule_value": "valor",
  "add_rule": "Añadir regla",
  "add_step": "Añadir paso",
  "step_n": "Paso {{n}}",
  "delay_days": "retraso (días)"
}
```

## 4. `.env.example` additions

```bash
# --- Marketing automation (#31) — all optional; defaults are log-only (zero network) ---
MARKETING_EMAIL_PROVIDER=log      # log | resend   (resend = TODO(LIVE), needs keys below)
MARKETING_SMS_PROVIDER=log        # log | twilio   (twilio reuses services.twilio; demo-fallback when creds missing)
RESEND_API_KEY=                   # TODO(LIVE) real ESP
RESEND_FROM_EMAIL=                # TODO(LIVE) e.g. hello@yourshop.com
PUBLIC_BACKEND_URL=               # used to stamp absolute unsubscribe links, e.g. https://api.yourdomain.com
```

## 5. New pip dependencies

None. The feature uses only the standard library plus packages already in
`backend/requirements.txt` (fastapi, pydantic). The live ESP path is a
`TODO(LIVE)` skeleton (no new import at runtime); Twilio reuse goes through the
existing `services.twilio` adapter.

## API surface (for reference)

Shop (`/tenants/marketing`, tenant-scoped; writes need owner/admin):
segments CRUD + `POST /segments/{id}/preview`; campaigns CRUD +
`POST /campaigns/{id}/test-send`, `POST /campaigns/{id}/send`,
`GET /campaigns/{id}/stats`; sequences CRUD +
`POST /sequences/{id}/enroll`, `GET /sequences/{id}/schedule-preview`,
`POST /sequences/process-due`; `GET /unsubscribe/{token}` (public).

Platform (`/admin/marketing`, `require_platform_admin`): same campaigns /
segments / sequences surface in `context="platform"` against the
`platform_prospects` audience, plus `GET /suppressions`.

## Notes / follow-ups for the coordinator

- Drip delivery is pull-based: `POST /sequences/process-due` sends due steps.
  Wire it to a cron (e.g. every 15 min) for real drip automation.
- Platform audience reads the `platform_prospects` collection, which nothing
  populates yet — a follow-up should pipe trial signups / waitlist entries into it.
- Segment rule evaluation is Python-side (correct on any Mongo); for very large
  audiences push simple rules into a Mongo query (marked `TODO(LIVE)` in code).
- No frontend route/nav wiring was done per the contract.
