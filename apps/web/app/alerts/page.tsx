import { AppShell } from "@/components/layout/app-shell";
import { AlertHistory } from "@/components/notifications/alert-history";
import { EmailDeliveryPanel } from "@/components/notifications/email-delivery-panel";
import { ReviewRemindersPanel } from "@/components/notifications/review-reminders-panel";

export default function AlertsPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Notification history</p>
        <h1 className="page-title">Alerts about matching openings.</h1>
        <p className="page-intro">
          Every alert records the jobs that matched your deterministic
          preferences, why each one matched, and when it was first observed.
          Email delivery is optional and configured in Settings. “Accepted by
          Resend” confirms provider acceptance, while your inbox confirms final
          delivery.
        </p>
        <ReviewRemindersPanel />
        <EmailDeliveryPanel />
        <AlertHistory />
      </section>
    </AppShell>
  );
}
