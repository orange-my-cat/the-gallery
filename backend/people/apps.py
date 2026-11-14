from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "people"

    def ready(self) -> None:
        # The operation engine finds step types by name; register ours in
        # every process, web and worker alike.
        from people.services import steps  # noqa: F401
