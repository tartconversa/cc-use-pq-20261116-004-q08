import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import DomainError
from .service import Service


class Payload(BaseModel):
    model_config = ConfigDict(extra="allow")


class Ingestion(BaseModel):
    source: str
    batch_id: str
    events: list[dict]


class CandidateRequest(BaseModel):
    candidate_id: str
    plant_id: str
    from_day: str
    to_day: str
    expected_head: int
    expected_revision: int


class PublishRequest(BaseModel):
    expected_head: int
    expected_revision: int


def create_app(database: str | None = None) -> FastAPI:
    service = Service(database or os.environ.get("CARBON_TRACE_DB", "carbon_trace.sqlite3"))
    app = FastAPI(title="Carbon material evidence service", version="1.0.0")
    app.state.service = service

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError):
        return JSONResponse(error.as_dict(), status_code=error.status)

    @app.get("/status")
    def status():
        return service.status()

    @app.post("/plants", status_code=201)
    def add_plant(payload: Payload):
        return service.register_plant(payload.model_dump())

    @app.get("/plants")
    def plants():
        return service.catalog.list_plants()

    @app.post("/lines", status_code=201)
    def add_line(payload: Payload):
        return service.register_line(payload.model_dump())

    @app.get("/plants/{plant_id}/lines")
    def lines(plant_id: str):
        return service.catalog.list_lines(plant_id)

    @app.post("/specifications", status_code=201)
    def add_specification(payload: Payload):
        return service.register_specification(payload.model_dump())

    @app.get("/specifications/{grade}")
    def specifications(grade: str):
        return service.catalog.list_specifications(grade)

    @app.post("/batches")
    def ingest(payload: Ingestion):
        return service.ingest(payload.model_dump())

    @app.get("/batches/{batch_id}")
    def batch(batch_id: str):
        return service.intake.batch(batch_id)

    @app.get("/batches/{batch_id}/rejections")
    def rejections(batch_id: str):
        return service.intake.rejections(batch_id)

    @app.get("/sources/{source}/events/{external_id}")
    def receipt(source: str, external_id: str):
        return service.intake.receipt(source, external_id)

    @app.get("/lots/{lot_id}/quality")
    def lot_quality(lot_id: str):
        return service.quality_of_lot(lot_id)

    @app.get("/plants/{plant_id}/report")
    def report(plant_id: str, from_day: str, to_day: str):
        return service.report(plant_id, from_day, to_day)

    @app.get("/plants/{plant_id}/inventory")
    def inventory(plant_id: str, as_of: str):
        return service.inventory(plant_id, as_of)

    @app.post("/candidates", status_code=201)
    def prepare(payload: CandidateRequest):
        return service.prepare(payload.model_dump())

    @app.get("/candidates/{candidate_id}")
    def candidate(candidate_id: str):
        return service.publication.candidate(candidate_id)

    @app.post("/candidates/{candidate_id}/publish", status_code=201)
    def publish(candidate_id: str, payload: PublishRequest):
        return service.publish(candidate_id, payload.model_dump())

    @app.get("/versions/{version}")
    def version(version: int):
        return service.publication.version(version)

    @app.get("/plants/{plant_id}/versions")
    def versions(plant_id: str):
        return service.publication.versions(plant_id)

    @app.get("/plants/{plant_id}/comparison")
    def comparison(plant_id: str, from_day: str, to_day: str, version: int):
        return service.compare(plant_id, from_day, to_day, version)

    @app.post("/orders", status_code=201)
    def create_order(payload: Payload):
        return service.create_order(payload.model_dump())

    @app.get("/orders/{order_id}")
    def order(order_id: str):
        return service.orders.detail(order_id)

    @app.post("/orders/{order_id}/cancel")
    def cancel_order(order_id: str, payload: Payload):
        return service.cancel_order(order_id, payload.model_dump())

    @app.post("/reservations", status_code=201)
    def reserve(payload: Payload):
        return service.reserve(payload.model_dump())

    @app.post("/reservations/{reservation_id}/release")
    def release(reservation_id: str, payload: Payload):
        return service.release(reservation_id, payload.model_dump())

    @app.get("/plants/{plant_id}/allocation")
    def allocation(plant_id: str, as_of: str):
        return service.orders.allocation(plant_id, as_of)

    @app.put("/sources/{source}/policy")
    def configure_source(source: str, payload: Payload):
        return service.configure_source(source, payload.model_dump())

    @app.get("/sources/{source}/policy")
    def source_policy(source: str):
        return service.governance.get(source)

    @app.post("/incidents", status_code=201)
    def open_incident(payload: Payload):
        return service.open_incident(payload.model_dump())

    @app.post("/incidents/{incident_id}/close")
    def close_incident(incident_id: str, payload: Payload):
        return service.close_incident(incident_id, payload.model_dump())

    @app.get("/plants/{plant_id}/incidents")
    def incidents(plant_id: str, from_day: str, to_day: str):
        return service.incidents.list(plant_id, from_day, to_day)

    @app.get("/plants/{plant_id}/reconciliation")
    def reconcile(plant_id: str, from_day: str, to_day: str):
        return service.reconciliation.plant(plant_id, from_day, to_day)

    @app.get("/sources/{source}/reconciliation")
    def reconcile_source(source: str, from_revision: int = 0):
        return service.reconciliation.source(source, from_revision)

    @app.get("/plants/{plant_id}/forecast")
    def forecast(plant_id: str, start_day: str, days: int = 7):
        return service.forecast.horizon(plant_id, start_day, days)

    @app.get("/lots/{lot_id}/trace")
    def trace_lot(lot_id: str):
        return service.trace.lot(lot_id)

    @app.get("/revisions/{revision}")
    def trace_revision(revision: int):
        return service.trace.revision(revision)

    @app.get("/audit/timeline")
    def audit_timeline(from_revision: int, to_revision: int):
        return service.trace.timeline(from_revision, to_revision)

    @app.get("/audit/sources/{source}/events/{external_id}")
    def audit_source_event(source: str, external_id: str):
        return service.trace.source_event(source, external_id)

    @app.post("/delivery/commitments", status_code=201)
    def commit_delivery(payload: Payload):
        return service.delivery.commit(payload.model_dump())

    @app.post("/delivery/checks", status_code=201)
    def check_delivery(payload: Payload):
        return service.delivery.check(payload.model_dump())

    @app.get("/delivery/shipments/{shipment_id}")
    def shipment_delivery(shipment_id: str):
        return service.delivery.shipment(shipment_id)

    @app.get("/delivery/orders/{order_id}")
    def order_delivery(order_id: str):
        return service.delivery.order(order_id)

    return app


app = create_app()
