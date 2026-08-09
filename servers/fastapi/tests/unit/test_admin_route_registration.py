from api.main import app
from api.v1.admin.router import require_settings_admin


def test_provider_settings_routes_are_registered_on_main_app():
    expected = {
        ("/api/v1/admin/provider-settings", "GET"),
        ("/api/v1/admin/provider-settings", "PUT"),
        ("/api/v1/admin/provider-settings/custom-model-available", "POST"),
    }
    registered = {
        (getattr(route, "path", None), method)
        for route in app.routes
        for method in (getattr(route, "methods", set()) or set())
    }

    assert expected <= registered


def test_custom_model_check_keeps_admin_authentication_dependency():
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None)
        == "/api/v1/admin/provider-settings/custom-model-available"
    )

    assert require_settings_admin in {
        dependency.call for dependency in route.dependant.dependencies
    }
