import { ProfileSettingsForm } from "@/components/forms/profile-settings-form";
import { AppShell } from "@/components/layout/app-shell";

export default function SettingsPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Local preferences</p>
        <h1 className="page-title">Settings</h1>
        <p className="page-intro">
          Update the profile and deterministic job filters stored on this
          machine.
        </p>
        <ProfileSettingsForm mode="settings" />
      </section>
    </AppShell>
  );
}
