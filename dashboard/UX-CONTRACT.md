# PI Hardbox UI contract

Sources: owner requests, `docs/README.md`, `/opt/hardbox-vpn/docs/README.md`.
Visual intent and runtime token ownership: `DESIGN.md`.

## Routes

`/` owns server telemetry; title: `PI Hardbox — состояние сервера`.
`/vpn` owns VPN/Wi-Fi monitoring and owner key replacement; title:
`VPN и Wi-Fi — PI Hardbox`. Native links expose aria-current; Tab/Enter, Back,
refresh and direct links use browser navigation. `/manage` owns fan controls,
autostart status and reboot; title: `Управление сервером — PI Hardbox`. Each route scrolls naturally.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | Native forms in dashboard.py | docs/README.md | fan / VPN key / domain exceptions | HTTP + browser |
| Scrollbar | Global CSS in dashboard.py | DESIGN.md | document | browser narrow/wide |
| Modal | Native dialog in dashboard.py | Reboot API contract | reboot confirmation | keyboard + browser |
| Toast | Inline role=status in dashboard.py | This contract | pending / success / error | browser |

## Owner and privacy

The admin system password is verified by PAM for a shared 10-minute HttpOnly
session authorizing fan, VPN and reboot actions. The retired owner code is rejected. Public
VPN readings exclude IP/MAC addresses, individual client names, all passwords,
and VLESS links. Operation results require the authenticated session. The current key is
never read back. Secret fields are masked, permit paste, and have show/hide
buttons. No credentials are stored in browser storage; logout clears private drafts.

## Replacement

Submit once → asynchronous operation → validate and probe a separate candidate
→ atomically apply and restart VPN → verify live egress → synchronize and restart img2txt → verify health → success in place.
Candidate failure leaves live configuration untouched. Apply/probe failure
restores the previous bytes. A durable pending marker recovers interrupted
updates when the controller starts. Wi-Fi settings and firewall stay in force.

Busy state prevents duplicate submissions; IDs make retries idempotent. Only one
update runs at a time. URI clears after acceptance. Uncertain responses trigger
result polling without an automatic second mutation. Offline keeps old readings
with a stale message and disables new actions. Invalid credentials/input preserve
drafts, mark/focus the field and show correction text. Feedback reserves geometry,
uses aria-live and never prints secrets. No native browser dialogs are used.

## Routing editor

One shared session/login disclosure authorizes fan, VPN-key, routing and reboot. Manual
domain lists load only through a protected request after explicit activation.
Empty lists are meaningful: no manual additions. Two labeled native textareas
use the existing form tokens and collapse to one column on phones. Domains
appear only in this owner workflow. Public polling never overwrites drafts.

Editing marks the draft unsaved; save is explicit and disabled before loading.
Reload clearly states when it will replace a dirty draft. Validation preserves
inputs and focuses the invalid field. Revision conflict keeps the draft and
asks for explicit reload. Pending actions disable both forms; uncertain POSTs
poll their request ID instead of resubmitting. Success reloads the saved lists;
failure preserves the draft. Logout aborts pending reads, invalidates late
responses, masks/clears credentials, and clears domain drafts. Credentials,
drafts and revisions never enter browser storage or URLs. Offline disables
mutations and retains readable stale data and drafts.

Daily source updates retain owner exceptions. Their error is displayed beside
the last check, and the previous rules continue working. Counter labels state
direction, reset behavior and inclusion of DNS/probes. Unknown or hidden domains
remain in VPN; the page does not promise automatic detection of all blockages.

## Reboot confirmation and auth recovery

Canonical modal owner: native `<dialog>` in dashboard.py, with existing surface,
ink, raspberry and focus tokens. Native top-layer focus isolation owns inert
background, Tab containment and Escape. Explicit cancel and confirm are labeled
with the concrete action; Cancel receives initial focus, close restores the
trigger. Scope/disruption is stated before submission. Pending and uncertain
reboots disable further dispatch. A normal saved fan/VPN change has no dialog.

Auth password allows paste/password managers and show/hide, clears on success,
and never enters storage/URLs. Auth transitions invalidate stale status replies.
Bad credentials focus the field; rate limiting gives a one-minute recovery hint.
Expired sessions disable mutations while preserving safe in-page drafts, and
the shared login resumes access. Logout clears drafts; failed logout does not
pretend the server-side session was revoked. All tab routes share this owner.
The login disclosure is visible only on VPN and management, per the owner's
explicit instruction. The public dashboard exposes no login control. Shared
form/action CSS owns consistent button height, alignment and mobile wrapping;
utility-action labels reserve space across loading and dirty-draft states.

## Monitoring indicators and projects

Service and project lamps use the shared status-light owner in dashboard.py.
Each has a visible state label; work, HTTP and autostart remain distinct.
Fetching failures gray lamps and state that data is unconfirmed. The project
monitor is cached, bounded and independent from auth; browsing or polling never
starts/stops containers. Both dashboard and management show the same approved
project summary. Empty/loading, partial healthchecks, stopped/attention/unknown,
stale data and reconnect states have explicit copy. The dashboard still exposes
no login control. Browser favicon and link metadata are public static assets.


img2txt monitoring requires both application and proxy containers, application
HTTP, proxy HTTPS reachability, and synchronized primary key for a healthy
state. VPN and key lamps have explicit unavailable/unknown text. Key replacement
reports its consumer synchronization stage before success; consumer failure
rolls back both primary and derived config. An unchanged key is idempotent.

The LLM readiness light comes from a cached, background GET /models in img2txt.
It checks provider authorization and configured model presence without generation
or patient data. Only safe state/timestamp metadata leaves the service. A failed
provider API key is separate from VLESS and does not reject a valid VPN rotation.


## Subscription editor

The VPN route owns the subscription status and owner form. Flat description
lists and the shared status-light show schedule, next/last checks, working/tested
counts and the last selected ordinal with median HTTPS time. No exit addresses,
profile names, source links or secrets appear in public or protected readbacks.

Explicit protected load enables the native checkbox, integer-hours field and
masked write-only HTTPS input. Blank preserves the saved URL; the server only
returns configured/revision. The shared reveal recipe applies. Dirty reload
names draft replacement; public polling never changes fields. Manual check uses
saved settings and is disabled while dirty. Save immediately checks an enabled
subscription; disable saves without network changes. Validation/conflict/expiry
preserve drafts and focus correction; accepted writes clear/mask the secret.
Uncertain writes poll the original ID without resubmission. Late protected reads
are invalidated on logout, which clears/masks URL and resets the draft. All VPN
mutations share the existing busy guard; long candidate checks show progress.
Results distinguish update failure from the still-running previous connection.
No new navigation, modal, browser storage or login control is introduced.
