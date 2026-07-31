from api.main import app


def test_provider_settings_routes_are_registered_on_main_app():
    methods = set()
    for route in app.routes:
        if (
            getattr(route, "path", None) == "/api/v1/admin/provider-settings"
            and hasattr(route, "methods")
        ):
            methods.update(route.methods or ())

    assert methods == {"GET", "PUT"}
