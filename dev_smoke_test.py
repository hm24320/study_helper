from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from main import DB_PATH, app


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    with TestClient(app) as client:
        future_due = iso(datetime.now(tz=timezone.utc) + timedelta(hours=2))
        resp = client.post(
            "/v1/tasks",
            json={
                "title": "Physics practice",
                "verify_method": "Upload photo",
                "due_at_iso": future_due,
            },
        )
        resp.raise_for_status()
        created = resp.json()

        past_due = iso(datetime.now(tz=timezone.utc) - timedelta(hours=1))
        resp = client.post(
            "/v1/tasks",
            json={
                "title": "Chemistry homework",
                "verify_method": "Link to doc",
                "due_at_iso": past_due,
            },
        )
        resp.raise_for_status()
        expired_id = resp.json()["id"]

        resp = client.get("/v1/tasks")
        resp.raise_for_status()
        items = resp.json()["items"]
        assert len(items) == 2
        states = {item["id"]: item["state"] for item in items}
        assert states[expired_id] == "EXPIRED"

        resp = client.post(
            f"/v1/tasks/{created['id']}/verify-attempt",
            json={
                "proof_url": "https://example.com/proof.png",
                "verdict": True,
                "score": 0.95,
                "reasons": "All steps verified",
                "raw_features": {"pages": [1, 2]},
            },
        )
        resp.raise_for_status()
        assert resp.json()["state"] == "APPROVED"


if __name__ == "__main__":
    run()
    print("Smoke test completed successfully.")
