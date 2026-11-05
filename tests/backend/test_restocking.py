"""
Tests for the Restocking API endpoints.

Covered:
- GET  /api/restocking/lead-times  (per-warehouse average lead time)
- GET  /api/restocking/orders      (submitted restock orders)
- POST /api/restocking/orders      (submit a restock order)
- GET  /api/demand                 (forecasts now include unit_cost)

Note: restock orders are stored in an in-memory list, so tests in this file
share state by design (a submitted order is visible to later tests).
"""
from datetime import datetime


class TestRestockingLeadTimes:
    """Test suite for the restocking lead-time endpoint."""

    def test_get_lead_times(self, client):
        """Test that lead times are returned for every warehouse."""
        response = client.get("/api/restocking/lead-times")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert len(data) > 0

        # All three warehouses should be present
        for warehouse in ["San Francisco", "London", "Tokyo"]:
            assert warehouse in data

    def test_lead_time_values_are_positive_days(self, client):
        """Test that lead times are positive whole numbers of days."""
        response = client.get("/api/restocking/lead-times")
        data = response.json()

        for warehouse, days in data.items():
            assert isinstance(days, int), f"{warehouse} lead time should be an int"
            assert days > 0, f"{warehouse} lead time should be positive"


class TestRestockOrderSubmission:
    """Test suite for submitting and listing restock orders."""

    def test_create_restock_order(self, client):
        """Test submitting a restock order returns the full order."""
        response = client.post(
            "/api/restocking/orders",
            json={
                "items": [
                    {
                        "sku": "WDG-001",
                        "name": "Industrial Widget Type A",
                        "quantity": 150,
                        "unit_cost": 24.99,
                    }
                ],
                "warehouse": "Tokyo",
            },
        )
        assert response.status_code == 201

        order = response.json()

        # Core structure
        assert "id" in order
        assert order["order_number"].startswith("RESTOCK-")
        assert order["warehouse"] == "Tokyo"
        assert order["status"].lower() == "processing"
        assert order["is_restock"] is True

        # Total value calculation (150 x 24.99 = 3748.50)
        assert abs(order["total_value"] - 3748.5) < 0.01

        # Lead time fields
        assert "lead_time_days" in order
        assert order["lead_time_days"] > 0
        assert "T" in order["expected_delivery"]

    def test_lead_time_matches_warehouse_average(self, client):
        """Cross-validate: submitted order lead time equals the warehouse average."""
        lead_times = client.get("/api/restocking/lead-times").json()

        response = client.post(
            "/api/restocking/orders",
            json={
                "items": [
                    {
                        "sku": "GSK-203",
                        "name": "High-Temperature Gasket",
                        "quantity": 100,
                        "unit_cost": 12.75,
                    }
                ],
                "warehouse": "London",
            },
        )
        assert response.status_code == 201

        order = response.json()
        assert order["lead_time_days"] == lead_times["London"]

    def test_expected_delivery_is_order_date_plus_lead_time(self, client):
        """Test expected_delivery equals order_date + lead_time_days."""
        response = client.post(
            "/api/restocking/orders",
            json={
                "items": [
                    {
                        "sku": "FLT-405",
                        "name": "Oil Filter Cartridge",
                        "quantity": 150,
                        "unit_cost": 8.25,
                    }
                ],
                "warehouse": "San Francisco",
            },
        )
        assert response.status_code == 201

        order = response.json()
        order_date = datetime.fromisoformat(order["order_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery"])
        delta_days = (expected_delivery - order_date).days

        # Allow 1-day tolerance for sub-day time component
        assert abs(delta_days - order["lead_time_days"]) <= 1

    def test_create_restock_order_with_empty_items_rejected(self, client):
        """Test that an order with no items is rejected with a 400."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [], "warehouse": "Tokyo"},
        )
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_create_restock_order_missing_fields_rejected(self, client):
        """Test that an invalid payload is rejected by Pydantic (422)."""
        response = client.post("/api/restocking/orders", json={"items": "nope"})
        assert response.status_code == 422

    def test_get_restock_orders_lists_submitted_orders(self, client):
        """Test that submitted orders appear in the list endpoint."""
        # Submit a fresh order first
        response = client.post(
            "/api/restocking/orders",
            json={
                "items": [
                    {
                        "sku": "PSU-501",
                        "name": "5V 10A Switching Power Supply",
                        "quantity": 2,
                        "unit_cost": 18.99,
                    }
                ],
                "warehouse": "San Francisco",
            },
        )
        assert response.status_code == 201
        created = response.json()

        listing = client.get("/api/restocking/orders")
        assert listing.status_code == 200

        data = listing.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # The newly created order should be present
        submitted = [o for o in data if o["id"] == created["id"]]
        assert len(submitted) == 1
        assert submitted[0]["order_number"] == created["order_number"]

    def test_restock_order_item_structure(self, client):
        """Test that restock order items have the expected structure."""
        listing = client.get("/api/restocking/orders")
        data = listing.json()

        for order in data:
            assert "items" in order
            assert isinstance(order["items"], list)
            for item in order["items"]:
                assert "sku" in item
                assert "name" in item
                assert "quantity" in item
                assert "unit_cost" in item
                assert isinstance(item["quantity"], int)
                assert isinstance(item["unit_cost"], (int, float))
                assert item["quantity"] >= 0


class TestDemandUnitCost:
    """Test suite for the unit_cost field added to demand forecasts."""

    def test_demand_forecasts_include_unit_cost(self, client):
        """Test that every demand forecast exposes a positive unit_cost."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        for forecast in data:
            assert "unit_cost" in forecast
            assert isinstance(forecast["unit_cost"], (int, float))
            assert forecast["unit_cost"] > 0
