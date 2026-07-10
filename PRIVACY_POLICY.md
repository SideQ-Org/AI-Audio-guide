# Privacy Policy — AI Audio Guide

> **DRAFT — not legal advice.** This is an engineering-accurate skeleton describing what the
> app actually collects and where it flows, so your lawyer can turn it into a binding policy.
> Fill every `[BRACKET]`. Required before App Store / Play Store submission and for GDPR.

**Controller:** [COMPANY / SOLE TRADER NAME], [ADDRESS]. **Contact:** [PRIVACY EMAIL].
**Last updated:** [DATE].

---

## 1. Summary

AI Audio Guide narrates the places around you as you walk. To do that it needs your
**location** and, if you ask a question, your **voice**. You can use it **as a guest** (no
account, nothing kept after your session) or **sign in** to save your walk history. This
policy explains what we process, why, and who it's shared with.

## 2. What we process and why

| Data | Why | Kept |
|---|---|---|
| **Location** (GPS coordinates, heading, pace) | Find and narrate nearby places in real time | Guest: only in-memory during the session. Signed-in: the narrated places + walk route are saved as your history. |
| **Voice audio** (only when you press to ask) | Transcribed to text so the guide can answer | Transcribed on our server, then discarded; not stored. |
| **Question text** (the transcript / typed question) | Sent to the language model to generate an answer | Not stored beyond the session (guest) / not stored as PII. |
| **Account info** (email, and name/avatar if you use Google/Apple sign-in) | Create and secure your account | Until you delete your account. |
| **Walk history** (routes, narrated places, timestamps, language) | Show your past walks | Until you delete the walk or your account. |
| **Diagnostics** (aggregate counts, error logs, cost metrics) | Keep the service running and affordable | Aggregate, not tied to your identity. |

We do **not** sell your data or use it for advertising profiling. [Confirm if ads are added
later — this must change.]

## 3. Who we share it with (processors / sub-processors)

To narrate, the app sends data to third parties. Your **location** and **questions** leave
your device:

- **[Map/Auth/DB — Supabase]** — authentication and the database that stores your account and
  walk history. [Region: e.g. EU].
- **[LLM provider — e.g. OpenRouter / DeepSeek / Anthropic]** — receives the **surrounding
  context (approximate location, nearby place names) and your questions** to generate the
  narration and answers.
- **OpenStreetMap / Overpass** — receives your **approximate location** to look up nearby
  places.
- **Wikipedia / web search** — receives **place names** (not you) to fetch facts.
- **[Map tile provider — e.g. MapTiler / Mapbox]** — serves the map imagery (sees tile requests / IP).
- **[Google / Apple]** — only if you choose their sign-in.

Voice audio is transcribed **on our own server** (not sent to a third-party speech service);
speech synthesis (the voice you hear) runs **on your device**.

## 4. Legal basis (GDPR)

- **Consent** — location and microphone access (you grant OS permission; you can revoke it).
- **Contract** — providing the account and history features you signed up for.
- **Legitimate interest** — keeping the service secure and operational (aggregate diagnostics).

## 5. Your rights

You can, at any time:
- **Access / export** your walks — in-app history.
- **Delete** a walk (in-app) or your **entire account and all data** (Settings → Delete
  account, which erases your profile, walks, and narrated-place records).
- **Use as a guest** — no account, no server-side history.
- **Revoke** location / microphone permission in your device settings (the guide then stops).
- Contact [PRIVACY EMAIL] to exercise any GDPR right (access, rectification, erasure,
  portability, objection) or lodge a complaint with your data protection authority.

## 6. Retention

Guest sessions are held only in memory and evicted shortly after you stop. Signed-in walk
history is kept until you delete it or your account. [State a maximum retention window if you
adopt one, e.g. "inactive accounts deleted after 24 months".]

## 7. Security

Traffic is encrypted (TLS). Access to the database is restricted; users can only read their
own history (row-level security). [Describe key management / access controls per your setup.]

## 8. Children

Not directed at children under [16 / the age in your jurisdiction]. We don't knowingly
collect their data.

## 9. International transfers

Data may be processed in [REGIONS] by the processors in §3. [Add the transfer mechanism, e.g.
SCCs, if data leaves the EEA.]

## 10. Changes

We'll update this policy as the app changes and note the date above. Material changes will be
surfaced in-app.

**Questions:** [PRIVACY EMAIL].
