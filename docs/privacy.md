# Privacy and local data boundary

JobScout is a single-owner local application. PostgreSQL data remains on the local machine in a
Docker volume. Source code can be shared without sharing the owner's database or `.env` file.

API keys remain server-side and `.env` is ignored. The frontend receives no OpenAI or Resend key.
Company research must not send resumes, personal immigration records, or owner email addresses.
Logs must contain identifiers and safe error codes—not raw external pages, credentials, or keys.

Alerts follow the same boundary. A stored `notification_outbox` payload holds public job data and
match reasons only; the recipient address is read from `app_settings` at delivery time, so it is
never persisted into an alert record or written to a log. The fake email adapter logs the subject
and the recipient's domain, never the full address. Enabling email alerts sends job titles,
locations, and public apply links to Resend; leaving Resend unconfigured keeps every alert local.
An owner-requested test sends only the saved recipient address and a fixed JobScout diagnostic
message. Delivery records retain safe adapter/status/error metadata and the provider message ID,
but no API key. `Sent` records provider acceptance; webhook-based inbox delivery tracking is not
implemented.

The product is informational and is not legal or immigration advice.
