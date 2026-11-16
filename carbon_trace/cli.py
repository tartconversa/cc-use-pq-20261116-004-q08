import argparse
import json
import sys
import tempfile
from pathlib import Path

from .errors import DomainError
from .service import Service


def demo(path: str | None = None) -> dict:
    fixture = json.loads(Path(path or "fixtures/demo.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        service = Service(str(Path(directory) / "demo.sqlite3"))
        for plant in fixture["plants"]:
            service.register_plant(plant)
        for line in fixture["lines"]:
            service.register_line(line)
        for spec in fixture["specifications"]:
            service.register_specification(spec)
        batches = [service.ingest(batch) for batch in fixture["batches"]]
        plant_id = fixture["query"]["plant_id"]
        first = fixture["query"]["from_day"]
        last = fixture["query"]["to_day"]
        report = service.report(plant_id, first, last)
        revision = service.status()["revision"]
        prepared = service.prepare({"candidate_id": "demo-v1", "plant_id": plant_id,
                                    "from_day": first, "to_day": last,
                                    "expected_head": 0, "expected_revision": revision})
        publication = service.publish("demo-v1", {"expected_head": 0,
                                                   "expected_revision": revision})
        frozen = service.publication.version(publication["version"])
        if frozen["rows"] != report["rows"] or frozen["rows_digest"] != prepared["rows_digest"]:
            raise RuntimeError("published report differs from prepared report")
        return {"accepted": sum(len(batch["accepted"]) for batch in batches),
                "rejected": sum(len(batch["rejected"]) for batch in batches),
                "report": report, "publication": publication,
                "reconciliation": service.reconciliation.plant(plant_id, first, last)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="carbon-trace")
    parser.add_argument("command", choices=["demo", "status"])
    parser.add_argument("--fixture", default="fixtures/demo.json")
    parser.add_argument("--database", default="carbon_trace.sqlite3")
    args = parser.parse_args(argv)
    try:
        result = demo(args.fixture) if args.command == "demo" else Service(args.database).status()
    except (DomainError, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
