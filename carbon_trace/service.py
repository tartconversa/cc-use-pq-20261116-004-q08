from .analytics import Analytics
from .catalog import Catalog
from .intake import Intake
from .governance import Governance
from .incidents import Incidents
from .orders import Orders
from .forecast import Forecast
from .delivery import Delivery
from .reconciliation import Reconciliation
from .trace import Trace
from .publication import Publication
from .quality import Quality
from .store import Store
from .values import identifier, mapping


class Service:
    def __init__(self, path: str):
        self.store = Store(path)
        self.catalog = Catalog(self.store)
        self.intake = Intake(self.store)
        self.governance = Governance(self.store)
        self.incidents = Incidents(self.store)
        self.orders = Orders(self.store)
        self.forecast = Forecast(self.store)
        self.delivery = Delivery(self.store)
        self.reconciliation = Reconciliation(self.store)
        self.trace = Trace(self.store)
        self.analytics = Analytics(self.store)
        self.publication = Publication(self.store)
        self.quality = Quality()

    def register_plant(self, payload: dict) -> dict:
        return self.catalog.add_plant(mapping(payload))

    def register_line(self, payload: dict) -> dict:
        return self.catalog.add_line(mapping(payload))

    def register_specification(self, payload: dict) -> dict:
        return self.catalog.add_specification(mapping(payload))

    def ingest(self, payload: dict) -> dict:
        payload = mapping(payload)
        return self.intake.submit(payload.get("source"), payload.get("batch_id"), payload.get("events"))

    def report(self, plant_id: str, from_day: str, to_day: str) -> dict:
        return self.analytics.report(plant_id, from_day, to_day)

    def inventory(self, plant_id: str, as_of: str) -> dict:
        return self.analytics.inventory(plant_id, as_of)

    def quality_of_lot(self, lot_id: str) -> dict:
        return self.quality.lot(self.store, identifier(lot_id, "lot_id"))

    def prepare(self, payload: dict) -> dict:
        payload = mapping(payload)
        return self.publication.prepare(payload.get("candidate_id"), payload.get("plant_id"),
                                        payload.get("from_day"), payload.get("to_day"),
                                        payload.get("expected_head"), payload.get("expected_revision"))

    def publish(self, candidate_id: str, payload: dict) -> dict:
        payload = mapping(payload)
        return self.publication.publish(candidate_id, payload.get("expected_head"),
                                        payload.get("expected_revision"))

    def compare(self, plant_id: str, from_day: str, to_day: str, version: int) -> dict:
        return self.analytics.compare(plant_id, from_day, to_day, version)

    def status(self) -> dict:
        return self.store.status()

    def create_order(self, payload: dict) -> dict:
        return self.orders.create(mapping(payload))

    def reserve(self, payload: dict) -> dict:
        return self.orders.reserve(mapping(payload))

    def cancel_order(self, order_id: str, payload: dict) -> dict:
        return self.orders.cancel(order_id, mapping(payload).get("expected_revision"))

    def release(self, reservation_id: str, payload: dict) -> dict:
        return self.orders.release(reservation_id, mapping(payload).get("expected_revision"))

    def configure_source(self, source: str, payload: dict) -> dict:
        payload = mapping(payload)
        return self.governance.configure(source, payload.get("enabled"), payload.get("kinds"),
                                         payload.get("max_batch_size"), payload.get("expected_revision"))

    def open_incident(self, payload: dict) -> dict:
        return self.incidents.open(mapping(payload))

    def close_incident(self, incident_id: str, payload: dict) -> dict:
        payload = mapping(payload)
        return self.incidents.close(incident_id, payload.get("ended_at"),
                                    payload.get("expected_revision"))
