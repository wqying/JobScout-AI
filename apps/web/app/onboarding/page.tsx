import { AppShell } from "@/components/layout/app-shell";
import { ProfileSettingsForm } from "@/components/forms/profile-settings-form";

export default function OnboardingPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Milestone 2 · Local onboarding</p>
        <h1 className="page-title">Set up your search preferences.</h1>
        <p className="page-intro">
          Create the single profile for this installation. No account or cloud
          synchronization is involved.
        </p>
        <ProfileSettingsForm mode="onboarding" />
      </section>
    </AppShell>
  );
}
