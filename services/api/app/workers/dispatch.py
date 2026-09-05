from uuid import UUID

from app.workers.tasks import poll_career_source, run_company_discovery


class CeleryDiscoveryDispatcher:
    def enqueue(self, run_id: UUID) -> None:
        run_company_discovery.delay(str(run_id))


class CeleryPollDispatcher:
    def enqueue(self, source_id: UUID, lease_owner: str) -> None:
        poll_career_source.delay(str(source_id), lease_owner)
